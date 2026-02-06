# CuWatch Server implementation

The Pepper board sends events to this server, which displays the data, makes it available for download, and is used to control the Pepper boards.

Flask + WebSockets + Mosquitto + Postgres. Production Gunicorn worker is **eventlet** (stable WebSockets).
Telemetry stored as typed columns; dashboard shows a **rolling event rate (Hz)** computed from absolute timestamps.

## Telemetry topics & payload

- Publish **telemetry** to: `telemetry/<device_id>`
- Optional **status**/heartbeat: `status/<device_id>`
- Control (from web app): `control/<device_id>/set` (retain=false via API)

Pico W client compatibility:
- Device ID format: Pico W code publishes to zero-padded numeric IDs (e.g., `telemetry/003`). The server accepts any string as `device_id`; both `dev-001` and `003` are fine.
- Telemetry fields: Pico W sends `device_number, muon_count, adc_v, temp_adc_v, ts, wait_cnt, coincidence` and may include `t_ms` (ignored by server). `dt` is optional and can be omitted.
- First-event run metadata: The first event of a run may include `run_start` (ISO8601) plus `baseline, threshold, reset_threshold, is_leader` to seed server-side run metadata. These fields are typically sent once per run and ignored on later events.
- Run base announcement: On the first event, Pico W may include `run_start` as ISO8601; the server accepts `run_base_ts | run_start_ts | run_start`.
- Status: Pico W publishes `status/<device_id>` with a JSON payload (e.g., `rate, muon_count, threshold, reset_threshold, baseline, runtime`); the server uses it for liveness only.
- Control: The device subscribes to `control/<device_id>/set` and honors JSON commands `{ "threshold": int }`, `{ "reset_threshold": int }`, `{ "new_run": true }`, `{ "shutdown": true }`, `{ "make_leader": true|false }`. `new_run` asks the device to begin a fresh run and choose a new base timestamp/run key; legacy string commands `"shutdown"` and `"new_run"` are still tolerated.

## Device Integration (Pico W)

Reference implementation: Pico W client publishes/consumes the topics above. See the client repo for full code and hardware details:
- https://github.com/cornellpepper/CuWatch_code (branch `main`, file `src/asynchio5.py`)

Minimal MicroPython publish example (update `BROKER` to your host IP):

```python
from umqtt.simple import MQTTClient
import ujson as json

DEVICE_ID = 3  # your device id (int)
BROKER = "192.168.1.10"  # IP of the machine running docker compose
PORT = 1883

client = MQTTClient(f"cuwatch_{DEVICE_ID:03d}", BROKER, port=PORT, keepalive=60)
client.connect()

topic = f"telemetry/{DEVICE_ID:03d}".encode()
event = {
  "device_number": DEVICE_ID,
  "muon_count": 1,
  "adc_v": 1234,
  "temp_adc_v": 987,
  "ts": "2025-08-27T14:30:12Z",  # absolute time (preferred)
  # "dt": 1000,  # optional ms since previous event
  "wait_cnt": 0,
  "coincidence": False,
}
client.publish(topic, json.dumps(event))
client.disconnect()
```

Optional: subscribe to control and print commands received by the device:

```python
CTRL = f"control/{DEVICE_ID:03d}/set".encode()

def on_msg(topic, msg):
  print("control:", msg)

client = MQTTClient(f"cuwatch_{DEVICE_ID:03d}", BROKER, port=PORT, keepalive=60)
client.set_callback(on_msg)
client.connect()
client.subscribe(CTRL)
# loop calling client.check_msg() in your main task to receive messages
```

### Expected telemetry payload (JSON)

The bridge prefers an absolute timestamp field `ts` on every telemetry message. A legacy `timestamp` or `end_time` is also accepted. If you cannot send an absolute timestamp, you may include a relative `dt` (milliseconds) and announce a run base time once (see below).

```json
{
  "device_number": 3,
  "ts": "2025-08-27T14:30:12Z",
  "muon_count": 42,
  "adc_v": 1234,
  "temp_adc_v": 987,
  "dt": 1000,
  "wait_cnt": 7,
  "coincidence": true
}
```

