"""Tests for the Sovereign Archive layer (Epic A).

Append-only / evidence-grade guarantees:
- SAFE_ARCHIVE_MODE master guardrail
- Deletion tombstones (Telegram deletions never destroy the local row)
- Message version history (edits never lose the prior text)
- Append-only message event log
"""

import os
import sys
from datetime import UTC, datetime
from unittest.mock import patch

import pytest
import pytest_asyncio

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.config import Config
from src.db.adapter import DatabaseAdapter
from src.db.base import DatabaseManager

# ============================================================
# Increment 1 — Config: SAFE_ARCHIVE_MODE master guardrail
# ============================================================


class TestSafeArchiveModeConfig:
    def _config(self, env):
        base = {"CHAT_TYPES": "private"}
        base.update(env)
        with patch("os.makedirs"), patch.dict(os.environ, base, clear=True):
            return Config()

    def test_safe_archive_mode_defaults_true(self):
        """Sovereign archive is safe by default — never lose data unless opted out."""
        config = self._config({})
        assert config.safe_archive_mode is True

    def test_delete_local_defaults_false(self):
        config = self._config({})
        assert config.delete_local_on_telegram_delete is False

    def test_safe_mode_forces_delete_local_off_even_when_requested(self):
        """Master guardrail: SAFE_ARCHIVE_MODE wins over DELETE_LOCAL_ON_TELEGRAM_DELETE."""
        config = self._config(
            {"SAFE_ARCHIVE_MODE": "true", "DELETE_LOCAL_ON_TELEGRAM_DELETE": "true"}
        )
        assert config.safe_archive_mode is True
        assert config.delete_local_on_telegram_delete is False

    def test_opting_out_of_safe_mode_allows_hard_delete(self):
        config = self._config(
            {"SAFE_ARCHIVE_MODE": "false", "DELETE_LOCAL_ON_TELEGRAM_DELETE": "true"}
        )
        assert config.safe_archive_mode is False
        assert config.delete_local_on_telegram_delete is True


# ============================================================
# Real in-memory-ish DB fixture (file-backed; NullPool drops :memory:)
# ============================================================


@pytest_asyncio.fixture
async def adapter(tmp_path):
    """A DatabaseAdapter backed by a real SQLite file with all tables created."""
    db_path = tmp_path / "sovereign_test.db"
    manager = DatabaseManager(f"sqlite:///{db_path}")
    await manager.init()
    yield DatabaseAdapter(manager)
    await manager.close()


async def _seed_message(adapter, *, chat_id=-100, message_id=1, text="original"):
    await adapter.upsert_chat({"id": chat_id, "type": "supergroup", "title": "T"})
    await adapter.insert_message(
        {
            "id": message_id,
            "chat_id": chat_id,
            "sender_id": 42,
            "date": datetime(2026, 6, 15, 12, 0, 0, tzinfo=UTC),
            "text": text,
        }
    )


# ============================================================
# Increment 2 — Deletion tombstones (append-only)
# ============================================================


class TestDeletionTombstone:
    @pytest.mark.asyncio
    async def test_mark_deleted_preserves_row_text_media_and_reactions(self, adapter):
        """A Telegram deletion must flag the row, never destroy local truth."""
        await _seed_message(adapter, text="secret deal terms")
        await adapter.insert_media(
            {"id": "file1", "message_id": 1, "chat_id": -100, "type": "document"}
        )
        await adapter.insert_reactions(1, -100, [{"emoji": "👍", "user_id": 42, "count": 1}])

        await adapter.mark_message_deleted(-100, 1)

        msg = await adapter.get_message(-100, 1)
        assert msg is not None, "row must be preserved after Telegram deletion"
        assert msg["text"] == "secret deal terms"
        assert msg["is_deleted_in_telegram"] == 1
        assert msg["deleted_detected_at"] is not None
        # associated evidence must survive too
        assert len(await adapter.get_media_for_chat(-100)) == 1
        assert len(await adapter.get_reactions(1, -100)) == 1

    @pytest.mark.asyncio
    async def test_mark_deleted_is_idempotent(self, adapter):
        await _seed_message(adapter)
        await adapter.mark_message_deleted(-100, 1)
        first = (await adapter.get_message(-100, 1))["deleted_detected_at"]
        await adapter.mark_message_deleted(-100, 1)
        again = (await adapter.get_message(-100, 1))["deleted_detected_at"]
        assert first == again, "first-detected deletion timestamp must be preserved"

    @pytest.mark.asyncio
    async def test_mark_deleted_records_event(self, adapter):
        await _seed_message(adapter)
        await adapter.mark_message_deleted(-100, 1)
        events = await adapter.get_message_events(-100, 1)
        assert any(e["event_type"] == "deleted" for e in events)


