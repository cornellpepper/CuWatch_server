## Plan: Add 48h Host Temperature History

Add a host-temperature time series to the System Health flow by sampling Raspberry Pi thermal data once per minute, storing it in Postgres, and exposing it through a new API + chart in the existing SAR-style section. This matches your preference to use an equivalent thermal source instead of direct vcgencmd execution and guarantees a rolling 2-day history.

**Steps**
1. Phase 1 - Data model and retention
2. Add a new ORM model in /home/CuWatch/src/CuWatch_server/services/web/models.py for system metrics history (timestamp + temperature in C). Keep it independent of device telemetry tables.
3. Define retention policy of 48 hours in code constants so old rows are pruned automatically. Retention cleanup should run in the same background workflow as sampling. Depends on step 2.
4. Phase 2 - Temperature collection service
5. Add a lightweight sampler in /home/CuWatch/src/CuWatch_server/services/web/app.py that runs every 60 seconds and reads an equivalent thermal source (primary: /sys/class/thermal/thermal_zone0/temp; fallback: /sys/class/thermal/* if needed).
6. Convert millidegree values to Celsius and insert one row per sample into the new table. Depends on step 2.
7. Add defensive behavior: if thermal source is missing/unreadable, log and skip this interval without crashing the app. Parallel with step 6.
8. Add periodic prune query to delete rows older than now-48h so data volume remains bounded. Depends on step 3 and step 6.
9. Phase 3 - API and UI integration
10. Add a new endpoint in /home/CuWatch/src/CuWatch_server/services/web/app.py, for example /api/system/sar/temperature, returning SAR-like payload shape: metric_type, window_hours (default 48), samples, data[{ts,temp_c}]. Depends on step 6.
11. Update /home/CuWatch/src/CuWatch_server/services/web/templates/system_health.html to include a Temperature tab/chart in the Historical Data section and fetch the new endpoint in the existing historical chart update flow. Depends on step 10.
12. Keep chart ordering and UX consistent with current patterns: sort ascending by timestamp client-side, unified hover, responsive Plotly layout. Parallel with step 11.
13. Phase 4 - Deployment wiring and docs
14. Update /home/CuWatch/src/CuWatch_server/README.md with the new temperature history capability, source path assumptions, and troubleshooting when thermal files are unavailable in non-RPi environments. Depends on step 5.
15. If needed, update /home/CuWatch/src/CuWatch_server/docker-compose.yml notes to document that host thermal filesystem access is required for meaningful temperature data in containers. Parallel with step 14.

**Relevant files**
- /home/CuWatch/src/CuWatch_server/services/web/models.py - Add new table/model for host temperature samples.
- /home/CuWatch/src/CuWatch_server/services/web/app.py - Add background collector lifecycle, retention pruning, and API endpoint.
- /home/CuWatch/src/CuWatch_server/services/web/templates/system_health.html - Add temperature tab, chart container, and fetch/render logic.
- /home/CuWatch/src/CuWatch_server/README.md - Document behavior, limits, and deployment expectations.
- /home/CuWatch/src/CuWatch_server/docker-compose.yml - Optional notes on thermal source visibility in containerized deployments.

**Verification**
1. Start stack and confirm sampler inserts rows over several minutes by querying the new table from Postgres.
2. Call the new endpoint and verify JSON includes ordered timestamps and Celsius values for at least recent minutes.
3. Open System Health page and confirm Temperature chart renders and updates when changing date/window controls.
4. Simulate missing thermal source (temporary bad path) and verify API returns empty data gracefully while web service remains healthy.
5. Verify retention by inserting older synthetic rows and confirming prune logic removes data older than 48 hours.

**Decisions**
- Use equivalent thermal source from /sys/class/thermal instead of vcgencmd binary execution.
- Keep 1-minute cadence and rolling 48-hour history as explicit product requirements.
- Persist in Postgres to survive restarts and guarantee historical availability.

**Further Considerations**
1. Endpoint shape choice: strict SAR parity (days_back) vs explicit window_hours. Recommendation: use window_hours for minute-resolution data and fixed 48h default.
2. Multi-worker future: if Gunicorn worker count increases beyond 1, sampler should use a leader lock or external scheduler to avoid duplicate writes.
