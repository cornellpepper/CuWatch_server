# IoT Muon Skeleton (v7)
Flask + WebSockets + Mosquitto + Postgres. Production Gunicorn worker is **eventlet** (stable WebSockets).
Telemetry stored as typed columns; dashboard shows **rolling boxcar event rate**.

## Telemetry topics & payload
- Publish **telemetry** to: `telemetry/<device_id>`
- Optional **status**/heartbeat: `status/<device_id>`
- Control (from web app): `control/<device_id>/set` (retained)

### Expected telemetry payload (JSON)
```json
{
  "device_number": 3,
  "timestamp": "2025-08-27T14:30:12Z",
  "muon_count": 42,
  "adc_v": 1234,
  "temp_adc_v": 987,
  "dt": 1000,
  "wait_cnt": 7,
  "coincidence": true
}
```
Timestamp may also be provided as `ts` or `end_time` (epoch seconds/ms or ISO8601).

## Services
- `mqtt-broker` — Mosquitto
- `db` — Postgres 16
- `bridge` — subscribes to MQTT and writes typed rows with Postgres upsert
- `web` — Flask UI + APIs; CSV export; **event rate** chart; WebSockets live log

## Quick start
```bash
docker compose up --build
# then open http://localhost:5000
```
Publish a test:
```bash
mosquitto_pub -h localhost -t telemetry/dev-001 -m '{
  "device_number": 3,
  "timestamp": "2025-08-27T14:30:12Z",
  "muon_count": 42,
  "adc_v": 1234,
  "temp_adc_v": 987,
  "dt": 1000,
  "wait_cnt": 7,
  "coincidence": true
}'
```
