import json, threading
import paho.mqtt.client as mqtt
from flask import current_app

_client = None
_lock = threading.Lock()

def _get_client():
    global _client
    with _lock:
        if _client is None:
            _client = mqtt.Client(protocol=mqtt.MQTTv311)
            cfg = current_app.config
            _client.connect(cfg["MQTT_BROKER_URL"], cfg["MQTT_BROKER_PORT"], 60)
        return _client

def publish_control(device_id: str, payload: dict, retain=True, qos=1):
    topic = f"control/{device_id}/set"
    msg = json.dumps(payload, separators=(",", ":"))
    client = _get_client()
    client.loop_start()
    try:
        client.publish(topic, msg, qos=qos, retain=retain)
    finally:
        client.loop_stop()