Notes:

- Timestamp key: prefer `ts`. `timestamp` and `end_time` are also accepted.
- Timestamp formats: ISO8601/RFC3339 (with optional `Z`) or epoch seconds/milliseconds.
- `dt` is now optional. If omitted or invalid, the bridge stores `0` for `dt`.

Optional run base announcement (to support relative-only devices):

```json
{ "run_start_ts": "2025-08-27T14:00:00Z" }
```

Accepted keys are `run_base_ts`, `run_start_ts`, or `run_start` (ISO8601/epoch). When a subsequent telemetry message only has `dt`, the bridge computes `ts = base + dt`.

### Runs and base timestamps

- Base time: The bridge treats the first `run_base_ts | run_start_ts | run_start` it sees for a device as the run base and upserts a row in `runs` with that `base_ts`. Missing or pre-2000 timestamps are ignored; the bridge will fall back to `now` if no valid base exists.
- Derived timestamps: If a telemetry has no absolute `ts` but includes `dt`, the bridge reconstructs `ts = base + dt`. If `dt` is missing/invalid, it stores `dt=0` and uses the best available timestamp.
- Metadata on first event: `baseline`, `threshold`, `reset_threshold`, and `is_leader` are accepted on the first event of a run and stored in `runs.meta` and `devices.meta` for later display. An optional `run_key` (string) from the device is also stored when present.
- New runs: Sending the control message `{ "new_run": true }` (or a device restart) should emit a fresh first event with a new base time and metadata. Each run is listed via `GET /api/device/<id>/runs` and tied to samples through `base_ts`.
- Run duration tracking:
  - When a new run starts, the bridge updates the previous run's `meta.run_end_ts` to the new run's `base_ts`.
  - As samples arrive for a run, the bridge updates `meta.run_end_inferred_ts` to the latest sample timestamp (only if explicit `run_end_ts` is not set).
  - The API computes `duration_s` from these timestamps and indicates whether the duration is inferred or explicit.
- Timed sessions: Use `POST /api/device/<id>/session` to start a timed run that automatically sends `new_run` at start and `shutdown` after the specified duration. The UI provides preset durations (5 min to 8 hours) with live countdown display.

## Services

- `mqtt-broker` — Mosquitto
- `db` — Postgres 16
- `bridge` — subscribes to MQTT and writes typed rows with Postgres upsert
- `web` — Flask UI + APIs; CSV export; event rate chart; WebSockets live status

## Quick start

```bash
docker compose up --build
# then open http://localhost:80
```

### Docker Compose: Dev & Maintenance

Common workflows for running and maintaining the stack locally.

Start the stack (foreground):

```bash
docker compose up --build
```

Start in background (detached):

```bash
docker compose up -d --build
```

Check status and health:

```bash
docker compose ps
docker compose logs -f web
docker compose logs -f bridge
```

Graceful stop and cleanup:

```bash
# Graceful stop (keeps containers for faster restart)
docker compose stop

# Stop and remove containers (recreates on next up)
docker compose down
```

Notes:
- If you run in the foreground, press Ctrl+C to stop gracefully.
- Postgres data is ephemeral with the provided compose file. `down` will remove the DB container and lose data. For persistence, add a named volume to Postgres (e.g., `- pgdata:/var/lib/postgresql/data`).

Rebuild and restart a service after dependency changes:

```bash
docker compose build web && docker compose up -d web
docker compose build bridge && docker compose up -d bridge
```

Tail all logs:

```bash
docker compose logs -f
```

Exec into containers:

```bash
# Shell inside web
docker compose exec web bash

# DB shell (psql)
docker compose exec db psql -U postgres -d iot
```

Health checks and quick diagnostics:

```bash
# Web health endpoint (liveness)
curl -fsS http://localhost/healthz

# Subscribe to MQTT topics
mosquitto_sub -h localhost -t 'telemetry/#' -v

# System health dashboard (CPU, memory, disk I/O, MQTT status, DB status, device activity rates)
# Visit http://localhost/system in your browser
```

