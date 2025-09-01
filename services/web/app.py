from flask import Flask, render_template, request, jsonify, Response, stream_with_context, session, redirect, url_for, flash
from db import init_db, db
from models import Device, Sample
from config import Config
from sockets import init_sockets
from mqtt_publish import publish_control
from mqtt_subscribe import LiveStream, start_subscriber
from datetime import datetime, timezone
from functools import wraps

def login_required(view_func):
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login", next=request.path))
        return view_func(*args, **kwargs)
    return wrapped

def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    # Ensure SECRET_KEY for session cookies
    if not app.config.get("SECRET_KEY"):
        # Fallback for development; set in Config for production
        app.config["SECRET_KEY"] = "dev-change-me"

    init_db(app)

    # Live stream buffer + MQTT subscriber
    stream = LiveStream(maxlen=2000)
    start_subscriber(app, stream)
    init_sockets(app, stream)

    @app.route('/')
    def index():
        if session.get("logged_in"):
            return redirect(url_for('device_select'))
        return redirect(url_for('login'))

    @app.route('/login', methods=['GET', 'POST'])
    def login():
        # Credentials come from Config; if not provided, accept any non-empty username for dev.
        expected_user = app.config.get('LOGIN_USER')
        expected_pass = app.config.get('LOGIN_PASSWORD')

        if request.method == 'POST':
            username = request.form.get('username', '').strip()
            password = request.form.get('password', '')
            ok = False
            if expected_user and expected_pass:
                ok = (username == expected_user and password == expected_pass)
            else:
                # Dev mode: allow any non-empty username
                ok = bool(username)

            if ok:
                session['logged_in'] = True
                session['username'] = username
                nxt = request.args.get('next') or url_for('device_select')
                return redirect(nxt)
            else:
                flash('Invalid credentials', 'error')

        return render_template('login.html')

    @app.route('/logout')
    def logout():
        session.clear()
        return redirect(url_for('login'))

    @app.route('/devices')
    @login_required
    def device_select():
        rows = Device.query.order_by(Device.id).all()
        # Provide a simple page where a user picks a device
        return render_template('device_select.html', devices=[{
            'id': d.id,
            'online': d.online,
            'device_number': d.device_number,
            'last_seen': d.last_seen.isoformat() if d.last_seen else None
        } for d in rows])

    @app.route('/device/<device_id>')
    @login_required
    def device_detail(device_id):
        # The template should render the live chart via sockets and show CSV download links
        return render_template('device_detail.html', device_id=device_id)

    @app.get('/device/<device_id>/download')
    @login_required
    def download_page(device_id):
        # This page can show quick links to CSV with common ranges
        return render_template('download.html', device_id=device_id)

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
