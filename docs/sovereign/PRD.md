# PRD: Telegram Sovereign Archive / Telegram Mirror Memory

> Canonical product spec. Treat Telegram as the raw memory layer of business and life:
> **preserve first, index second, summarize third, never discard, never trust AI
> without sources, never let a Telegram deletion destroy local truth.**
>
> Grounded implementation status against upstream code lives in [`AUDIT.md`](./AUDIT.md).

## 1. Purpose
Local, self-hosted full mirror of a Telegram account: private chats, groups,
supergroups, channels, bots, media, documents, voice/audio/video/photos, forwards,
replies, reactions, service events, edits/deletions, links, attachments, metadata.

Telegram is used as working, legal, commercial and personal memory. The system must
**never** treat any message as "garbage" at ingest/storage. Classification affects
*presentation only*, never *preservation*.

## 2–3. Problem & Goal (summary)
Telegram is the operational comms layer for deals, clients, files, voice notes,
credentials, ideas. Accounts get banned/lost/limited; counterparties delete chats;
old agreements are unfindable; voice/attachments aren't indexed; SaaS solutions
require handing over the account. Goal: a local archive that incrementally mirrors all
cloud chats, preserves edit history and deletions as events (without removing local
copies), supports exact + semantic + transcript + OCR search, ships a Telegram-like
viewer, and feeds Obsidian / local RAG / AI agents — all local-first, with encrypted
backup.

## 4. Principles
- **4.1 Zero-discard:** ingest never drops messages, files, empties, reactions, short
  texts, stickers, forwards, service messages. No "skip short/old/no-keyword/no-text/
  large/untranscribed" rules at the raw layer. Filtering is allowed only in UI /
  search / AI / analytics.
- **4.2 Append-only:** edits create versions (old kept); Telegram deletions add
  `deleted_in_telegram=true` + `deleted_at` but never remove the local copy;
  previously-downloaded files stay even if later unavailable.
- **4.3 Raw first, intelligence second:** AI/summarize/tag/extract/OCR/Whisper/Obsidian
  are secondary layers and must not affect archive completeness.
- **4.4 Local-first:** Docker, Postgres/SQLite, local media folder, local viewer, local
  search; cloud only as an encrypted backup target.
- **4.5 Evidence-grade:** reconstruct who/when/which chat/in reply to what/attachments/
  edits/deletions/forwards/source/participants/original link/file hash.

## 5. Strategy
Fork `GeiserX/Telegram-Archive` for auth, incremental download, Docker, media
download, viewer, search, scheduled backup, real-time listener. Doctor it from
"archiver" into a sovereign memory system. GPL-3.0 is fine for internal use; if a
closed-source product is later needed, reimplement core on Telethon/Pyrogram using
upstream only as reference. **Decision (internal use): fast path = fork.**

## 6–7. Audience & Use cases
Business owners, lawyers, consultants, managing partners, sales/BD; teams, agencies,
recruiters, founders, family offices.
Use cases: UC-01 full local backup · UC-02 continuous mirror · UC-03 deletion
protection · UC-04 deal search · UC-05 context reconstruction (±N) · UC-06 voice
search (Whisper) · UC-07 image/document search (OCR/extraction) · UC-08 curated
Obsidian export · UC-09 AI-agent memory with citations.

## 8. MVP scope
In: fork; docker-compose; TG auth; full sync of all cloud chats; scheduled
incremental sync; download all media; metadata; **edits as versions**; **deletions as
tombstones (no local removal)**; full-text search; web viewer; backup/restore;
integrity checks; JSONL/Parquet export; local search API; rate-limit config; error
logs + retry queue.
Out: full AI assistant; auto legal opinions; auto garbage deletion; auto publishing;
multi-account enterprise dashboard; face recognition; cloud SaaS.