**System Health & Monitoring**

- UI: `/system` shows real-time metrics, MQTT status, DB status, and device activity monitor (rate anomalies/light leaks), plus historical CPU/memory/disk I/O from SAR.
- JSON: `/api/system/health` for basic liveness + counts; `/api/system/device-rates` for per-device rates and anomaly flags; `/api/system/device-rates/history?device_id=<id>` for hourly rate history.
- Requirements: `sysstat` package on host and `/var/log/sysstat` mounted read-only (see [docs/SAR_INTEGRATION.md](docs/SAR_INTEGRATION.md)).

Backup and restore the dev database:

```bash
# Backup to a local file
docker compose exec -T db pg_dump -U postgres -d iot > backup.sql

# Restore from a local file
docker compose exec -T db psql -U postgres -d iot < backup.sql
```

Persistent storage (compose override):

To persist Postgres data across `down`/recreate, create `docker-compose.override.yml` alongside `docker-compose.yml`:

```yaml
services:
  db:
    volumes:
      - pgdata:/var/lib/postgresql/data

volumes:
  pgdata:
```

This repository includes a ready-to-use [docker-compose.override.yml](docker-compose.override.yml) with the configuration above. Docker Compose loads it automatically.

Alternatively, bind-mount to a local folder (Linux/macOS):

```yaml
services:
  db:
    volumes:
      - ./data/postgres:/var/lib/postgresql/data
```

Where the database lives:
- Inside the container: `/var/lib/postgresql/data`.
- With a named volume (e.g., `pgdata`): Docker stores it under its volume path (Linux default `/var/lib/docker/volumes/pgdata/_data`).
- Inspect volumes:

```bash
docker volume ls
docker volume inspect pgdata
```

### Rotate/replace the database volume (e.g., each semester)

If you want a fresh database while keeping old data, create a new host folder (or named volume) and update your override file to point at it. Example using a bind mount with a semester-specific folder:

```yaml
# docker-compose.override.yml
services:
  db:
    volumes:
      - ./data/postgres_spring2026:/var/lib/postgresql/data
```

Rotation steps:
1) Stop the stack: `docker compose down` (or `stop` to keep containers).
2) (Optional) Backup old DB: `docker compose exec -T db pg_dump -U postgres -d iot > backup.sql`.
3) Create the new host directory, e.g., `mkdir -p data/postgres_spring2026`.
4) Edit `docker-compose.override.yml` to reference the new folder (or change the named volume name).
5) Start the stack: `docker compose up -d --build`.

Using a named volume instead of a bind mount works the same way—just change the volume name (e.g., `pgdata_spring2026`) in both `services.db.volumes` and the top-level `volumes:` block. The old volume remains intact for archival; the new name gives you a fresh database.

Query the database from CLI:

```bash
# From inside the db container
docker compose exec db psql -U postgres -d iot

# One-off queries
docker compose exec -T db psql -U postgres -d iot -c "SELECT COUNT(*) FROM samples;"

# From host (requires psql installed)
psql "postgresql://postgres:postgres@127.0.0.1:5432/iot" -c "SELECT NOW();"
```

Useful queries:

```sql
-- Latest devices and status
SELECT id, last_seen, online FROM devices ORDER BY last_seen DESC LIMIT 10;

-- Sample counts by device
SELECT device_id, COUNT(*) AS n FROM samples GROUP BY device_id ORDER BY n DESC LIMIT 10;

-- Latest 5 samples for a device (replace dev-001)
SELECT ts, muon_count, adc_v, temp_adc_v, dt, wait_cnt, coincidence
FROM samples
WHERE device_id = 'dev-001'
ORDER BY ts DESC, muon_count DESC
LIMIT 5;

-- Known runs (newest first)
SELECT device_id, base_ts, run_key FROM runs ORDER BY base_ts DESC LIMIT 20;
```

Publish a test:

