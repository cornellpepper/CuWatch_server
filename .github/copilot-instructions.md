# CuWatch Server — Copilot Instructions

These guidelines help AI coding agents work effectively in this repo. Focus on the existing patterns and workflows below; avoid introducing new frameworks or conventions.

## Architecture & Data Flow
- **Services**: `mqtt-broker` (Mosquitto), `db` (Postgres 16), `bridge` (MQTT→DB), `web` (Flask UI/APIs/WebSockets).
- **Ingest path**: Devices publish JSON to `telemetry/<device_id>` and optionally `status/<device_id>`. The `bridge` subscribes and writes typed rows to Postgres, computing metrics and persisting run metadata.
- **UI/API/Control**: The `web` app serves templates and JSON APIs, streams live MQTT messages via `/ws`, and publishes device control commands to `control/<device_id>/set`.
- **Key files**: [services/bridge/bridge.py](../../services/bridge/bridge.py), [services/web/app.py](../../services/web/app.py), [services/web/models.py](../../services/web/models.py), [services/web/mqtt_subscribe.py](../../services/web/mqtt_subscribe.py), [services/web/mqtt_publish.py](../../services/web/mqtt_publish.py), [docker-compose.yml](../../docker-compose.yml).

## Telemetry & Timestamp Handling
- **Topics**: `telemetry/<device_id>` (required), `status/<device_id>` (optional heartbeat).
- **Timestamp priority** (in order of precedence): `ts` → `timestamp` → `end_time` (prefer ISO8601/RFC3339 with optional `Z`, or epoch seconds/milliseconds). If only `dt` (ms) provided, announce run base once via `{ run_base_ts | run_start_ts | run_start }`, then bridge reconstructs `ts = base + dt`.
- **Fallback**: Timestamps before year 2000 are invalid; bridge ignores them. If no absolute ts available, falls back to current time (`now`).
- **dt field**: Optional; if absent/invalid, bridge stores `0`.
- **Deterministic ordering**: API uses `ts DESC, muon_count DESC` for samples; CSV export uses `ts ASC, muon_count ASC`.
- **Rate metrics**: Bridge computes `inst_rate_hz = 1000.0 / dt_ms` and maintains EMA (smoothing factor 0.2) in `devices.meta.metrics.{inst_rate_hz, ema_rate_hz}`.

## Database & Models
- **Tables**: `devices(id, last_seen, online, device_number, meta)`, `samples(id, device_id, ts, device_number, muon_count, adc_v, temp_adc_v, dt, wait_cnt, coincidence)`, `runs(id, device_id, base_ts, run_key, meta)`.
- **ORM vs Core**: Web uses Flask-SQLAlchemy ORM ([models.py](../../services/web/models.py)); bridge uses SQLAlchemy Core with Postgres upsert ([bridge.py](../../services/bridge/bridge.py)). **Keep schemas in sync**—don't drift ORM vs Core definitions.
- **meta handling**: Stored as JSON string in DB for Core compatibility. Web app tolerates `str|dict` on retrieval, with fallback parsing via `json.loads()`. Bridge stores device meta as JSON-serialized dict containing `current_run: {base_ts, id}` and `metrics: {inst_rate_hz, ema_rate_hz}`.

## Web App Patterns
- **App factory**: `create_app()` in [app.py](../../services/web/app.py) initializes DB, WebSockets via `flask-sock`, and MQTT live subscriber thread.
- **Auth**: Session-based. If `LOGIN_USER`/`LOGIN_PASSWORD` env vars set, enforces exact match; otherwise dev mode allows any non-empty username.
- **WebSockets** at `/ws`: Sends recent 50-message snapshot first, then streams new MQTT payloads with 1s timeout heartbeat. Uses `LiveStream` buffer (default 2000 messages) ([sockets.py](../../services/web/sockets.py)).
- **MQTT publish**: Use `publish_control(device_id, payload, retain=True, qos=1)` to send JSON to `control/<device_id>/set`. API calls use `retain=False` ([mqtt_publish.py](../../services/web/mqtt_publish.py)).
- **APIs** ([app.py](../../services/web/app.py)):
  - `GET /api/devices` — lists all with online status (5-min last_seen threshold).
  - `GET /api/device/<id>/meta` — device metadata including current run base and metrics.
  - `GET /api/device/<id>/runs` — lists run records (base_ts, run_key, meta) ordered by recency.
  - `GET /api/samples/<id>?limit=500&start=...&end=...` — samples in descending ts order; max 50k rows.
  - `GET /api/export/<id>.csv?start=...&end=...` — streams CSV (generator-based for memory efficiency); ascending ts order.
  - `POST /api/control/<id>` — JSON payload; validates `threshold` as 12-bit int [0..4095]; requires auth.
  - `GET /healthz` — simple health probe.

