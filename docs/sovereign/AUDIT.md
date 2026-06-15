# Phase 0 Audit — Telegram Sovereign Archive

**Fork:** `ctmakc/telegram-sovereign-archive` ← `GeiserX/Telegram-Archive`
**Audited commit:** upstream `main` (cloned 2026-06-15)
**Auditor:** Claude Code
**License:** GPL-3.0 (inherited). OK for internal use. See PRD §5 / Risk 5 if closed-source distribution is ever needed.

## 1. Verdict

The upstream is **mature and actively developed** (last push 2026-06-15, 13 alembic
migrations through 2026-05-24, viewer accounts/sessions, PWA, push). It already
covers an estimated **~70% of the PRD MVP**. We should build *on* it, not rewrite.

The gap is exactly the part that makes this "sovereign / evidence-grade" rather than
"a backup tool": **append-only guarantees, tombstones, version history, integrity
reporting, and the intelligence/RAG layers.**

## 2. What upstream ALREADY does (do not rebuild)

| PRD requirement | Upstream status | Where |
|---|---|---|
| Telegram user-session auth | ✅ | `src/setup_auth.py`, `init_auth.sh`, `scripts/auth_noninteractive.py` |
| Full + incremental sync | ✅ `last_synced_message_id` per chat | `src/telegram_backup.py`, `sync_status` table |
| Scheduled sync (cron) | ✅ | `src/scheduler.py` |
| Real-time listener (new/edit/delete) | ✅ | `src/listener.py`, `src/realtime.py` |
| Media download (photo/video/doc/voice/audio/sticker/gif/poll) | ✅ | `src/telegram_backup.py`, `src/parallel_download.py` |
| Media SHA-256 content hash | ✅ `media.content_hash` (migration 011, 2026-05-03) | `src/db/models.py:164` |
| Media dedup | ✅ via sharded shared-media + symlinks | `src/migrate_shared_media.py`, `scripts/deduplicate_media.py` |
| Albums / grouped media | ✅ | `scripts/detect_albums.py`, `scripts/normalize_grouped_ids.py` |
| Reactions | ✅ `reactions` table | `src/db/models.py:189` |
| Service messages (joins/leaves/title) | ✅ | `src/listener.py` ~L1005 |
| Forum topics / folders | ✅ (migration 006) | `forum_topics` table |
| Web viewer (Telegram-like, PWA, push) | ✅ | `src/web/`, `manifest.json`, `sw.js` |
| Viewer users / sessions / permissions | ✅ (migrations 007, 009) | `viewer_accounts`, `viewer_sessions` |
| Docker / compose / alembic | ✅ | `docker-compose.yml`, `alembic/` |
| SQLite + PostgreSQL | ✅ | `src/db/adapter.py`, `migrate-sqlite-to-postgres.py` |
| Backup/export | ✅ partial | `src/export_backup.py` |
| Mass-delete/edit flood protection | ✅ (sliding window) | `src/listener.py:54` `_protector` |
| `LISTEN_DELETIONS` default **false** | ✅ (backup protected by default) | `src/listener.py:737` |

## 3. CRITICAL GAPS vs the sovereign mandate (net-new work)

### 3.1 Deletions HARD-delete the archive  🔴 violates PRD §4.1/§4.2
`AsyncDatabaseAdapter.delete_message()` permanently removes the message row **and its
media and reactions**:

```python
# src/db/adapter.py:526
async def delete_message(self, chat_id, message_id):
    await session.execute(delete(Media).where(...))      # media gone
    await session.execute(delete(Reaction).where(...))   # reactions gone
    await session.execute(delete(Message).where(...))    # message gone
```

Called from `listener.py:775/796` when `LISTEN_DELETIONS=true`. Default-false means
the *default* is safe, but the sovereign requirement is **mirror deletions AND keep
local truth** — i.e. record a tombstone, never destroy the row. This method must
become non-destructive (or be bypassed) under `SAFE_ARCHIVE_MODE`.

**Fix (Epic A / Phase 1):** add `messages.is_deleted_in_telegram` +
`deleted_detected_at`; rewrite `delete_message` to UPDATE-flag instead of DELETE when
`SAFE_ARCHIVE_MODE=true`; surface deleted-but-preserved in viewer + search.

### 3.2 Edits OVERWRITE text — no version history  🔴 violates PRD §4.2
```python
# src/listener.py:707  ->  src/db/adapter.py:558
await self.db.update_message_text(chat_id, message_id, new_text, edit_date)
```
The previous text is lost. `messages.edit_date` records *that* an edit happened, not
*what changed*.

