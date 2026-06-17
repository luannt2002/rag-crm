# Streaming Upload — partner contract

Endpoint: `POST /api/ragbot/documents/upload-stream`

The JSON-body `POST /documents/create` endpoint expects a `source_url`
the worker can fetch. For partner BE flows where the bytes only exist on
the caller side — large compliance binders, scanned PDF archives, multi-
megabyte training corpora — use this chunked-upload variant. The server
reads the body chunk-by-chunk and writes each chunk straight to a temp
file, so resident memory peak is bounded by the read window regardless
of file size.

## Limits

| Knob | Value |
|---|---|
| Max body | 500 MiB (`DEFAULT_UPLOAD_STREAM_MAX_BYTES`) |
| Read chunk | 1 MiB (`DEFAULT_UPLOAD_STREAM_CHUNK_SIZE`) |
| Resident memory peak | ~1 MiB per concurrent upload |

Body above the cap returns `413 PAYLOAD_TOO_LARGE` and the temp file is
unlinked before the response is sent. Disk-fill DoS protection.

## Request

```http
POST /api/ragbot/documents/upload-stream
Authorization: Bearer <jwt-with-tenant-claim>
Content-Type: multipart/form-data; boundary=----WebKitFormBoundaryXyz
```

Form fields:

| Field | Required | Notes |
|---|---|---|
| `bot_id` | yes | tenant slug, ≤64 chars |
| `channel_type` | yes | opaque channel string, ≤32 chars |
| `document_name` | yes | display name, ≤255 chars |
| `workspace_id` | no | slug fallback = `str(record_tenant_id)` |
| `mime_type` | no | falls back to part `Content-Type` |
| `language` | no | default `vi` |
| `file` | yes | binary part — the document bytes |

Tenant identity is taken **only** from the JWT bearer claim
(`record_tenant_id`) and never from the body. The 4-key tuple
`(record_tenant_id, workspace_id, bot_id, channel_type)` must resolve
to an existing bot or the route returns 404.

## Response — 202 ACCEPTED

```json
{
  "ok": true,
  "document_id": "8a7b5c2e-9b1f-4f5d-9c8e-b4f9c2a1e7d3",
  "state": "uploading",
  "bytes_received": 524288000,
  "trace_id": "trace-abcdef"
}
```

Poll `GET /api/ragbot/jobs/<document_id>` for the worker outcome
(chunked, embedded, parser-rejected, etc.). The `state="uploading"`
discriminator separates this path from the synchronous URL ingest.

## Error matrix

| HTTP | Detail | Action |
|---|---|---|
| 401 | `missing tenant context` | refresh JWT |
| 403 | RBAC level below admin (60) | promote caller |
| 404 | `bot_not_found` | check 4-key |
| 413 | `payload_too_large` | split file or contact ops for cap lift |
| 422 | `empty_file` / `filename_missing` / workspace slug invalid | fix payload |
| 500 | `upload_write_failed` / `upload_tempdir_unavailable` | retry; alert ops |

## Retry semantics

* **Network drop mid-upload**: server unlinks the partial temp file in
  the request's `finally` block. Re-POST the full body.
* **202 received, no `/jobs/<id>` ever shows up**: Redis was unavailable
  at hand-off time. The temp file survives on disk; an admin
  orphan-cleanup cron sweeps stale temp files every 24h. Re-POST to
  surface a new `document_id` bound to a fresh Stream entry.
* **413**: not retryable as-is. Use the partner-side chunking helper
  (split + multiple `document_name`s) until the platform exposes
  resumable uploads (P2 roadmap).

## Privacy guarantees

* Temp filenames are UUID4 hex — no tenant or bot slug ever appears in
  the filesystem path.
* File content is never copied into log lines or Redis Stream payloads.
  The worker-handoff message carries only the path pointer + 4-key
  identity + byte count + filename metadata.
* Temp file mode is `0o600` (owner-only).
