# CuWatch Server — Copilot Instructions

These guidelines help AI coding agents work effectively in this repo. Focus on the existing patterns and workflows below; avoid introducing new frameworks or conventions.

## Architecture & Data Flow
- Services: `mqtt-broker` (Mosquitto), `db` (Postgres 16), `bridge` (MQTT→DB), `web` (Flask UI/APIs/WebSockets).
- Ingest: Devices publish JSON to `telemetry/<device_id>` and optionally `status/<device_id>`. The `bridge` subscribes and writes typed rows to Postgres.
- UI/API: The `web` app serves templates and JSON, streams live MQTT messages at `/ws`, and publishes device control messages to `control/<device_id>/set`.
- Key files: [services/bridge/bridge.py](../../services/bridge/bridge.py), [services/web/app.py](../../services/web/app.py), [services/web/models.py](../../services/web/models.py), [services/web/mqtt_subscribe.py](../../services/web/mqtt_subscribe.py), [services/web/mqtt_publish.py](../../services/web/mqtt_publish.py), [docker-compose.yml](../../docker-compose.yml).

## Telemetry Conventions (stored as typed columns)
- Topics: `telemetry/<device_id>`, optional `status/<device_id>`.
- Timestamps: Prefer `ts` (ISO8601/epoch). `timestamp`/`end_time` also accepted. If only `dt` (ms) is sent, devices should announce a base time once via `{ run_base_ts | run_start_ts | run_start }`.
- `dt` is optional; if absent/invalid, the bridge stores `0`.
- Deterministic ordering: API uses `ts DESC, muon_count DESC` for samples; CSV export uses `ts ASC, muon_count ASC`.
- The bridge computes instantaneous and EMA rate (Hz) from `dt` and stores it in `devices.meta.metrics`.

## Database & Models
- Tables: `devices(id, last_seen, online, device_number, meta)`, `samples(...)`, `runs(device_id, base_ts, run_key, meta)`.
- ORM vs Core: Web uses Flask-SQLAlchemy ORM ([models.py](../../services/web/models.py)); the bridge uses SQLAlchemy Core with Postgres upsert ([bridge.py](../../services/bridge/bridge.py)). Keep schemas in sync across both.
- `meta` is a JSON string in DB for compatibility; the web app tolerates `str|dict` when returning JSON.

## Web App Patterns
- App factory `create_app()` in [app.py](../../services/web/app.py) wires DB, WebSockets, and MQTT live subscriber.
- Auth: Simple session login. If `LOGIN_USER`/`LOGIN_PASSWORD` env vars are set, enforce fixed credentials; otherwise any non-empty username works (dev).
- WebSockets: `/ws` uses `flask-sock` sending recent snapshot then streaming new MQTT payloads ([sockets.py](../../services/web/sockets.py)).
- MQTT publish: Use `publish_control()` to send JSON to `control/<device_id>/set` with QoS 1; default `retain=False` in API ([mqtt_publish.py](../../services/web/mqtt_publish.py)).
- APIs (see [app.py](../../services/web/app.py)):
  - `GET /api/devices`, `GET /api/device/<id>/meta`, `GET /api/device/<id>/runs`
  - `GET /api/samples/<id>?limit&start&end` (limit up to 50k; ISO8601 or epoch)
  - `GET /api/export/<id>.csv?start&end` (streams CSV)
  - `POST /api/control/<id>` (validates `threshold` 0..4095; requires login)

## Bridge Patterns
- Subscribes to `telemetry/#` and `status/+`, writes rows, and upserts `devices` and `runs`.
- Timestamp parsing accepts ISO8601 (with optional `Z`) and epoch sec/ms; values before year 2000 are treated as invalid and ignored.
- If only `dt` present, bridge reconstructs `ts` from `devices.meta.current_run.base_ts`.

## Local Dev & Run
- Primary workflow: Docker Compose with hot-reload for `web`.
  - Start: 
    ```bash
    docker compose up --build
    ```
  - Web binds to host port `80`; health at `/healthz`. Postgres is on `127.0.0.1:5432`.
  - Compose watches `services/web` (excluding `templates/**`) to sync and restart; templates sync without restart for fast Jinja updates.
- Gunicorn worker: `eventlet` with 1 worker (stable WebSockets). If changing sockets behavior, keep `eventlet` compatibility.
- Env vars of interest: `DATABASE_URL`, `MQTT_BROKER_URL`, `MQTT_BROKER_PORT`, optional `MQTT_USERNAME`/`MQTT_PASSWORD`, `SECRET_KEY`, optional `LOGIN_USER`/`LOGIN_PASSWORD`.

## Adding Features (follow these patterns)
- New API endpoint: implement in [app.py](../../services/web/app.py), return JSON via Flask, and keep deterministic ordering consistent with existing endpoints.
- Schema changes: update both [models.py](../../services/web/models.py) and the Core schema in [bridge.py](../../services/bridge/bridge.py); avoid silent divergence.
- New telemetry fields: extend `samples` table (both sides) and parsing in the bridge; prefer explicit types; ensure CSV and JSON endpoints include the new fields.
- Live stream additions: adapt [mqtt_subscribe.py](../../services/web/mqtt_subscribe.py) to push extra context if needed; the WebSocket sends raw JSON strings.
- Control messages: extend the `POST /api/control/<id>` validator and publish payload via `publish_control()`; document accepted payloads in README.

## Examples
- Publish test telemetry to the broker (dev):
  ```bash
  mosquitto_pub -h localhost -t telemetry/dev-001 -m '{"device_number":3,"ts":"2025-08-27T14:30:12Z","muon_count":42,"adc_v":1234,"temp_adc_v":987,"dt":1000,"wait_cnt":7,"coincidence":true}'
  ```

When unsure, mirror the closest existing pattern rather than inventing new ones.
