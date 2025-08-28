# Web-side MQTT subscriber for live streaming to WebSocket clients.
import json, threading, queue
import paho.mqtt.client as mqtt

class LiveStream:
    def __init__(self, maxlen=2000):
        self._q = queue.Queue()
        self._buf = []
        self._maxlen = maxlen
        self._lock = threading.Lock()

    def push(self, msg: str):
        with self._lock:
            self._buf.append(msg)
            if len(self._buf) > self._maxlen:
                self._buf = self._buf[-self._maxlen:]
        try:
            self._q.put_nowait(msg)
        except queue.Full:
            pass

    def snapshot(self, n=100):
        with self._lock:
            return self._buf[-n:]

    def wait(self, timeout=1.0):
        try:
            return self._q.get(timeout=timeout)
        except queue.Empty:
            return None

def start_subscriber(app, stream: LiveStream):
    broker = app.config["MQTT_BROKER_URL"]
    port = app.config["MQTT_BROKER_PORT"]
    client = mqtt.Client(protocol=mqtt.MQTTv311)

    def on_connect(c, u, f, rc):
        c.subscribe("telemetry/#")
        c.subscribe("status/+")

    def on_message(c, u, m):
        # Forward raw messages to browser for live view
        try:
            payload = json.loads(m.payload.decode("utf-8"))
        except Exception:
            return
        stream.push(json.dumps({"topic": m.topic, "payload": payload}))

    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(broker, port, 60)

    t = threading.Thread(target=lambda: client.loop_forever(retry_first_connection=True), daemon=True)
    t.start()
    return client
