import json, threading, time
import paho.mqtt.client as mqtt
from flask import current_app

_client = None
_lock = threading.Lock()
_loop_started = False

def _get_client():
    global _client, _loop_started
    with _lock:
        if _client is None:
            _client = mqtt.Client(protocol=mqtt.MQTTv311)
            cfg = current_app.config
            # Optional auth if provided
            user = cfg.get("MQTT_USERNAME")
            pw = cfg.get("MQTT_PASSWORD")
            if user:
                _client.username_pw_set(user, pw)
            # Backoff for reconnects
            try:
                _client.reconnect_delay_set(min_delay=1, max_delay=30)
            except Exception:
                pass
            _client.connect(cfg["MQTT_BROKER_URL"], cfg["MQTT_BROKER_PORT"], 60)
        # Ensure network loop is running once
        if not _loop_started:
            _client.loop_start()
            _loop_started = True
        return _client

def publish_control(device_id: str, payload: dict, retain=True, qos=1):
    topic = f"control/{device_id}/set"
    msg = json.dumps(payload, separators=(",", ":"))
    client = _get_client()
    # Publish and wait for it to be sent to the broker
    info = client.publish(topic, msg, qos=qos, retain=retain)
    try:
        # Wait a short time for the broker ACK (esp. QoS 1)
        info.wait_for_publish(timeout=3)
    except Exception:
        # If wait fails, give a small grace period
        time.sleep(0.1)
