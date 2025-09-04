import os, json, time, sys
import paho.mqtt.client as mqtt
from sqlalchemy import create_engine, Table, MetaData, Column, BigInteger, String, DateTime, Integer, Boolean, text, UniqueConstraint
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import OperationalError
from datetime import datetime, timezone, timedelta

DB_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@db/iot")
MQTT_HOST = os.getenv("MQTT_BROKER_URL", "mqtt-broker")
MQTT_PORT = int(os.getenv("MQTT_BROKER_PORT", "1883"))

engine = create_engine(DB_URL, future=True, pool_pre_ping=True)
meta = MetaData()

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
    print("Bridge connected to MQTT; subscribed.")

def on_message(client, userdata, msg):
    topic_parts = msg.topic.split("/")
    try:
        payload = json.loads(msg.payload.decode("utf-8"))
    except Exception:
        return

    now = datetime.now(timezone.utc)
    with engine.begin() as conn:
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

                    # Upsert run record
                    run_meta = None
                    try:
                        run_meta = json.dumps({"source": "bridge"})
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

                # Compute absolute timestamp if only a relative dt is provided
                dt_ms = None
                try:
                    dt_ms = to_int("dt", payload)
                except ValueError:
                    dt_ms = None

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

                row = {
                    "device_id": device_id,
                    "ts": abs_ts,
                    "device_number": to_int("device_number", payload),
                    "muon_count": to_int("muon_count", payload),
                    "adc_v": to_int("adc_v", payload),
                    "temp_adc_v": to_int("temp_adc_v", payload),
                    "dt": to_int("dt", payload),
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
