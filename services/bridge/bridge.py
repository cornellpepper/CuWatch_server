import os, json, time, sys
import paho.mqtt.client as mqtt
from sqlalchemy import create_engine, Table, MetaData, Column, BigInteger, String, DateTime, Integer, Boolean, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import OperationalError
from datetime import datetime, timezone

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
    if v is None:
        return datetime.now(timezone.utc)
    if isinstance(v, (int, float)) or (isinstance(v, str) and v.isdigit()):
        x = float(v)
        if x > 1e12:  # ms
            x = x / 1000.0
        return datetime.fromtimestamp(x, tz=timezone.utc)
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
            pass
    return datetime.now(timezone.utc)

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
                ts = parse_ts(payload.get("timestamp") or payload.get("ts") or payload.get("end_time"))
                row = {
                    "device_id": device_id,
                    "ts": ts,
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
            stmt = pg_insert(devices).values(
                id=device_id,
                last_seen=ts,
                online=True,
                device_number=row["device_number"],
                meta=None,
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=[devices.c.id],
                set_={"last_seen": ts, "online": True, "device_number": row["device_number"]},
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