```bash
mosquitto_pub -h localhost -t telemetry/dev-001 -m '{
  "device_number": 3,
  "ts": "2025-08-27T14:30:12Z",
  "muon_count": 42,
  "adc_v": 1234,
  "temp_adc_v": 987,
  "wait_cnt": 7,
  "coincidence": true
}'
```

Epoch example (milliseconds):

```bash
mosquitto_pub -h localhost -t telemetry/dev-001 -m '{
  "device_number": 3,
  "ts": 1735377000000,
  "muon_count": 100,
  "adc_v": 1200,
  "temp_adc_v": 900,
  "wait_cnt": 3,
  "coincidence": false
}'
```

Relative-only example with run base and dt:

```bash
mosquitto_pub -h localhost -t telemetry/dev-001 -m '{ "run_base_ts": "2025-08-27T14:00:00Z" }'
mosquitto_pub -h localhost -t telemetry/dev-001 -m '{
  "device_number": 3,
  "dt": 1500,
  "muon_count": 101,
  "adc_v": 1201,
  "temp_adc_v": 901,
  "wait_cnt": 0,
  "coincidence": false
}'
```

## UI and rate computation

- The Device page shows “Live Rate (Hz)” and a line chart. Rates are computed from absolute timestamps over a sliding window; no per-event `dt` is required.
- Ordering: when timestamps are equal, samples are tie-broken by `muon_count` to produce deterministic windows and charts.
- The “Latest Samples” table shows the newest 50 rows for the selected window.

## APIs

- `GET /api/devices` — list devices with last_seen.
- `GET /api/device/<device_id>/meta` — device meta and metrics.
- `GET /api/device/<device_id>/runs` — known runs for a device.
  - Ordering: `base_ts DESC` (newest first).
  - Fields per row: `id, device_id, base_ts, run_key, meta, duration_s, duration_inferred`.
  - `base_ts`: ISO8601 timestamp (UTC) of the run's base/reference time.
  - `run_key`: optional device-supplied identifier for the run.
  - `meta`: free-form metadata (JSON string/object).
  - `duration_s`: run duration in seconds (computed from `run_end_ts` or `run_end_inferred_ts` in `meta`).
  - `duration_inferred`: `true` if duration is based on inferred end timestamp, `false` if explicit, `null` if no duration available.
  - Run end tracking:
    - `meta.run_end_ts`: Explicit run end timestamp, set when a new run is announced (the new run's `base_ts` becomes the previous run's `run_end_ts`).
    - `meta.run_end_inferred_ts`: Inferred end timestamp, updated to the latest sample timestamp received for the run (only if `run_end_ts` is not set).
- `POST /api/device/<device_id>/session` — start a timed session (authenticated).
  - Auth: requires login.
  - Starts a new run with automatic shutdown after the specified duration.
  - Immediately publishes `{ "new_run": true }` to `control/<device_id>/set`.
  - Schedules a background timer to publish `{ "shutdown": true }` after `duration_s` seconds.
  - If a session is already active for the device, it is cancelled and replaced.
  - Content-Type: `application/json`.
  - Payload: `{ "duration_s": <int> }` — duration in seconds (must be positive, max 604800 = 7 days).
  - Response: `200 { "ok": true, "duration_s": <int>, "stop_time": "<ISO8601>" }` on success.
  - Error responses:
    - `400 { "ok": false, "error": "..." }` for invalid duration.
- `DELETE /api/device/<device_id>/session` — stop active timed session (authenticated).
  - Auth: requires login.
  - Immediately publishes `{ "shutdown": true }` to `control/<device_id>/set` and clears the session.
  - Response: `200 { "ok": true }` on success, or `404 { "ok": false, "error": "No active session" }` if no session exists.
- `GET /api/device/<device_id>/session` — get current session status.
  - Returns: `{ "active": false }` if no session, or `{ "active": true, "duration_s": <int>, "remaining_s": <int>, "stop_time": "<ISO8601>" }` if session is running.
  - Useful for UI to display countdown timer and session state.
