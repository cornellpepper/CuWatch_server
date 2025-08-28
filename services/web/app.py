from flask import Flask, render_template, request, jsonify, Response, stream_with_context
from db import init_db, db
from models import Device, Sample
from config import Config
from sockets import init_sockets
from mqtt_publish import publish_control
from mqtt_subscribe import LiveStream, start_subscriber
from datetime import datetime, timezone

def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)
    init_db(app)

    # Live stream buffer + MQTT subscriber
    stream = LiveStream(maxlen=2000)
    start_subscriber(app, stream)
    init_sockets(app, stream)

    @app.route('/')
    def index():
        return render_template('dashboard.html')

    @app.route('/api/devices')
    def devices():
        rows = Device.query.order_by(Device.id).all()
        return jsonify([{
            'id': d.id,
            'online': d.online,
            'device_number': d.device_number,
            'last_seen': d.last_seen.isoformat() if d.last_seen else None
        } for d in rows])

    @app.route('/api/samples/<device_id>')
    def samples(device_id):
        # Optional query params: ?limit=1000&start=...&end=...
        limit = request.args.get('limit', default='500')
        try:
            limit = max(1, min(20000, int(limit)))
        except Exception:
            limit = 500

        def parse_time(x):
            if not x:
                return None
            try:
                if isinstance(x, str) and x.isdigit():
                    return datetime.fromtimestamp(int(x), tz=timezone.utc)
                return datetime.fromisoformat(x.replace('Z', '+00:00'))
            except Exception:
                return None

        start = parse_time(request.args.get('start'))
        end = parse_time(request.args.get('end'))

        q = Sample.query.filter_by(device_id=device_id)
        if start:
            q = q.filter(Sample.ts >= start)
        if end:
            q = q.filter(Sample.ts <= end)
        q = q.order_by(Sample.ts.desc()).limit(limit)
        rows = q.all()

        return jsonify([{
            'device_id': device_id,
            'ts': s.ts.isoformat() if s.ts else None,
            'device_number': s.device_number,
            'muon_count': s.muon_count,
            'adc_v': s.adc_v,
            'temp_adc_v': s.temp_adc_v,
            'dt': s.dt,
            'wait_cnt': s.wait_cnt,
            'coincidence': s.coincidence,
        } for s in rows])

    @app.get('/api/export/<device_id>.csv')
    def export_csv(device_id):
        # Optional ?start=...&end=...
        def parse_time(x):
            if not x:
                return None
            try:
                if isinstance(x, str) and x.isdigit():
                    return datetime.fromtimestamp(int(x), tz=timezone.utc)
                return datetime.fromisoformat(x.replace('Z', '+00:00'))
            except Exception:
                return None

        start = parse_time(request.args.get('start'))
        end = parse_time(request.args.get('end'))

        q = Sample.query.filter_by(device_id=device_id)
        if start:
            q = q.filter(Sample.ts >= start)
        if end:
            q = q.filter(Sample.ts <= end)
        q = q.order_by(Sample.ts.asc())

        if not start and not end:
            q = q.limit(10000)

        def gen():
            yield "device_id,ts,device_number,muon_count,adc_v,temp_adc_v,dt,wait_cnt,coincidence\n"
            for s in q.all():
                row = [
                    device_id,
                    (s.ts.isoformat() if s.ts else ""),
                    str(s.device_number),
                    str(s.muon_count),
                    str(s.adc_v),
                    str(s.temp_adc_v),
                    str(s.dt),
                    str(s.wait_cnt),
                    "true" if s.coincidence else "false",
                ]
                yield ",".join(row) + "\n"

        return Response(stream_with_context(gen()), mimetype="text/csv")

    @app.post('/api/control/<device_id>')
    def send_control(device_id):
        payload = request.get_json(force=True)
        publish_control(device_id, payload)
        return jsonify({'ok': True})

    @app.get('/healthz')
    def health():
        return {'status': 'ok'}

    with app.app_context():
        db.create_all()

    return app

app = create_app()
