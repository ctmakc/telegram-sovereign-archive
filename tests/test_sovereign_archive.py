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