## 9. v1.1
Whisper/faster-whisper transcription; OCR (images, PDF); document text extraction;
advanced search UI; saved searches; tags/labels (no raw mutation); entity extraction
(people, companies, emails, phones, addresses, crypto wallets, amounts, currencies,
jurisdictions, dates, document names); deal-timeline mode; Obsidian export; local
vector index.

## 10. v1.2
Local RAG API; chat-with-archive; source-grounded answers; per-project knowledge maps;
deduplicated media library; legal/evidence export package; multi-account; RBAC;
encrypted remote backups; monitoring dashboard.

## 11. Non-goals
Not a Telegram client; not a replacement; never sends messages; never gives third
parties account access; no SaaS without a separate security model; never deletes local
data on Telegram deletion; never ranks data as "garbage" at ingest; never uses Bot API
as the primary access method (user-account archive required).

## 12. Data entities
Account, Chat, Participant, ChatParticipant, **Message**, **MessageVersion**,
**MessageEvent**, **Media**, **ExtractedText**, **Entity**, **EntityMention**,
**SearchIndexStatus** — full field lists per the source spec. Key invariants:
`chat.priority_override` affects UI only, never storage. `Message` carries
`is_deleted_in_telegram`, `deleted_detected_at`, `first_seen_at`, `last_seen_at`,
`raw_json`. `MessageVersion` keeps every historical text + `content_hash`.
`MessageEvent.event_type` ∈ created/edited/deleted/pinned/unpinned/reaction_changed/
media_downloaded/media_failed. `Media` carries `sha256`, `perceptual_hash`,
`download_status` (pending/downloaded/failed/skipped/unavailable), `download_attempts`,
`last_download_error`.

## 13. Storage
Raw DB: SQLite (MVP) → PostgreSQL (production). Media: filesystem, **content-addressed
preferred** (`/archive/.../blob/sha256/ab/cd/<sha256>`) with mapping table → dedup,
integrity, immutable paths, easy backup. Search: PG FTS / SQLite FTS5 (MVP) →
Meilisearch/Typesense (v1.1) → Qdrant/Chroma/LanceDB (v1.2). Backup targets: external
SSD/NAS/restic/S3-compatible/B2/R2/MinIO — include DB dump, media blobs, config
(no secrets), encrypted session separately, manifest, hashes.

## 14. Ingest
Initial full sync (auth → dialogs → chat meta → participants → per-chat history → save
every message + raw JSON → queue+download media → retries → FTS index → report).
Incremental sync (track last id/date; new messages; edits; deletions where permitted;
new media; retry failed; refresh meta; update indexes). Real-time listener (new/edit/
delete/media/reactions/service — never deletes local content).
**Media policy:** download all, no semantic filtering; `MAX_MEDIA_SIZE_MB` is a
technical bandwidth/storage limit, not a value judgment — skipped items remain listed
as incomplete with reason and are re-downloadable.

## 15. Search
Filters: text/exact phrase/sender/chat/date range/media type/file name/mime/has
attachment|voice|document|link/edited/deleted-in-TG/forwarded/replies/amount/currency/
phone|email|url/language. Each result shows chat, sender, datetime, snippet, media
indicator, edit/delete status, viewer link, ±10/±50/±200 context, related attachments,
reply chain. Semantic search never *replaces* exact search; UI separates exact / fuzzy
/ semantic / AI-related. **No destructive ranking** — reorder yes, silently exclude no;
every page offers "show all / include low-confidence / include media-only / include
deleted / include untitled chats."

## 16. AI layer
AI may summarize/tag/cluster/extract entities/propose deal timelines/generate Obsidian
notes/answer with citations/suggest related chats. AI **may not** delete/skip ingest/
hide raw/overwrite original text/mark anything useless in storage. Every RAG answer
cites source messages, chat names, dates, senders, local links, confidence, and a
missing-context warning. Deal mode is an overlay only (money/jurisdiction/company/doc
mentions/keywords like договор, инвойс, оплата, подпись, дедлайн, нотариус, задаток,
акции, доли, agreement, invoice, payment, USDT/attachments/voice/repeat participants);
undetected chats remain fully archived and searchable.

