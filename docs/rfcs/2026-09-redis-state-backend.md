# RFC 003 — Redis state backend (Profile 4 in flight)

| Field | Value |
| --- | --- |
| **Author** | Mavis (Sprint 4 of v0.3.0 RFC 002) |
| **Status** | **Implemented** (2026-09-07) |
| **Target** | OmniScribe v0.3.1+ (Profile 4 multi-worker LAN deployment) |
| **Audit refs** | §6 deferred capabilities, Phase 4 follow-up, D2-02 |

## Current status and action validity (2026-09-30)

The Redis state backend is implemented in `plugins/state_backend_redis.py`;
`StateBackendSchema` accepts `redis`, and the registration site opens the
backend at boot. RFC 004 subsequently added distributed dispatch in
`plugins/jobs_redis.py`, the `omniscribe-worker` CLI, and Redis Pub/Sub progress
fan-out in `plugins/progress.py`. The original non-goals below describe this
RFC's initial scope; they do not describe missing features in the current app.

`scripts/dev_redis_smoke.py` checks API health and Redis keys by default.
Opt in with `--jobs 4 --verify-recovery --timeout 600` to submit concurrent
translation jobs, verify token-bound HTTP/Redis artifacts and observe the same
job moving to a different worker before completion. Restart the identified
worker at the printed cue; the script does not control deployment processes.
Export the deployment's `OMNISCRIBE_AUTH_TOKEN` for authenticated API requests;
the smoke rejects redirects before forwarding credentials or artifact tokens.
Executing this smoke against a real deployment and recording a measured
production profile remain open in [outstanding-work.md](../outstanding-work.md#release-and-deployment-decisions).
TLS is supported through `REDIS_TLS` / `OMNISCRIBE_REDIS_TLS`; credentials can
be supplied through the Redis URL. Select deployment settings rather than
adding those already-supported capabilities. No fresh runtime validation was
performed for this deployment profile.

Sections 1–11 retain the original design and implementation plan. Use the
[architecture ledger](../ARCHITECTURE.md) and
[deployment guide](../DEPLOYMENT.md) for current runtime ownership and recipes.

## 1. Background

v0.2.0 shipped the `MemoryStateBackend` and `SQLiteStateBackend` as the
two persistence options. `OMNISCRIBE_STATE_BACKEND=redis` is in the
config schema and the `redis>=8.1.0` base dep is already in
`pyproject.toml`, but the `Literal["memory", "sqlite"]` in
`StateBackendSchema` rejects `redis` at boot:

```python
raise ValueError(
    "state backend must be one of "
    f"{sorted(_ALLOWED_BACKENDS)} in this build, got {backend_name!r} "
    "(redis support ships in a follow-up)"
)
```

This RFC closes that gap. Profile 4 (multi-worker LAN deployment)
needs shared state across workers — `memory` doesn't survive a
restart, and `sqlite` doesn't survive a multi-worker read-write
contention pattern. Redis is the right tool for that profile.

## 2. Goals

1. **`OMNISCRIBE_STATE_BACKEND=redis` boots successfully.** No more
   `ValueError` at plugin apply.
2. **All 15 `StateBackend` Protocol methods are implemented** with
   correct semantics (atomicity where the SQLite impl is atomic,
   TTL where the SQLite impl is TTL-aware).
3. **Multi-worker safe.** Two workers can `put_artifact` /
   `get_job` / `consume_channel` concurrently without losing
   updates or double-consuming channels.
4. **Tests with `fakeredis`.** `fakeredis>=2.0` is already a dev
   dep. Tests use it to simulate Redis in-process.
5. **End-to-end with real Redis.** A `make redis-smoke` target (or
   a docs recipe) boots the server with `OMNISCRIBE_STATE_BACKEND=redis`
   pointing at a local Redis, submits a job, and verifies state
   survives a worker restart.

## 3. Non-goals

- **No Celery.** The existing in-process `JobQueue` continues to
  dispatch jobs to the local worker. Redis is *just* the shared
  state store. Celery integration is a separate, larger effort.
- **No pub/sub for progress frames.** Progress frames still go
  through the local WebSocket. Redis pub/sub for cross-worker
  progress is a follow-up.
- **No cluster / sentinel mode.** Single-Redis deployments only.
  Cluster mode is a follow-up.
- **No Redis Streams for the job queue.** The job queue stays
  in-process. Redis Streams integration is a follow-up.

## 4. Data model

All keys are prefixed `omniscribe:`. The total key count per
deployment is bounded by the number of in-flight jobs + artifacts +
channels, so even a small Redis instance handles Profile 4.

| Key | Type | TTL | Purpose |
| --- | --- | --- | --- |
| `omniscribe:artifact:{id}` | STRING (JSON) | `ttl_seconds` | `ArtifactRecord` |
| `omniscribe:artifact:blob:{id}` | STRING (bytes) | `ttl_seconds` | `ArtifactBlob.blob` |
| `omniscribe:job:{id}` | STRING (JSON) | none | `JobRecord` (terminal jobs persist) |
| `omniscribe:channel:{id}` | STRING (JSON) | `ttl_seconds` | `ChannelRecord` |

Indexes (for `list_jobs` and `prune_expired_*`):

| Key | Type | Purpose |
| --- | --- | --- |
| `omniscribe:job:index` | ZSET (score = `created_at`) | `list_jobs(limit, offset)` pagination by `created_at` desc |
| `omniscribe:channel:index` | ZSET (score = `created_at + ttl_seconds`) | `prune_expired_channels` — `ZRANGEBYSCORE 0 now` returns expired channels |

`prune_expired_artifacts` doesn't need an index — Redis's built-in
TTL on the key does the work. A `SCAN` pass finds keys that *should*
have expired (operator-set TTL was wrong), or the operator can
rely on Redis's expiration policy alone.

## 5. Method-by-method

### Artifacts

- `put_artifact(*, id, token, owner_job_id, content_type, blob, ttl_seconds)`
  — `SETEX omniscribe:artifact:{id} ttl_seconds <json>` +
  `SETEX omniscribe:artifact:blob:{id} ttl_seconds <bytes>`.
  Both keys share the same TTL so they expire together.
- `get_artifact(id, token) -> ArtifactBlob | None`
  — `GET` both keys. If either is missing, return None.
  If the metadata's `token` field doesn't match the supplied
  token, return None (the SQLite impl does the same check).
- `delete_artifact(id) -> None` — `DEL` both keys.
- `prune_expired_artifacts(now) -> int`
  — `SCAN MATCH omniscribe:artifact:*` and delete keys whose
  `PTTL` is `-1` (TTL was never set, or was set to 0). Returns
  the count of deleted keys. (Redis normally expires keys
  automatically; this is the safety net for malformed data.)

### Jobs

- `upsert_job(record) -> None`
  — `SET omniscribe:job:{id} <json>` (no TTL — terminal jobs
  persist until `clear_jobs` / `delete_job`).
  `ZADD omniscribe:job:index <created_at> <id>`.
- `get_job(job_id) -> JobRecord | None` — `GET`.
- `list_jobs(*, limit, offset) -> list[JobRecord]`
  — `ZREVRANGE omniscribe:job:index offset offset+limit-1` then
  `MGET`. Build `JobRecord` objects in order.
- `clear_jobs() -> int` — `SCAN MATCH omniscribe:job:*` + `DEL` +
  `DEL omniscribe:job:index`. Returns the count.
- `delete_job(job_id) -> None` — `DEL key` + `ZREM index`.

### Progress channels

- `put_channel(channel_id, session_token, job_id, ttl_seconds)`
  — `SETEX omniscribe:channel:{channel_id} ttl_seconds <json>`.
  `ZADD omniscribe:channel:index <created_at + ttl_seconds> <channel_id>`
  (the score is the expiration time, so `prune_expired_channels`
  is a single `ZRANGEBYSCORE` call).
- `get_channel(channel_id) -> ChannelRecord | None` — `GET`.
- `consume_channel(channel_id, session_token) -> ChannelRecord | None`
  — atomic read-and-mark. Two patterns:
  1. **Lua script** (preferred): single round-trip, server-side
     atomicity. The script returns the record on success, or nil
     if missing / wrong token / already consumed.
  2. **WATCH / MULTI / EXEC**: client-side optimistic lock.
     More chatty; only used if the Lua path is unavailable
     (e.g. fakeredis version mismatch).
  This RFC ships option 1.
- `delete_channel(channel_id) -> None` — `DEL key` + `ZREM index`.
- `prune_expired_channels(now) -> int` — `ZRANGEBYSCORE
  omniscribe:channel:index 0 now` returns expired channel ids;
  for each, `DEL` the key and `ZREM` from the index. Returns
  the count.

### Lifecycle

- `aclose() -> None` — `await redis.aclose()` (closes the
  connection pool). The plugin's `ctx.effect(backend.aclose)`
  wiring handles this.

## 6. Connection management

```python
import redis.asyncio as redis_async


class RedisStateBackend:
    def __init__(self, redis_url: str) -> None:
        self._redis = redis_async.from_url(
            redis_url, encoding="utf-8", decode_responses=False
        )
```

`from_url` constructs a `Redis` client backed by a connection
pool. The pool is closed in `aclose()`. We use `decode_responses=False`
because artifact blobs are raw bytes; the JSON metadata is
decoded explicitly with `json.loads`.

The `REDIS_URL` is read from `omniscribe.config.load_settings().redis_url`
(defaults to `redis://localhost:6379/0`). For Profile 4 deployments
on a LAN, the operator overrides via env:
`REDIS_URL=redis://10.0.0.5:6379/0`.

## 7. Cordis wiring

The Cordis plugin row already exists:

```yaml
- id: state_backend
  use: omniscribe.plugins.state_backend:plugin
  config:
    backend: ${OMNISCRIBE_STATE_BACKEND:-sqlite}
    sqlite_path: ${OMNISCRIBE_STATE_DB_PATH:-}
```

The plugin's `apply()` adds a new branch:

```python
if backend_name == "redis":
    from .state_backend_redis import RedisStateBackend

    redis_url = settings.redis_url
    backend = RedisStateBackend(redis_url=redis_url)
    await backend.open()  # ping the server, raise if unreachable
    _LOGGER.info("state backend redis url=%s", _redact_url(redis_url))
```

`open()` is added to the Protocol (no-op for the existing memory /
sqlite impls, pings Redis for the redis impl). The plugin
bootstraps with the URL, and a failed ping raises a `RuntimeError`
so the operator sees a clear "Redis unreachable" error instead of
silent broken behavior.

## 8. Schema + literal update

```python
class StateBackendSchema(BaseModel):
    backend: Literal["memory", "sqlite", "redis"] = "memory"
    sqlite_path: str = ""


_ALLOWED_BACKENDS = {"memory", "sqlite", "redis"}
```

## 9. Tests

- `tests/plugins/test_state_backend_redis.py` — fakeredis-based.
  Coverage:
  - All 15 methods have at least one direct test
  - Round-trip put/get/delete for each domain
  - TTL behavior (fakeredis honors TTL on `SETEX`)
  - `consume_channel` atomicity: open the channel, call
    `consume_channel` twice, second call returns None
  - `prune_expired_channels(now)` returns the count of channels
    whose `created_at + ttl_seconds <= now`
  - `list_jobs` pagination: 10 jobs, fetch limit=3 offset=0,
    then limit=3 offset=3, then limit=3 offset=9
  - `clear_jobs` removes the index too
- `tests/plugins/test_state_backend_redis_concurrent.py` —
  optional, only if fakeredis supports it; simulate two
  `RedisStateBackend` instances against the same fakeredis
  server and verify a `put_job` from one is visible to the other.
- Existing `test_state_backend_plugin.py` updated to add a third
  path: `OMNISCRIBE_STATE_BACKEND=redis` with a fakeredis
  override. The Cordis boot, harness mount, and `aclose`
  lifecycle are exercised end-to-end.

## 10. End-to-end smoke

The implemented recipe is `scripts/dev_redis_smoke.py`, run against an
already-started API and Redis. It checks `/api/health` and lists the
`omniscribe:*` keyspace. The planned `.sh` recipe does not exist. Job submission,
artifact verification and restart recovery remain separate verification work;
an empty keyspace currently counts as a successful boot probe.

## 11. Staging

| Stage | What | Commit |
| --- | --- | --- |
| 1 | Skeleton + literal allowlist + plugin branching | small |
| 2 | Read methods (`get_artifact`, `get_job`, `get_channel`, `list_jobs`) | small |
| 3 | Write methods (`put_artifact`, `upsert_job`, `put_channel`) with TTL | small |
| 4 | Delete + prune methods | small |
| 5 | `consume_channel` Lua atomic script | small |
| 6 | Tests with fakeredis | medium |
| 7 | Wire + docs + design RFC | small |
| 8 | End-to-end manual smoke (not committed; user-runnable) | n/a |

## 12. Open questions

- **Profile 4 deployment scale.** How many workers? How many
  jobs/second? Affects whether we need a Redis Cluster setup
  or a single instance is enough.
- **Migration path from sqlite.** Is there an existing Profile
  4 deployment using sqlite that needs to migrate to redis
  without losing job history? The utility already exists at
  [`scripts/migrate_sqlite_to_redis.py`](../../scripts/migrate_sqlite_to_redis.py),
  with regression coverage in `tests/scripts/test_migrate_sqlite_to_redis.py`.
  Inspect the real database with `uv run python scripts/migrate_sqlite_to_redis.py
  --sqlite-path <existing-state.db> --dry-run`, then verify the chosen migration
  against that deployment before switching backends. Keep the SQLite database
  as the rollback/source record; a fresh Redis deployment need not delete it.
- **Auth.** The `REDIS_URL` is plain by default. For Profile 4
  LAN deployment, select the required TLS/password configuration. The current
  schema supports TLS and a password in the URL
  (`redis://:password@host:port/0`); the user supplies these through
  `REDIS_TLS` / `OMNISCRIBE_REDIS_TLS` and `REDIS_URL`.

These don't block Stages 1-7. Stage 8 (manual smoke) is the
right place to surface them.