- `GET /api/samples/<device_id>?limit=&start=&end=` — JSON rows.
  - Ordering: `ts DESC, muon_count DESC` (deterministic).
  - `start`/`end`: ISO8601 (or epoch seconds) in UTC; optional.
  - `limit`: 1..50000 (default 500).
- `GET /api/export/<device_id>.csv?start=&end=` — CSV export.
  - Ordering: `ts ASC, muon_count ASC`.
  - Columns: `device_id,ts,device_number,muon_count,adc_v,temp_adc_v,dt,wait_cnt,coincidence` (`dt` may be `0`).
- `POST /api/control/<device_id>` — publish a control message for a device.
  - Auth: requires login.
  - Publishes JSON to MQTT topic `control/<device_id>/set` with QoS 1 and retain=false (as used by the API).
  - Content-Type: `application/json`.
  - Accepted payloads:
    - `{ "threshold": <int 0..4095> }` — sets the discriminator threshold.
    - `{ "reset_threshold": <int 0..4095> }` — sets the reset threshold (must be strictly less than the effective threshold; server validates using provided `threshold` or last known value).
    - `"shutdown"` — request a clean shutdown.
    - `"new_run"` — request starting a new run (device chooses semantics).
    - `{ "make_leader": true|false }` — request leader election state.
  - Responses:
    - `200 { "ok": true }` on publish accept.
    - `400` with `{ "ok": false, "error": "..." }` for bad input (e.g., invalid threshold).
- `GET /api/system/sar/cpu?days_back=0` — CPU usage history from SAR data.
  - `days_back`: number of days back to retrieve (default 0 for today).
  - Returns array of samples with timestamp, user%, system%, iowait%, and other metrics.
- `GET /api/system/sar/memory?days_back=0` — Memory usage history from SAR data.
  - `days_back`: number of days back to retrieve (default 0 for today).
  - Returns array of samples with timestamp, memused%, memfree%, and other metrics.
- `GET /api/system/sar/disk?days_back=0` — Disk I/O history from SAR data.
  - `days_back`: number of days back to retrieve (default 0 for today).
  - Returns array of disk I/O samples with read/write statistics per device.

## Database (tables)

- `devices(id, last_seen, online, device_number, meta)`
- `samples(id, device_id, ts, device_number, muon_count, adc_v, temp_adc_v, dt, wait_cnt, coincidence)`
- `runs(id, device_id, base_ts, run_key, meta)`

## Authentication

Authentication is disabled in this build: all UI pages and APIs are accessible without login. If you need to restore login enforcement, re-enable the `login_required` checks in [services/web/app.py](services/web/app.py) (the decorator is currently a no-op) and set `LOGIN_USER`/`LOGIN_PASSWORD` in the environment.

## Production Notes

- Reverse proxy & TLS: Terminate TLS with Nginx/Traefik in front of the `web` service. The app listens on container port 80; forward `443 → web:80` and enforce HTTPS.
- WebSockets: The app runs `gunicorn -k eventlet -w 1` for stable WebSockets. If scaling horizontally, ensure sticky sessions or a shared pub/sub layer; keep `eventlet` compatibility.
- Secrets & auth: Set `SECRET_KEY`, `LOGIN_USER`, and `LOGIN_PASSWORD` via environment. Avoid default `SECRET_KEY` in production.
- MQTT security: Configure Mosquitto for authentication/TLS (see [deploy/mosquitto.conf](deploy/mosquitto.conf)). If the broker enforces auth, set `MQTT_USERNAME`/`MQTT_PASSWORD` for the web publisher.
- Database persistence: Use the provided named volume (`pgdata`) or a bind mount for Postgres. Restrict DB exposure (bind to localhost or private network) and back up regularly.
- Backups: Use `pg_dump`/`pg_restore` for logical backups (examples above). For larger installs, consider streaming/base backups.
- Health checks: Load balancers can use `/healthz` for liveness checks; Docker also reports service health.
- Migrations: Tables are created at runtime for dev. For production, manage schema with Alembic (see [migrations/README.md](migrations/README.md)).