## 17. Obsidian
Obsidian stores curated human-readable artifacts (daily/weekly digest, person/company/
deal/project pages, chat summary, voice transcript note, important attachment note,
task note) — **not** the raw archive. YAML frontmatter (source, archive_id, chat_id,
chat_title, date_range, participants, entities, local_archive_url, generated_at); every
claim links back to local archive messages. Export modes: manual/scheduled/saved-search/
entity-based/project-based.

## 18. Web viewer
Screens: dashboard, chat list, chat timeline, search, media library, voice transcripts,
documents, entities, deals/timelines, sync status, backup status, settings, audit log.
Dashboard counters incl. deleted-preserved, edited-preserved, voice awaiting
transcription, OCR queue, failed downloads, incomplete chats, backup status. Timeline
feels like Telegram (bubbles, dates, inline media, replies, forwards, albums, service,
edit indicator, **deleted-in-Telegram + local-only-preserved badges**). Message detail:
raw+HTML text, sender/chat/TG id/date, edit history, deletion status, reply chain,
forward meta, media meta, raw JSON, hashes, local paths, extracted text, entities, AI
notes, backlinks.

## 19. Backup & integrity
Modes: manual/scheduled/pre-upgrade/external-disk/encrypted-remote. Content: DB dump,
media blobs, search index or rebuild instructions, config, version + hash manifest,
logs, migration status; session backed up separately + encrypted. Integrity: verify DB
↔ files, verify SHA256, list missing/orphan/failed/incomplete/metadata-without-file/
untextindexed/broken-reply. Restore is documented + testable on a clean machine.

## 20. Security
Session file = account password: never in Git (`*.session` ignored), stored under
`/data/session`, optional at-rest encryption, encrypted separate backup, locked perms,
UI warning. Viewer binds `127.0.0.1` by default; remote only behind reverse
proxy+HTTPS+strong auth+VPN(Tailscale/WireGuard)+IP allowlist+audit logs. Auth: local
admin (MVP) → multi-user, per-chat perms, 2FA, session timeout, audit (v1.1). Secrets in
`.env` only (API id/hash, sessions, DB pw, encryption keys, backup creds).

## 21. Compliance / evidence
"Evidence package export": selected chat/date range → messages + attachments +
timestamps + participants + hash manifest + export timestamp + system version + source
metadata + non-modification statement + JSON & human-readable PDF/HTML. No legal
certification claim, but enough metadata for later review/reconstruction.

## 22. Architecture
Docker services: `archiver` (sync/jobs/media/retry), `listener` (realtime events),
`viewer` (UI/search/media/admin), `postgres`, `redis` (queue/cache), `worker` (OCR/
transcription/extraction/indexing), `search` (Meili/Typesense), `vector` (Qdrant/
Chroma/LanceDB), `backup` (restic/rclone). MVP = archiver + viewer + sqlite/postgres +
media folder.
API (MVP): `GET /api/chats`, `/api/chats/:id/messages`, `/api/messages/:id`,
`/api/messages/:id/context`, `/api/search`, `/api/media/:id`, `/api/sync/status`,
`POST /api/sync/run`, `POST /api/backup/run`, `GET /api/integrity/status`,
`POST /api/export/jsonl`, `POST /api/export/obsidian`. v1.2 AI: `/api/rag/search`,
`/api/rag/answer`, `/api/entities/extract`, `/api/entities/:id/timeline`,
`/api/deals/:id/timeline`.

## 23. Fork modification plan (phases)
- **Phase 0 — fork & audit** (done; see AUDIT.md).
- **Phase 1 — Safe archive mode:** `SAFE_ARCHIVE_MODE=true` → TG deletions never delete
  local rows; edits create versions; media never auto-removed; failed media stays in
  retry queue; sync logs skipped/incomplete; UI shows preserved-deleted. AC: delete in
  TG → original kept, marked deleted, still searchable, integrity shows preserved.