**Fix (Epic A / Phase 2):** new `message_versions` table (version_number, text,
edit_date, captured_at, content_hash, raw_json). First capture = v1; every edit
appends a new version before overwriting the live row. Viewer diff + search over
historical versions.

### 3.3 No append-only event log
No `message_events` table. created/edited/deleted/pinned/reaction_changed/
media_downloaded/media_failed are not recorded as immutable events.

**Fix (Epic A):** `message_events` append-only table; write an event on every mutation.

### 3.4 Media reliability metadata is thin
`media` has `downloaded` (0/1) + `content_hash` + `download_date`, but **no**
`download_status` enum (pending/failed/skipped/unavailable), `download_attempts`,
`last_download_error`, or `perceptual_hash`. No retry queue, no missing/orphan report.

**Fix (Epic B / Phase 3):** extend `media`; add retry queue + integrity reports
(missing files, orphan files, metadata-without-file, hash mismatch).

### 3.5 No intelligence/extraction layer
No `extracted_text`, `entities`, `entity_mentions`, `search_index_status` tables; no
Whisper/OCR/document-extraction workers; no vector index; no RAG API; no Obsidian
export. All of v1.1/v1.2 (Epics F, G).

### 3.6 Search is basic
Upstream search is DB-backed substring/FTS over the live `messages.text` only. PRD §15
needs: deleted-message search, historical-version search, media-only search,
attachment/mime filters, ±N context expansion, exportable result sets, and a strict
"never silently exclude" ranking mode.

## 4. Schema delta (new tables/columns to add)

- `messages`: **+** `is_deleted_in_telegram` (int), `deleted_detected_at` (datetime),
  `first_seen_at`, `last_seen_at`.
- **new** `message_versions` (PRD §12.6)
- **new** `message_events` (PRD §12.7)
- `media`: **+** `download_status`, `download_attempts`, `last_download_error`,
  `perceptual_hash`, `duration_seconds` rename align, `skipped_reason`.
- **new** `extracted_text` (§12.9)
- **new** `entities` (§12.10), `entity_mentions` (§12.11)
- **new** `search_index_status` (§12.12)
- `chats`: **+** `sync_enabled`, `priority_override` (UI-only), `notes`.

All via alembic migrations (continue the `0XX_` numbering; next is `014`).

## 5. Config additions (.env)
Add sovereign flags (PRD §24): `SAFE_ARCHIVE_MODE=true`,
`DELETE_LOCAL_ON_TELEGRAM_DELETE=false`, plus extraction/RAG toggles (default off):
`ENABLE_TRANSCRIPTION`, `ENABLE_OCR`, `ENABLE_VECTOR_SEARCH`, `ENABLE_OBSIDIAN_EXPORT`.

`SAFE_ARCHIVE_MODE` must be the master guardrail: when true it forces
`DELETE_LOCAL_ON_TELEGRAM_DELETE=false` and routes deletions/edits through the
tombstone + versioning paths regardless of other flags.

## 6. Recommended build order (matches PRD §29)
1. **Epic A — archive safety** (tombstones, versioning, event log, `SAFE_ARCHIVE_MODE`) — highest value, smallest surface.
2. **Epic B — media reliability** (status/retry/integrity reports).
3. **Epic C/D — search + viewer** (deleted/version/media search, context, badges).
4. **Epic E — backup/restore + integrity check** (round-trip on clean machine).
5. **Epic F — voice/OCR/doc extraction** (workers, local models).
6. **Epic G — Obsidian/RAG** (citations-first).

## 7. Risks confirmed in code
- **Session security:** `.gitignore` review needed — ensure `*.session` and `/data/session` excluded (PRD §20.1). `.gitguardian.yaml` present (good signal).
- **GPL-3.0:** fine internally; reimplement if closed-source product later.
- **Fork visibility:** the fork is currently **PUBLIC** (GitHub forks of public repos can't be private). No data lives in the repo, only code — acceptable. If a private home is wanted, mirror to a fresh private repo instead of forking. *(Flagged for user decision.)*

## 8. Test strategy
Upstream has `tests/` + codecov. Every sovereign change is TDD:
- delete a message in TG → row preserved, flagged, still searchable (Phase 1 AC).
- edit a message 3× → 3 versions retrievable, old text searchable (Phase 2 AC).
- duplicate file → single blob, two references; failed download visible (Phase 3 AC).
