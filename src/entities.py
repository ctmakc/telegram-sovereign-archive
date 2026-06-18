"""Deterministic entity extraction (no LLM).

Pulls high-signal, low-false-positive entities out of message text so the archive
can answer "find the chat where they mentioned this wallet / email / amount"
(PRD §9, §12.10, §16 deal mode). Regex-only and side-effect free — the storage and
search layers live in the DB adapter.

Entity types: email, url, phone, crypto_wallet, amount.
"""

from __future__ import annotations

import re

# Order matters: longer/more-specific patterns first so their spans win when
# overlapping (e.g. a URL containing an '@' shouldn't also register as email).
_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_URL = re.compile(r"https?://[^\s<>()\"']+")
# ETH-style 0x + 40 hex; TRON base58 starting with T, length 34.
_ETH = re.compile(r"\b0x[0-9a-fA-F]{40}\b")
_TRON = re.compile(r"\bT[1-9A-HJ-NP-Za-km-z]{33}\b")
# Amount with a currency symbol or 3-letter code (incl. crypto tickers).
_CURRENCY_CODE = (
    r"USD|EUR|GBP|UAH|PLN|RUB|CHF|CAD|AUD|JPY|CNY|USDT|USDC|BTC|ETH|TON"
)
_AMOUNT = re.compile(
    r"(?:[$€£₴]\s?\d[\d,]*(?:\.\d+)?)"  # $1,500  /  €50
    r"|(?:\b\d[\d,]*(?:\.\d+)?\s?[$€£₴])"  # 50€  /  1 000 ₴
    rf"|(?:\b\d[\d,]*(?:\.\d+)?\s?(?:{_CURRENCY_CODE})\b)",  # 2000 USD / 1.5 BTC
    re.IGNORECASE,
)
# Phone: optional +, then 9-18 digits with common separators. Require a leading
# + or at least 9 digits to avoid matching ordinary numbers.
_PHONE = re.compile(r"(?<![\w.])\+?\d[\d\s().\-]{7,}\d(?![\w])")


def _norm(entity_type: str, value: str) -> str:
    if entity_type in ("email", "url"):
        return value.lower()
    if entity_type == "phone":
        digits = re.sub(r"\D", "", value)
        return f"+{digits}" if value.strip().startswith("+") else digits
    if entity_type == "amount":
        return re.sub(r"\s+", " ", value.strip())
    return value  # wallets are case-sensitive (checksums)


def extract_entities(text: str | None) -> list[dict]:
    """Return a list of detected entities with character offsets.

    Each item: {entity_type, value, normalized_value, offset_start, offset_end}.
    Overlapping matches are resolved by preferring the earlier start, then the
    longer span, so a URL is not also reported as an email/phone.
    """
    if not text:
        return []

    raw: list[tuple[int, int, str, str]] = []  # (start, end, type, value)
    for etype, pattern in (
        ("url", _URL),
        ("email", _EMAIL),
        ("crypto_wallet", _ETH),
        ("crypto_wallet", _TRON),
        ("amount", _AMOUNT),
        ("phone", _PHONE),
    ):
        for m in pattern.finditer(text):
            raw.append((m.start(), m.end(), etype, m.group(0)))

    # Resolve overlaps: sort by start asc, then by span length desc.
    raw.sort(key=lambda r: (r[0], -(r[1] - r[0])))
    chosen: list[tuple[int, int, str, str]] = []
    occupied: list[tuple[int, int]] = []
    for start, end, etype, value in raw:
        if any(start < oe and end > os_ for os_, oe in occupied):
            continue  # overlaps an already-chosen, higher-priority span
        chosen.append((start, end, etype, value))
        occupied.append((start, end))

    chosen.sort(key=lambda r: r[0])
    return [
        {
            "entity_type": etype,
            "value": value,
            "normalized_value": _norm(etype, value),
            "offset_start": start,
            "offset_end": end,
        }
        for start, end, etype, value in chosen
    ]