- **Phase 2 — Message versioning:** `message_versions`; v1 on first capture, +1 per
  edit; UI compare; search current + historical; API exposes history. AC: edit 3× →
  3 versions, exact search finds old text.
- **Phase 3 — Media hardening:** content-addressed storage + SHA256 + dedup + retry
  queue + missing/orphan reports + media manifest + configurable max size with visible
  skipped status. AC: dup stored once, two refs; deleted TG media stays local; failures
  visible in dashboard.
- **Phase 4 — Search hardening:** exact/phrase/filters/deleted/edited-version/media-only
  + ±N context + export selection. AC: find short, deleted, old-edited text; open ±50.
- **Phase 5 — Backup/restore:** local + external-path backup; DB dump; media manifest;
  hash verify; restore script; integrity checker. AC: backup → wipe → restore on clean
  machine → integrity passes.
- **Phase 6 — Voice/document intelligence:** transcription (voice/audio), OCR (image/
  PDF), DOCX/XLSX/PDF extraction, indexed extracted text, UI badges. AC: find phrase
  said in voice; find text in screenshot/PDF/DOCX.
- **Phase 7 — Obsidian/RAG:** markdown templates, digests, entity/deal/project pages,
  local vector index, RAG API with citations + backlinks. AC: archive answer cites
  source messages; generated note has backlinks.

## 24. Config (.env additions)
`APP_NAME=telegram-sovereign-archive`, `SAFE_ARCHIVE_MODE=true`,
`DELETE_LOCAL_ON_TELEGRAM_DELETE=false`, `MEDIA_STORAGE_MODE=content_addressed`,
`MAX_MEDIA_SIZE_MB`, `RETRY_FAILED_MEDIA=true`, `ENABLE_FULL_TEXT_SEARCH=true`,
`ENABLE_VECTOR_SEARCH=false`, `ENABLE_TRANSCRIPTION=false`, `ENABLE_OCR=false`,
`ENABLE_OBSIDIAN_EXPORT=false`, `VIEWER_BIND_HOST=127.0.0.1`, `BACKUP_ENABLED=true`,
`BACKUP_ENCRYPTION=true` (plus upstream TG/DB vars). `SAFE_ARCHIVE_MODE` is the master
guardrail and overrides any destructive flag.

## 25. CLI (`tsa`)
`init`, `auth`, `sync --all|--chat <id>`, `listen`, `media retry|verify`,
`search "q"`, `export jsonl --chat <id>`, `export evidence --chat <id> --from --to`,
`obsidian export --mode weekly`, `backup run|verify`, `restore`, `integrity check`.

## 26. MVP acceptance
Archives all accessible cloud chats; downloads media + records failures; incremental
sync; **preserves deleted TG messages locally**; **preserves edited versions**; exact
FTS; viewer context; local-only (no public exposure); backup/restore docs; integrity
report; **never classifies data as garbage at ingest**; exports raw to JSONL.

## 27. Risks
TG API limits (incremental + backoff + per-chat queue + resume); session compromise
(local-only + perms + encryption + no Git + no SaaS + UI warning); incomplete media
(retry queue + visible status + report); secret chats unavailable (device-specific,
out of MVP); GPL constraints (internal fine; reimplement for closed-source); AI
hallucination (citations mandatory; exact search primary; no AI deletion/filtering).

## 28. Epics
A archive safety · B media reliability · C search · D viewer · E backup · F
intelligence · G Obsidian.

## 30. Definition of done
User can: start locally with Docker; auth; sync all; see chats in viewer; search any
message; open with context; confirm deleted-TG messages remain; confirm edited versions
kept; find voice after transcription; find text in docs/screenshots; export evidence
package; backup+restore on another machine; use as Obsidian/RAG source without losing
raw data.

## 31. Mantra
Telegram is the raw memory layer of business and life. Preserve first; index second;
summarize third; never discard; never trust AI without sources; never let a Telegram
deletion destroy local truth.
