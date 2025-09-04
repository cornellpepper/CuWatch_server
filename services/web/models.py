from db import db
from sqlalchemy import func

class Device(db.Model):
    __tablename__ = "devices"
    id = db.Column(db.String, primary_key=True)  # device_id
    last_seen = db.Column(db.DateTime(timezone=True))
    online = db.Column(db.Boolean, default=False)
    device_number = db.Column(db.Integer)  # optional mapping at device-level
    meta = db.Column(db.JSON, default=dict)

class Run(db.Model):
    __tablename__ = "runs"
    id = db.Column(db.BigInteger, primary_key=True, autoincrement=True)
    device_id = db.Column(db.String, db.ForeignKey("devices.id"), index=True, nullable=False)
    base_ts = db.Column(db.DateTime(timezone=True), index=True, nullable=False)
    run_key = db.Column(db.String)  # optional device-supplied run identifier
    meta = db.Column(db.String)     # stored as JSON string by the bridge

class Sample(db.Model):
    __tablename__ = "samples"
    id = db.Column(db.BigInteger, primary_key=True, autoincrement=True)
    device_id = db.Column(db.String, db.ForeignKey("devices.id"), index=True, nullable=False)
    ts = db.Column(db.DateTime(timezone=True), server_default=func.now(), index=True, nullable=False)
    device_number = db.Column(db.Integer, nullable=False)
    muon_count = db.Column(db.Integer, nullable=False)
    adc_v = db.Column(db.Integer, nullable=False)
    temp_adc_v = db.Column(db.Integer, nullable=False)
    dt = db.Column(db.Integer, nullable=False)
    wait_cnt = db.Column(db.Integer, nullable=False)
    coincidence = db.Column(db.Boolean, nullable=False)