# ============================================================
# Increment 3 — Message versioning on edit (append-only)
# ============================================================


import hashlib  # noqa: E402


def _sha256(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class TestMessageVersioning:
    @pytest.mark.asyncio
    async def test_first_edit_snapshots_prior_text_and_updates_live(self, adapter):
        await _seed_message(adapter, text="A")
        edited = await adapter.record_message_edit(
            -100, 1, "B", datetime(2026, 6, 15, 13, 0, 0, tzinfo=UTC)
        )
        assert edited is True

        # live row now holds the new text
        assert (await adapter.get_message(-100, 1))["text"] == "B"
        # the prior text is preserved as version 1
        versions = await adapter.get_message_versions(-100, 1)
        assert len(versions) == 1
        assert versions[0]["version_number"] == 1
        assert versions[0]["text"] == "A"
        assert versions[0]["content_hash"] == _sha256("A")

    @pytest.mark.asyncio
    async def test_three_edits_keep_all_prior_texts_in_order(self, adapter):
        await _seed_message(adapter, text="A")
        for new in ("B", "C", "D"):
            await adapter.record_message_edit(-100, 1, new, datetime(2026, 6, 15, 13, 0, 0, tzinfo=UTC))

        versions = await adapter.get_message_versions(-100, 1)
        assert [v["text"] for v in versions] == ["A", "B", "C"]
        assert [v["version_number"] for v in versions] == [1, 2, 3]
        assert (await adapter.get_message(-100, 1))["text"] == "D"

    @pytest.mark.asyncio
    async def test_edit_records_edited_event(self, adapter):
        await _seed_message(adapter, text="A")
        await adapter.record_message_edit(-100, 1, "B", None)
        events = await adapter.get_message_events(-100, 1)
        assert any(e["event_type"] == "edited" for e in events)

    @pytest.mark.asyncio
    async def test_noop_edit_same_text_creates_no_version(self, adapter):
        await _seed_message(adapter, text="A")
        edited = await adapter.record_message_edit(-100, 1, "A", None)
        assert edited is False
        assert await adapter.get_message_versions(-100, 1) == []


class TestMessageContext:
    """UC-05: open any message with N messages of surrounding context."""

    async def _seed_run(self, adapter, n, chat_id=-100):
        await adapter.upsert_chat({"id": chat_id, "type": "supergroup", "title": "T"})
        for i in range(1, n + 1):
            await adapter.insert_message(
                {"id": i, "chat_id": chat_id, "date": datetime(2026, 6, 15, 12, i, tzinfo=UTC), "text": f"m{i}"}
            )

    @pytest.mark.asyncio
    async def test_context_returns_window_around_target(self, adapter):
        await self._seed_run(adapter, 10)
        ctx = await adapter.get_message_context(-100, 5, window=2)
        assert [m["id"] for m in ctx] == [3, 4, 5, 6, 7]

    @pytest.mark.asyncio
    async def test_context_clamps_at_start_boundary(self, adapter):
        await self._seed_run(adapter, 5)
        ctx = await adapter.get_message_context(-100, 1, window=2)
        assert [m["id"] for m in ctx] == [1, 2, 3]

    @pytest.mark.asyncio
    async def test_context_missing_message_returns_empty(self, adapter):
        await adapter.upsert_chat({"id": -100, "type": "supergroup", "title": "T"})
        assert await adapter.get_message_context(-100, 999, window=2) == []

    @pytest.mark.asyncio
    async def test_context_includes_deleted_target(self, adapter):
        """A deleted-in-Telegram message must still be openable in context."""
        await self._seed_run(adapter, 5)
        await adapter.mark_message_deleted(-100, 3)
        ctx = await adapter.get_message_context(-100, 3, window=1)
        target = next(m for m in ctx if m["id"] == 3)
        assert target["is_deleted_in_telegram"] == 1
        assert [m["id"] for m in ctx] == [2, 3, 4]


class TestGlobalSearch:
    """§15/UC-04: cross-chat search with explicit, non-destructive filters."""

    async def _seed(self, adapter):
        for cid, title in ((-100, "Deals"), (-200, "Family")):
            await adapter.upsert_chat({"id": cid, "type": "supergroup", "title": title})
        # -100: deal chat
        await adapter.insert_message(
            {"id": 1, "chat_id": -100, "date": datetime(2026, 6, 1, tzinfo=UTC), "text": "invoice for Poland deal"}
        )
        await adapter.insert_message(
            {"id": 2, "chat_id": -100, "date": datetime(2026, 6, 2, tzinfo=UTC), "text": "payment in USDT"}
        )
        await adapter.insert_media(
            {"id": "f1", "message_id": 2, "chat_id": -100, "type": "document", "downloaded": 1}
        )
        # -200: family chat (also mentions 'deal' to prove cross-chat)
        await adapter.insert_message(
            {"id": 1, "chat_id": -200, "date": datetime(2026, 6, 3, tzinfo=UTC), "text": "great deal on groceries"}
        )
        # edited + deleted markers
        await adapter.record_message_edit(-100, 1, "invoice for Poland deal (signed)", None)
        await adapter.mark_message_deleted(-200, 1)

    @pytest.mark.asyncio
    async def test_query_matches_across_chats(self, adapter):
        await self._seed(adapter)
        hits = await adapter.search_messages(query="deal")
        keys = {(h["chat_id"], h["id"]) for h in hits}
        assert (-100, 1) in keys and (-200, 1) in keys
        # chat title is surfaced for context
        titles = {h["chat_title"] for h in hits}
        assert {"Deals", "Family"} <= titles

    @pytest.mark.asyncio
    async def test_deleted_only_filter(self, adapter):
        await self._seed(adapter)
        hits = await adapter.search_messages(deleted_only=True)
        assert {(h["chat_id"], h["id"]) for h in hits} == {(-200, 1)}

    @pytest.mark.asyncio
    async def test_edited_only_filter(self, adapter):
        await self._seed(adapter)
        hits = await adapter.search_messages(edited_only=True)
        assert {(h["chat_id"], h["id"]) for h in hits} == {(-100, 1)}

    @pytest.mark.asyncio
    async def test_media_only_filter(self, adapter):
        await self._seed(adapter)
        hits = await adapter.search_messages(media_only=True)
        assert {(h["chat_id"], h["id"]) for h in hits} == {(-100, 2)}
        assert all(h["has_media"] for h in hits)

    @pytest.mark.asyncio
    async def test_chat_scope_and_date_range(self, adapter):
        await self._seed(adapter)
        hits = await adapter.search_messages(
            chat_id=-100, start_date=datetime(2026, 6, 2, tzinfo=UTC)
        )
        assert {(h["chat_id"], h["id"]) for h in hits} == {(-100, 2)}

    @pytest.mark.asyncio
    async def test_query_finds_text_from_old_versions(self, adapter):
        """Phase 2/4 AC: exact search must find text that was later edited away."""
        await adapter.upsert_chat({"id": -100, "type": "supergroup", "title": "Deals"})
        await adapter.insert_message(
            {"id": 1, "chat_id": -100, "date": datetime(2026, 6, 1, tzinfo=UTC), "text": "pay 5000 USDT"}
        )
        await adapter.record_message_edit(-100, 1, "cancelled", None)

        # live text no longer mentions USDT, but the deal evidence must still surface
        hits = await adapter.search_messages(query="USDT")
        assert {(h["chat_id"], h["id"]) for h in hits} == {(-100, 1)}
        hit = hits[0]
        assert hit["matched_historical"] is True  # flag: matched only in an old version

    @pytest.mark.asyncio
    async def test_filter_by_sender(self, adapter):
        await adapter.upsert_chat({"id": -100, "type": "supergroup", "title": "T"})
        base = datetime(2026, 6, 1, tzinfo=UTC)
        await adapter.insert_message({"id": 1, "chat_id": -100, "sender_id": 11, "date": base, "text": "from alice"})
        await adapter.insert_message({"id": 2, "chat_id": -100, "sender_id": 22, "date": base, "text": "from bob"})
        hits = await adapter.search_messages(sender_id=11)
        assert {(h["chat_id"], h["id"]) for h in hits} == {(-100, 1)}

    @pytest.mark.asyncio
    async def test_filter_by_media_type(self, adapter):
        await adapter.upsert_chat({"id": -100, "type": "supergroup", "title": "T"})
        base = datetime(2026, 6, 1, tzinfo=UTC)
        await adapter.insert_message({"id": 1, "chat_id": -100, "date": base, "text": "voice note"})
        await adapter.insert_message({"id": 2, "chat_id": -100, "date": base, "text": "a doc"})
        await adapter.insert_media({"id": "v1", "message_id": 1, "chat_id": -100, "type": "voice", "downloaded": 1})
        await adapter.insert_media({"id": "d1", "message_id": 2, "chat_id": -100, "type": "document", "downloaded": 1})
        hits = await adapter.search_messages(media_type="voice")
        assert {(h["chat_id"], h["id"]) for h in hits} == {(-100, 1)}

    @pytest.mark.asyncio
    async def test_filter_has_link(self, adapter):
        await adapter.upsert_chat({"id": -100, "type": "supergroup", "title": "T"})
        base = datetime(2026, 6, 1, tzinfo=UTC)
        await adapter.insert_message({"id": 1, "chat_id": -100, "date": base, "text": "see https://example.com/x"})
        await adapter.insert_message({"id": 2, "chat_id": -100, "date": base, "text": "no link here"})
        hits = await adapter.search_messages(has_link=True)
        assert {(h["chat_id"], h["id"]) for h in hits} == {(-100, 1)}

    @pytest.mark.asyncio
    async def test_live_match_not_flagged_historical(self, adapter):
        await adapter.upsert_chat({"id": -100, "type": "supergroup", "title": "Deals"})
        await adapter.insert_message(
            {"id": 1, "chat_id": -100, "date": datetime(2026, 6, 1, tzinfo=UTC), "text": "pay 5000 USDT"}
        )
        hits = await adapter.search_messages(query="USDT")
        assert hits[0]["matched_historical"] is False


class TestEvidenceExport:
    """PRD §21: evidence package with tamper-evident hash manifest."""

    async def _seed(self, adapter):
        await adapter.upsert_chat({"id": -100, "type": "supergroup", "title": "Deals"})
        await adapter.insert_message(
            {"id": 1, "chat_id": -100, "date": datetime(2026, 6, 1, tzinfo=UTC), "text": "hello"}
        )
        await adapter.insert_message(
            {"id": 2, "chat_id": -100, "date": datetime(2026, 6, 2, tzinfo=UTC), "text": "A"}
        )
        await adapter.insert_message(
            {"id": 3, "chat_id": -100, "date": datetime(2026, 6, 3, tzinfo=UTC), "text": "bye"}
        )
        await adapter.record_message_edit(-100, 2, "B", None)
        await adapter.mark_message_deleted(-100, 3)

    @pytest.mark.asyncio
    async def test_package_captures_history_and_is_deterministic(self, adapter):
        await self._seed(adapter)
        pkg = await adapter.build_evidence_package(-100)

        assert pkg["chat"]["id"] == -100
        assert pkg["chat"]["title"] == "Deals"
        assert pkg["manifest"]["message_count"] == 3
        assert len(pkg["manifest"]["content_sha256"]) == 64
        assert pkg["manifest"]["non_modification_statement"]

        m2 = next(m for m in pkg["messages"] if m["id"] == 2)
        assert [v["text"] for v in m2["versions"]] == ["A"]  # prior text preserved
        m3 = next(m for m in pkg["messages"] if m["id"] == 3)
        assert m3["is_deleted_in_telegram"] == 1  # deleted-but-preserved

        # deterministic: identical content → identical hash (manifest timestamp excluded)
        pkg2 = await adapter.build_evidence_package(-100)
        assert pkg2["manifest"]["content_sha256"] == pkg["manifest"]["content_sha256"]

    @pytest.mark.asyncio
    async def test_tamper_changes_hash(self, adapter):
        await self._seed(adapter)
        h1 = (await adapter.build_evidence_package(-100))["manifest"]["content_sha256"]
        await adapter.record_message_edit(-100, 1, "hello (tampered)", None)
        h2 = (await adapter.build_evidence_package(-100))["manifest"]["content_sha256"]
        assert h1 != h2

    @pytest.mark.asyncio
    async def test_date_range_filters(self, adapter):
        await self._seed(adapter)
        pkg = await adapter.build_evidence_package(-100, start_date=datetime(2026, 6, 2, tzinfo=UTC))
        assert {m["id"] for m in pkg["messages"]} == {2, 3}


class TestIntegrityChecks:
    """Epic E: data-integrity checks (PRD §19.3)."""

    @pytest.mark.asyncio
    async def test_broken_reply_references_detected(self, adapter):
        await adapter.upsert_chat({"id": -100, "type": "supergroup", "title": "T"})
        base = datetime(2026, 6, 15, tzinfo=UTC)
        await adapter.insert_message({"id": 1, "chat_id": -100, "date": base, "text": "root"})
        await adapter.insert_message(
            {"id": 2, "chat_id": -100, "date": base, "text": "valid reply", "reply_to_msg_id": 1}
        )
        await adapter.insert_message(
            {"id": 3, "chat_id": -100, "date": base, "text": "dangling", "reply_to_msg_id": 999}
        )
        broken = await adapter.get_broken_reply_references()
        assert {b["id"] for b in broken} == {3}
        assert broken[0]["reply_to_msg_id"] == 999

    @pytest.mark.asyncio
    async def test_no_broken_references_when_all_resolve(self, adapter):
        await adapter.upsert_chat({"id": -100, "type": "supergroup", "title": "T"})
        base = datetime(2026, 6, 15, tzinfo=UTC)
        await adapter.insert_message({"id": 1, "chat_id": -100, "date": base, "text": "root"})
        await adapter.insert_message(
            {"id": 2, "chat_id": -100, "date": base, "text": "reply", "reply_to_msg_id": 1}
        )
        assert await adapter.get_broken_reply_references() == []


class TestMediaFileVerification:
    """Epic E (PRD §19.3): verify DB media records against files on disk."""

    @pytest.mark.asyncio
    async def test_detects_missing_files(self, adapter, tmp_path):
        present = tmp_path / "present.bin"
        present.write_bytes(b"data")
        gone = tmp_path / "gone.bin"  # never created
        await adapter.upsert_chat({"id": -100, "type": "supergroup", "title": "T"})
        await adapter.insert_message(
            {"id": 1, "chat_id": -100, "date": datetime(2026, 6, 15, tzinfo=UTC), "text": "m"}
        )
        await adapter.insert_media(
            {"id": "ok", "message_id": 1, "chat_id": -100, "type": "document",
             "file_path": str(present), "downloaded": True}
        )
        await adapter.insert_media(
            {"id": "missing", "message_id": 1, "chat_id": -100, "type": "document",
             "file_path": str(gone), "downloaded": True}
        )

        report = await adapter.verify_media_files(str(tmp_path))
        assert report["checked"] == 2
        assert {m["id"] for m in report["missing"]} == {"missing"}

    @pytest.mark.asyncio
    async def test_clean_archive_reports_no_missing(self, adapter, tmp_path):
        f = tmp_path / "f.bin"
        f.write_bytes(b"x")
        await adapter.upsert_chat({"id": -100, "type": "supergroup", "title": "T"})
        await adapter.insert_message(
            {"id": 1, "chat_id": -100, "date": datetime(2026, 6, 15, tzinfo=UTC), "text": "m"}
        )
        await adapter.insert_media(
            {"id": "ok", "message_id": 1, "chat_id": -100, "type": "document",
             "file_path": str(f), "downloaded": True}
        )
        report = await adapter.verify_media_files(str(tmp_path))
        assert report["missing"] == []


class TestSovereignStats:
    """Dashboard counters proving the append-only guarantees are working."""

    @pytest.mark.asyncio
    async def test_stats_count_preserved_deleted_and_versioned(self, adapter):
        await _seed_message(adapter, message_id=1, text="kept")
        await _seed_message(adapter, message_id=2, text="A")
        await _seed_message(adapter, message_id=3, text="untouched")
        # one deleted-but-preserved
        await adapter.mark_message_deleted(-100, 1)
        # one edited twice (2 versions)
        await adapter.record_message_edit(-100, 2, "B", None)
        await adapter.record_message_edit(-100, 2, "C", None)

        stats = await adapter.get_sovereign_stats()
        assert stats["deleted_preserved"] == 1
        assert stats["messages_with_history"] == 1
        assert stats["total_versions"] == 2
        # events: 1 deleted + 2 edited
        assert stats["events_total"] == 3

    @pytest.mark.asyncio
    async def test_stats_empty_archive_is_all_zero(self, adapter):
        await adapter.upsert_chat({"id": -100, "type": "supergroup", "title": "T"})
        stats = await adapter.get_sovereign_stats()
        assert stats["deleted_preserved"] == 0
        assert stats["messages_with_history"] == 0
        assert stats["total_versions"] == 0
        assert stats["events_total"] == 0


# ============================================================
# Epic B — Media reliability: status lifecycle + integrity
# ============================================================


async def _seed_media(adapter, media_id="m1", *, chat_id=-100, message_id=1, downloaded=0):
    await adapter.upsert_chat({"id": chat_id, "type": "supergroup", "title": "T"})
    await adapter.insert_message(
        {"id": message_id, "chat_id": chat_id, "date": datetime(2026, 6, 15, tzinfo=UTC), "text": "m"}
    )
    await adapter.insert_media(
        {"id": media_id, "message_id": message_id, "chat_id": chat_id, "type": "document", "downloaded": downloaded}
    )


class TestMediaReliability:
    @pytest.mark.asyncio
    async def test_insert_sets_status_from_downloaded_flag(self, adapter):
        await _seed_media(adapter, "done", downloaded=1)
        await _seed_media(adapter, "todo", message_id=2, downloaded=0)
        statuses = {m["id"]: m["download_status"] for m in await adapter.get_media_for_chat(-100)}
        assert statuses["done"] == "downloaded"
        assert statuses["todo"] == "pending"

    @pytest.mark.asyncio
    async def test_mark_failed_tracks_attempts_and_error(self, adapter):
        await _seed_media(adapter, "m1")
        await adapter.mark_media_failed("m1", "timeout")
        await adapter.mark_media_failed("m1", "connection reset")
        rec = await adapter.get_media("m1")
        assert rec["download_status"] == "failed"
        assert rec["download_attempts"] == 2
        assert rec["last_download_error"] == "connection reset"
        assert rec["downloaded"] == 0  # never falsely marked complete

    @pytest.mark.asyncio
    async def test_mark_skipped_records_reason_without_marking_complete(self, adapter):
        await _seed_media(adapter, "m1")
        await adapter.mark_media_skipped("m1", "exceeds MAX_MEDIA_SIZE_MB")
        rec = await adapter.get_media("m1")
        assert rec["download_status"] == "skipped"
        assert rec["skipped_reason"] == "exceeds MAX_MEDIA_SIZE_MB"
        assert rec["downloaded"] == 0

    @pytest.mark.asyncio
    async def test_mark_downloaded_completes(self, adapter):
        await _seed_media(adapter, "m1")
        await adapter.mark_media_failed("m1", "x")
        await adapter.mark_media_downloaded("m1", file_path="/data/m1.bin", content_hash="abc")
        rec = await adapter.get_media("m1")
        assert rec["download_status"] == "downloaded"
        assert rec["downloaded"] == 1
        assert rec["file_path"] == "/data/m1.bin"
        assert rec["content_hash"] == "abc"

    @pytest.mark.asyncio
    async def test_integrity_summary_counts_by_status(self, adapter):
        await _seed_media(adapter, "a", downloaded=1)
        await _seed_media(adapter, "b", message_id=2)
        await _seed_media(adapter, "c", message_id=3)
        await adapter.mark_media_failed("b", "err")
        await adapter.mark_media_skipped("c", "too big")
        summary = await adapter.get_media_integrity_summary()
        assert summary["downloaded"] == 1
        assert summary["failed"] == 1
        assert summary["skipped"] == 1
        assert summary["total"] == 3
        # incomplete = anything not successfully downloaded
        assert summary["incomplete"] == 2

    @pytest.mark.asyncio
    async def test_get_failed_media_is_retry_queue(self, adapter):
        await _seed_media(adapter, "a", downloaded=1)
        await _seed_media(adapter, "b", message_id=2)
        await adapter.mark_media_failed("b", "err")
        failed_ids = {m["id"] for m in await adapter.get_failed_media()}
        assert failed_ids == {"b"}

    @pytest.mark.asyncio
    async def test_insert_media_persists_explicit_skipped_status_and_reason(self, adapter):
        """A size-skipped download must be recorded as skipped with a visible reason."""
        await adapter.upsert_chat({"id": -100, "type": "supergroup", "title": "T"})
        await adapter.insert_message(
            {"id": 1, "chat_id": -100, "date": datetime(2026, 6, 15, tzinfo=UTC), "text": "m"}
        )
        await adapter.insert_media(
            {
                "id": "big",
                "message_id": 1,
                "chat_id": -100,
                "type": "document",
                "file_size": 9_000_000_000,
                "downloaded": False,
                "download_status": "skipped",
                "skipped_reason": "exceeds MAX_MEDIA_SIZE_MB",
            }
        )
        rec = await adapter.get_media("big")
        assert rec["download_status"] == "skipped"
        assert rec["skipped_reason"] == "exceeds MAX_MEDIA_SIZE_MB"
        assert rec["downloaded"] == 0


class TestMediaSkipWiring:
    """The backup's _process_media must record oversize files as skipped (not silently pending)."""

    def _backup(self, max_bytes):
        import asyncio  # noqa: F401
        from unittest.mock import MagicMock

        from src.telegram_backup import TelegramBackup

        b = TelegramBackup.__new__(TelegramBackup)
        b.config = MagicMock()
        b.config.media_path = "/tmp/tsa_skip"
        b.config.get_max_media_size_bytes = MagicMock(return_value=max_bytes)
        b._get_media_type = MagicMock(return_value="document")
        b._get_media_size = MagicMock(return_value=999_999_999)
        return b

    def test_oversize_media_is_marked_skipped_with_reason(self):
        import asyncio
        from unittest.mock import MagicMock

        b = self._backup(max_bytes=1024)
        msg = MagicMock()
        msg.id = 5
        msg.media = MagicMock()
        msg.media.document = MagicMock()
        msg.media.document.id = "d1"
        msg.media.photo = None

        result = asyncio.run(b._process_media(msg, -100))

        assert result["downloaded"] is False
        assert result["download_status"] == "skipped"
        assert "MAX_MEDIA_SIZE" in result["skipped_reason"]

    def test_failed_retry_records_failure_in_queue(self):
        """A download that errors during retry must be marked failed (visible in retry queue)."""
        import asyncio
        from unittest.mock import AsyncMock, MagicMock

        from src.telegram_backup import TelegramBackup

        b = TelegramBackup.__new__(TelegramBackup)
        b.config = MagicMock()
        b.config.skip_media_chat_ids = set()
        b.config.get_max_media_size_bytes = MagicMock(return_value=100 * 1024 * 1024)
        b.db = AsyncMock()
        b.db.get_pending_media_downloads = AsyncMock(
            return_value=[{"id": "x", "message_id": 5, "chat_id": -100, "type": "document"}]
        )
        msg = MagicMock()
        msg.id = 5
        msg.media = MagicMock()
        b.client = AsyncMock()
        b.client.get_messages = AsyncMock(return_value=[msg])
        b._process_media = AsyncMock(side_effect=RuntimeError("download boom"))

        asyncio.run(b._retry_pending_media_downloads())

        b.db.mark_media_failed.assert_awaited_once()
        called_id = b.db.mark_media_failed.await_args.args[0]
        assert called_id == "x"
