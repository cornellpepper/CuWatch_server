import os
class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev")
    SQLALCHEMY_DATABASE_URI = os.environ.get("DATABASE_URL", "postgresql://postgres:postgres@db/iot")
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    MQTT_BROKER_URL = os.environ.get("MQTT_BROKER_URL", "mqtt-broker")
    MQTT_BROKER_PORT = int(os.environ.get("MQTT_BROKER_PORT", 1883))
    # Optional auth
    MQTT_USERNAME = os.environ.get("MQTT_USERNAME")
    MQTT_PASSWORD = os.environ.get("MQTT_PASSWORD")
