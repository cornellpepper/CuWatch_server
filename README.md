# CuWatch Server implementation (v7)

The Pepper board sends events to this server, which displays the data, makes it available for download, and is used to control the Pepper boards.

Flask + WebSockets + Mosquitto + Postgres. Production Gunicorn worker is **eventlet** (stable WebSockets).
Telemetry stored as typed columns; dashboard shows a **rolling event rate (Hz)** computed from absolute timestamps.

## Telemetry topics & payload

- Publish **telemetry** to: `telemetry/<device_id>`
- Optional **status**/heartbeat: `status/<device_id>`
- Control (from web app): `control/<device_id>/set` (retained)

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

## Services

- `mqtt-broker` — Mosquitto
- `db` — Postgres 16
- `bridge` — subscribes to MQTT and writes typed rows with Postgres upsert
- `web` — Flask UI + APIs; CSV export; event rate chart; WebSockets live status

## Quick start

```bash
docker compose up --build
# then open http://localhost:5000
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
  - Fields per row: `id, device_id, base_ts, run_key, meta`.
  - `base_ts`: ISO8601 timestamp (UTC) of the run's base/reference time.
  - `run_key`: optional device-supplied identifier for the run.
  - `meta`: free-form metadata (JSON string/object).
- `GET /api/samples/<device_id>?limit=&start=&end=` — JSON rows.
  - Ordering: `ts DESC, muon_count DESC` (deterministic).
  - `start`/`end`: ISO8601 (or epoch seconds) in UTC; optional.
  - `limit`: 1..50000 (default 500).
- `GET /api/export/<device_id>.csv?start=&end=` — CSV export.
  - Ordering: `ts ASC, muon_count ASC`.
  - Columns: `device_id,ts,device_number,muon_count,adc_v,temp_adc_v,dt,wait_cnt,coincidence` (`dt` may be `0`).

## Database (tables)
- `devices(id, last_seen, online, device_number, meta)`
- `samples(id, device_id, ts, device_number, muon_count, adc_v, temp_adc_v, dt, wait_cnt, coincidence)`
- `runs(id, device_id, base_ts, run_key, meta)`

## Authentication
The UI has a simple login. Set `LOGIN_USER` and `LOGIN_PASSWORD` in the web service environment to enable fixed credentials; otherwise any non-empty username is accepted for development.
