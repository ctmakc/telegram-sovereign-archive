"""Tests for the deterministic entity extractor (Epic F/G, no LLM).

Pulls high-signal, low-false-positive entities out of message text so deals can be
found by email / wallet / amount / phone / url — PRD §9 / §12.10 / §16 deal mode.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.entities import extract_entities


def _by_type(text):
    out = {}
    for e in extract_entities(text):
        out.setdefault(e["entity_type"], []).append(e["value"])
    return out


class TestEntityExtraction:
    def test_email(self):
        ents = _by_type("write me at Alice@Example.com please")
        assert ents["email"] == ["Alice@Example.com"]

    def test_email_normalized_lowercase(self):
        e = next(x for x in extract_entities("X@Y.COM") if x["entity_type"] == "email")
        assert e["normalized_value"] == "x@y.com"

    def test_url(self):
        ents = _by_type("see https://example.com/deal?id=5 and http://a.io")
        assert "https://example.com/deal?id=5" in ents["url"]
        assert "http://a.io" in ents["url"]

    def test_eth_wallet(self):
        addr = "0x52908400098527886E0F7030069857D2E4169EE7"
        ents = _by_type(f"send to {addr}")
        assert ents["crypto_wallet"] == [addr]

    def test_tron_wallet(self):
        addr = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"
        ents = _by_type(f"USDT wallet {addr}")
        assert addr in ents["crypto_wallet"]

    def test_amount_with_currency(self):
        ents = _by_type("price is $1,500 or 2000 USD or 50€")
        vals = ents["amount"]
        assert "$1,500" in vals
        assert "2000 USD" in vals
        assert "50€" in vals

    def test_phone(self):
        ents = _by_type("call +1 (415) 555-2671 tomorrow")
        assert ents["phone"] == ["+1 (415) 555-2671"]

    def test_offsets_point_at_value(self):
        text = "mail x@y.com now"
        e = next(x for x in extract_entities(text) if x["entity_type"] == "email")
        assert text[e["offset_start"] : e["offset_end"]] == "x@y.com"

    def test_no_false_positive_on_plain_text(self):
        assert extract_entities("just a normal sentence with no entities") == []

    def test_empty_and_none(self):
        assert extract_entities("") == []
        assert extract_entities(None) == []
