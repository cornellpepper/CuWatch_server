import os, json, time, sys
import paho.mqtt.client as mqtt
from sqlalchemy import create_engine, Table, MetaData, Column, BigInteger, String, DateTime, Integer, Boolean, text, UniqueConstraint
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import OperationalError
from datetime import datetime, timezone, timedelta

DB_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@db/iot")
MQTT_HOST = os.getenv("MQTT_BROKER_URL", "mqtt-broker")
MQTT_PORT = int(os.getenv("MQTT_BROKER_PORT", "1883"))

# Optional debug logging controlled by env var
DEBUG = str(os.getenv("BRIDGE_DEBUG", "")).strip().lower() in ("1", "true", "yes", "on")

def dlog(*args, **kwargs):
    if DEBUG:
        print(*args, **kwargs)

engine = create_engine(DB_URL, future=True, pool_pre_ping=True)
meta = MetaData()

# Cache of last control payloads per device_id
last_controls = {}

samples = Table("samples", meta,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("device_id", String, index=True, nullable=False),
    Column("ts", DateTime(timezone=True), nullable=False),
    Column("device_number", Integer, nullable=False),
    Column("muon_count", Integer, nullable=False),
    Column("adc_v", Integer, nullable=False),
    Column("temp_adc_v", Integer, nullable=False),
    Column("dt", Integer, nullable=False),
    Column("wait_cnt", Integer, nullable=False),
    Column("coincidence", Boolean, nullable=False),
)

devices = Table("devices", meta,
    Column("id", String, primary_key=True),
    Column("last_seen", DateTime(timezone=True)),
    Column("online", Boolean),
    Column("device_number", Integer),
    Column("meta", String),
)

# Per-device runs: a run base timestamp and optional run key/meta
runs = Table("runs", meta,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("device_id", String, index=True, nullable=False),
    Column("base_ts", DateTime(timezone=True), index=True, nullable=False),
    Column("run_key", String),
    Column("meta", String),
    UniqueConstraint("device_id", "base_ts", name="uq_runs_device_base"),
)

def wait_for_db(max_wait=120):
    start = time.time()
    while True:
        try:
            with engine.begin() as conn:
                conn.execute(text("SELECT 1"))
            print("DB is ready.")
            break
        except OperationalError:
            if time.time() - start > max_wait:
                print("ERROR: DB not ready after wait.", file=sys.stderr)
                raise
            print("Waiting for DB...")
            time.sleep(2)

def init_db():
    with engine.begin() as conn:
        meta.create_all(conn)

def parse_ts(v):
    # Accept ISO 8601 / RFC3339 or epoch seconds/milliseconds
    # Return None if value missing/invalid instead of defaulting to now.
    if v is None:
        return None
    if isinstance(v, (int, float)) or (isinstance(v, str) and v.isdigit()):
        x = float(v)
        if x > 1e12:  # ms
            x = x / 1000.0
        try:
            return datetime.fromtimestamp(x, tz=timezone.utc)
        except Exception:
            return None
    if isinstance(v, str):
        s = v.strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except Exception:
            return None
    return None

def to_int(name, payload):
    try:
        return int(payload[name])
    except Exception:
        raise ValueError(f"{name} must be int")

def to_bool(name, payload):
    val = payload.get(name)
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float)):
        return bool(int(val))
    if isinstance(val, str):
        s = val.strip().lower()
        if s in ("true","t","1","yes","y"): return True
        if s in ("false","f","0","no","n"): return False
    raise ValueError(f"{name} must be bool")

def on_connect(client, userdata, flags, rc):
    client.subscribe("telemetry/#")
    client.subscribe("status/+")
    # Cache control messages to enrich run metadata
    client.subscribe("control/+/set")
    print("Bridge connected to MQTT; subscribed.")
    dlog("Debug logging enabled (BRIDGE_DEBUG=1)")