## Bridge Patterns
- **Subscriptions**: `telemetry/#` (processes and stores samples) and `status/+` (updates last_seen/online only).
- **Timestamp parsing**: Accepts ISO8601 (with optional `Z` suffix) and epoch sec/ms; silently rejects pre-2000 values.
- **Run base announcement**: Accepts `run_base_ts`, `run_start_ts`, or `run_start` keys; stores in device `meta.current_run` and upserts `runs` table with `(device_id, base_ts)` unique constraint.
- **Metric computation**: For each telemetry, if `dt` present, calculates rate and updates EMA with alpha=0.2; stores in `devices.meta.metrics`.
- **Sample row construction**: Validates required fields via `to_int()` / `to_bool()`; raises `ValueError` if missing/invalid, causing message skip and stderr log.
- **Upsert strategy**: Uses Postgres `insert(...).on_conflict_do_update()` for idempotent `devices` and `runs` updates.

## Local Dev & Run
- **Quick start**:
  ```bash
  docker compose up --build
  # Then visit http://localhost
  ```
- **Compose features**: Watches `services/web` (excluding `templates/`, `__pycache__/`, `.venv/`); syncs code with restart. Templates sync without restart (Jinja auto-reload).
- **Services**:
  - `web`: Gunicorn (eventlet, 1 worker) on port 80; health check at `/healthz`.
  - `bridge`: Processes MQTT→DB; waits up to 120s for DB before starting.
  - `db`: Postgres 16 on `127.0.0.1:5432` (localhost only); ephemeral by default.
  - `mqtt-broker`: Mosquitto on port 1883.
- **Key env vars**: `DATABASE_URL`, `MQTT_BROKER_URL`, `MQTT_BROKER_PORT`, `MQTT_USERNAME` (optional), `MQTT_PASSWORD` (optional), `SECRET_KEY`, `LOGIN_USER` (optional), `LOGIN_PASSWORD` (optional).
- **Logs**: Use `docker compose logs -f <service>` to tail individual services.

## Adding Features (follow these patterns)
- **New API endpoint**: Add to [app.py](../../services/web/app.py); return JSON via `jsonify()`; maintain deterministic ordering (ts desc/asc, tie-breaker muon_count).
- **Schema changes**: Update both [models.py](../../services/web/models.py) ORM and [bridge.py](../../services/bridge/bridge.py) Core schema; avoid silent drift.
- **New telemetry fields**: Extend `samples` table (both ORM + Core); add parsing in bridge's `to_int()` / `to_bool()`; include in CSV header and JSON endpoints.
- **Live stream additions**: Enhance [mqtt_subscribe.py](../../services/web/mqtt_subscribe.py) `on_message()` to push extra fields; WebSocket sends raw JSON strings.
- **Control messages**: Validate in `POST /api/control` and publish via `publish_control()`; document accepted payloads in README.
- **Error handling**: Bridge logs to stderr and skips malformed messages; web returns JSON `{"error": "..."}` with HTTP status.

## Examples
- Publish test telemetry:
  ```bash
  mosquitto_pub -h localhost -t telemetry/003 -m '{"device_number":3,"ts":"2025-08-27T14:30:12Z","muon_count":42,"adc_v":1234,"temp_adc_v":987,"dt":1000,"wait_cnt":7,"coincidence":true}'
  ```
- Announce run base (relative timestamps only):
  ```bash
  mosquitto_pub -h localhost -t telemetry/003 -m '{"run_start_ts":"2025-08-27T14:00:00Z"}'
  ```

## Gotchas & Important Notes
- **ORM/Core drift**: Bridge uses Core, web uses ORM. If you add a column to ORM, add it to Core's `samples` table definition in bridge.py or data won't persist.
- **Meta as string**: Bridge stores `meta` as JSON string; web app must parse if needed. Don't assume dict.
- **Timestamp precedence**: ts > timestamp > end_time > (dt + base_ts) > now. Chain is important for devices with only relative times.
- **Eventlet compatibility**: WebSocket implementation depends on eventlet worker; don't switch to sync worker without testing sockets.
- **Templates hot-reload**: Only applies to Jinja files; Python code changes still require web restart.

When unsure, mirror the closest existing pattern rather than inventing new ones.