def on_message(client, userdata, msg):
    topic_parts = msg.topic.split("/")
    try:
        payload = json.loads(msg.payload.decode("utf-8"))
    except Exception:
        return

    now = datetime.now(timezone.utc)
    with engine.begin() as conn:
        if topic_parts[0] == "control":
            # Topic: control/<device_id>/set
            if len(topic_parts) >= 3 and topic_parts[2] == "set":
                device_id = topic_parts[1]
                # Store the entire payload; UI and run meta will pick relevant fields
                try:
                    last_controls[device_id] = dict(payload)
                except Exception:
                    last_controls[device_id] = payload
                dlog(f"Cached control for {device_id}: {last_controls[device_id]}")
            return

        if topic_parts[0] == "telemetry":
            device_id = topic_parts[1] if len(topic_parts) > 1 else "unknown"
            try:
                # Load existing device meta (stored as JSON string in DB)
                res = conn.execute(devices.select().where(devices.c.id == device_id))
                meta_row = res.mappings().fetchone()  # RowMapping for name-based access
                current_meta = {}
                if meta_row and meta_row.get("meta"):
                    try:
                        m = meta_row.get("meta")
                        current_meta = json.loads(m) if isinstance(m, str) else (m or {})
                    except Exception:
                        current_meta = {}

                # Parse any absolute timestamps present
                abs_ts = parse_ts(payload.get("timestamp") or payload.get("ts") or payload.get("end_time"))
                # Guard against bogus epoch/zero timestamps which would anchor at 1970
                min_valid = datetime(2000, 1, 1, tzinfo=timezone.utc)
                if abs_ts is not None and abs_ts < min_valid:
                    abs_ts = None

                # Accept multiple possible keys for a run base time announcement
                announced_base = (
                    parse_ts(payload.get("run_base_ts"))
                    or parse_ts(payload.get("run_start_ts"))
                    or parse_ts(payload.get("run_start"))
                    or None
                )
                if announced_base is not None and announced_base < min_valid:
                    announced_base = None
                run_id = payload.get("run_id") or payload.get("run") or None

                # If a base is announced, persist it in device meta
                if announced_base is not None:
                    current_meta.setdefault("current_run", {})
                    current_meta["current_run"]["base_ts"] = announced_base.isoformat()
                    if run_id is not None:
                        current_meta["current_run"]["id"] = str(run_id)

                    # Pull cached control data to enrich run metadata
                    ctl = last_controls.get(device_id) or {}
                    # Extract specific fields - prefer telemetry payload, fallback to cached control
                    baseline = payload.get("baseline") or ctl.get("baseline")
                    is_leader = payload.get("is_leader") if "is_leader" in payload else ctl.get("is_leader")
                    reset_threshold = payload.get("reset_threshold") or ctl.get("reset_threshold")
                    threshold = payload.get("threshold") or ctl.get("threshold")
                    dlog(f"Run announced for {device_id} @ {announced_base.isoformat()}, values: baseline={baseline}, is_leader={is_leader}, reset_threshold={reset_threshold}")
                    # Mirror into current_run for convenience
                    if baseline is not None:
                        current_meta["current_run"]["baseline"] = baseline
                    if is_leader is not None:
                        current_meta["current_run"]["is_leader"] = bool(is_leader)
                    if reset_threshold is not None:
                        current_meta["current_run"]["reset_threshold"] = reset_threshold
                    if threshold is not None:
                        current_meta["current_run"]["threshold"] = threshold

                    # Upsert run record
                    # Build run meta JSON including cached control snapshot fields if available
                    run_meta_obj = {"source": "bridge"}
                    if baseline is not None:
                        run_meta_obj["baseline"] = baseline
                    if is_leader is not None:
                        run_meta_obj["is_leader"] = bool(is_leader)
                    if reset_threshold is not None:
                        run_meta_obj["reset_threshold"] = reset_threshold
                    if threshold is not None:
                        run_meta_obj["threshold"] = threshold
                    # Optionally include the whole control payload for traceability
                    if ctl:
                        run_meta_obj["control_snapshot"] = ctl
                    try:
                        run_meta = json.dumps(run_meta_obj)
                    except Exception:
                        run_meta = None
                    rstmt = pg_insert(runs).values(
                        device_id=device_id,
                        base_ts=announced_base,
                        run_key=(str(run_id) if run_id is not None else None),
                        meta=run_meta,
                    ).on_conflict_do_update(
                        index_elements=[runs.c.device_id, runs.c.base_ts],
                        set_={"run_key": (str(run_id) if run_id is not None else None), "meta": run_meta},
                    )
                    conn.execute(rstmt)
                    dlog(f"Upserted run meta for {device_id} @ {announced_base.isoformat()}")

                # Compute absolute timestamp if only a relative dt is provided
                dt_ms = None
                try:
                    # dt (ms) is now optional; prefer absolute timestamp `ts` when present
                    dt_ms = to_int("dt", payload)
                except ValueError:
                    dt_ms = None

                # Compute instantaneous rate (Hz) from dt in milliseconds and update 
                # EMA in device meta
                inst_rate_hz = None
                if dt_ms is not None and dt_ms > 0:
                    inst_rate_hz = 1000.0 / float(dt_ms)

                # Store metrics in device meta (no schema change required)
                metrics = (current_meta.get("metrics") or {})
                if inst_rate_hz is not None:
                    alpha = 0.2  # smoothing factor for EMA
                    prev_ema = metrics.get("ema_rate_hz")
                    if isinstance(prev_ema, (int, float)):
                        ema = alpha * inst_rate_hz + (1.0 - alpha) * float(prev_ema)
                    else:
                        ema = inst_rate_hz
                    metrics["inst_rate_hz"] = round(inst_rate_hz, 3)
                    metrics["ema_rate_hz"] = round(ema, 3)
                    current_meta["metrics"] = metrics

                if abs_ts is None and dt_ms is not None:
                    base_iso = (current_meta.get("current_run") or {}).get("base_ts")
                    if base_iso:
                        try:
                            base_dt = datetime.fromisoformat(base_iso.replace('Z', '+00:00'))
                            abs_ts = base_dt + timedelta(milliseconds=int(dt_ms))
                        except Exception:
                            pass

                # Fallback to now if nothing else is available
                if abs_ts is None:
                    abs_ts = now

                # Build row; make `dt` optional with a safe default (0)
                safe_dt = 0
                try:
                    if "dt" in payload:
                        safe_dt = int(payload.get("dt"))
                except Exception:
                    safe_dt = 0

                row = {
                    "device_id": device_id,
                    "ts": abs_ts,
                    "device_number": to_int("device_number", payload),
                    "muon_count": to_int("muon_count", payload),
                    "adc_v": to_int("adc_v", payload),
                    "temp_adc_v": to_int("temp_adc_v", payload),
                    "dt": safe_dt,
                    "wait_cnt": to_int("wait_cnt", payload),
                    "coincidence": to_bool("coincidence", payload),
                }
            except ValueError as e:
                print(f"Skip bad message on {msg.topic}: {e}", file=sys.stderr)
                return

            conn.execute(samples.insert().values(**row))

            # Upsert device row and persist updated meta if changed
            meta_to_store = None
            try:
                meta_to_store = json.dumps(current_meta) if current_meta else None
            except Exception:
                meta_to_store = None

            stmt = pg_insert(devices).values(
                id=device_id,
                last_seen=abs_ts,
                online=True,
                device_number=row["device_number"],
                meta=meta_to_store,
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=[devices.c.id],
                set_={
                    "last_seen": abs_ts,
                    "online": True,
                    "device_number": row["device_number"],
                    "meta": meta_to_store,
                },
            )
            conn.execute(stmt)

            # If baseline/is_leader/reset_threshold provided outside of the exact run announcement,
            # try to merge them into the current run's meta using device meta's current_run.base_ts
            try:
                has_any = (
                    ("baseline" in payload) or ("is_leader" in payload) or ("reset_threshold" in payload)
                )
                if has_any:
                    cr = (current_meta.get("current_run") or {}) if isinstance(current_meta, dict) else {}
                    base_iso = cr.get("base_ts")
                    if base_iso:
                        try:
                            base_dt = datetime.fromisoformat(base_iso.replace('Z', '+00:00'))
                        except Exception:
                            base_dt = None
                        if base_dt is not None:
                            # Load existing run meta, merge fields
                            sel = runs.select().where(
                                (runs.c.device_id == device_id) & (runs.c.base_ts == base_dt)
                            )
                            res = conn.execute(sel).mappings().fetchone()
                            existing_meta = {}
                            if res and res.get("meta"):
                                try:
                                    m = res.get("meta")
                                    existing_meta = json.loads(m) if isinstance(m, str) else (m or {})
                                except Exception:
                                    existing_meta = {}
                            # Merge new fields
                            if "baseline" in payload:
                                existing_meta["baseline"] = payload.get("baseline")
                            if "is_leader" in payload:
                                existing_meta["is_leader"] = bool(payload.get("is_leader"))
                            if "reset_threshold" in payload:
                                existing_meta["reset_threshold"] = payload.get("reset_threshold")
                            if "threshold" in payload:
                                existing_meta["threshold"] = payload.get("threshold")
                            existing_meta.setdefault("source", "bridge")
                            try:
                                upd_meta = json.dumps(existing_meta)
                            except Exception:
                                upd_meta = None
                            if upd_meta is not None:
                                upd = (
                                    pg_insert(runs)
                                    .values(device_id=device_id, base_ts=base_dt, meta=upd_meta)
                                    .on_conflict_do_update(
                                        index_elements=[runs.c.device_id, runs.c.base_ts],
                                        set_={"meta": upd_meta},
                                    )
                                )
                                conn.execute(upd)
                                dlog(f"Merged late fields into run meta for {device_id} @ {base_dt.isoformat()}")
            except Exception as _merge_err:
                # best-effort; log merge failures for debugging but do not interrupt processing
                dlog(f"Error while merging late fields into run meta for {device_id}: {_merge_err!r}")

        elif topic_parts[0] == "status":
            device_id = topic_parts[1] if len(topic_parts) > 1 else "unknown"
            stmt = pg_insert(devices).values(
                id=device_id,
                last_seen=now,
                online=True,
                meta=None,
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=[devices.c.id],
                set_={"last_seen": now, "online": True},
            )
            conn.execute(stmt)

if __name__ == "__main__":
    wait_for_db()
    init_db()

    client = mqtt.Client()
    client.will_set("lwt/bridge", json.dumps({"online": False}), retain=True)
    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(MQTT_HOST, MQTT_PORT, 60)
    client.loop_forever()
