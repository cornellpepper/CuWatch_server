from flask import Flask, render_template, request, jsonify, Response, stream_with_context, session, redirect, url_for, flash
from db import init_db, db
from models import Device, Sample, Run
from config import Config
from sockets import init_sockets
from mqtt_publish import publish_control
from mqtt_subscribe import LiveStream, start_subscriber
from sar_parser import SARParser
from datetime import datetime, timezone, timedelta
from functools import wraps # noqa: F401
import psutil

def login_required(view_func):
    # Authentication disabled: pass-through decorator
    return view_func

def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)
    app.jinja_env.auto_reload = True
    # Ensure SECRET_KEY for session cookies
    if not app.config.get("SECRET_KEY"):
        # Fallback for development; set in Config for production
        app.config["SECRET_KEY"] = "dev-change-me"

    init_db(app)

    # Live stream buffer + MQTT subscriber
    stream = LiveStream(maxlen=2000)
    mqtt_client = start_subscriber(app, stream)
    init_sockets(app, stream)
    
    # Store references for health monitoring
    app.mqtt_client = mqtt_client
    app.live_stream = stream

    @app.route('/')
    def index():
        return redirect(url_for('device_select'))

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
        now = datetime.now(timezone.utc)
        return render_template('device_select.html', devices=[{
            'id': d.id,
            'online': (d.last_seen is not None and (now - d.last_seen) <= timedelta(minutes=5)),
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
        # Compute start-of-today in UTC for a convenient quick link
        today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        today_iso = today_start.isoformat().replace('+00:00', 'Z')
        return render_template('download.html', device_id=device_id, today_iso=today_iso)

    @app.get('/device/<device_id>/runs')
    @login_required
    def device_runs_page(device_id):
        # Simple page that lists known runs (base_ts, run_key, meta) for this device
        return render_template('device_runs.html', device_id=device_id)

    @app.route('/device/<device_id>/analyze')
    @login_required
    def device_analyze(device_id):
        """Interactive plotting and analysis page for device data"""
        device = db.session.get(Device, device_id)
        if not device:
            from flask import abort
            abort(404)
        
        # Get recent runs for dropdown (limit 50 for performance)
        runs = db.session.query(Run).filter_by(device_id=device_id)\
            .order_by(Run.base_ts.desc()).limit(50).all()
        
        return render_template('device_analyze.html', 
                              device=device,
                              device_id=device_id,
                              runs=runs)

    @app.route('/api/devices')
    def devices():
        rows = Device.query.order_by(Device.id).all()
        now = datetime.now(timezone.utc)
        return jsonify([{
            'id': d.id,
            'online': (d.last_seen is not None and (now - d.last_seen) <= timedelta(minutes=5)),
            'device_number': d.device_number,
            'last_seen': d.last_seen.isoformat() if d.last_seen else None
        } for d in rows])

    @app.get('/api/device/<device_id>/meta')
    def device_meta(device_id):
        d = Device.query.filter_by(id=device_id).first()
        if not d:
            return jsonify({"error": "not found"}), 404
        meta = d.meta
        if isinstance(meta, str):
            try:
                import json as _json
                meta = _json.loads(meta)
            except Exception:
                pass
        now = datetime.now(timezone.utc)
        return jsonify({
            "id": d.id,
            "last_seen": d.last_seen.isoformat() if d.last_seen else None,
            'online': (d.last_seen is not None and (now - d.last_seen) <= timedelta(minutes=5)),
            "device_number": d.device_number,
            "meta": meta,
        })

    @app.get('/api/device/<device_id>/runs')
    def device_runs(device_id):
        rows = Run.query.filter_by(device_id=device_id).order_by(Run.base_ts.desc()).all()
        return jsonify([
            {
                "id": r.id,
                "device_id": r.device_id,
                "base_ts": r.base_ts.isoformat() if r.base_ts else None,
                "run_key": r.run_key,
                "meta": r.meta,
            }
            for r in rows
        ])

    @app.route('/api/samples/<device_id>')
    def samples(device_id):
        # Optional query params: ?limit=1000&start=...&end=...
        limit = request.args.get('limit', default='500')
        try:
            # Allow up to 50k rows for JSON samples to support the event picker
            limit = max(1, min(50000, int(limit)))
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
        # Deterministic ordering: ts desc, then muon_count desc as tie-breaker
        q = q.order_by(Sample.ts.desc(), Sample.muon_count.desc()).limit(limit)
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
        # Deterministic ordering for export: ts asc, then muon_count asc
        q = q.order_by(Sample.ts.asc(), Sample.muon_count.asc())

        if not start and not end:
            q = q.limit(50000)

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

        headers = {'Content-Disposition': f'attachment; filename="{device_id}.csv"'}
        return Response(stream_with_context(gen()), mimetype="text/csv", headers=headers)

    @app.post('/api/control/<device_id>')
    @login_required
    def send_control(device_id):
        # Accept a JSON payload of control parameters.
        # If a 'threshold' is provided, validate it's a 12-bit integer [0..4095].
        payload = request.get_json(force=True, silent=True) or {}

        if 'threshold' in payload:
            try:
                thr = int(payload['threshold'])
            except Exception:
                return jsonify({'ok': False, 'error': 'threshold must be an integer'}), 400
            if thr < 0 or thr > 4095:
                return jsonify({'ok': False, 'error': 'threshold must be below 4095'}), 400
            # Normalize to int in payload
            payload['threshold'] = thr

        if 'reset_threshold' in payload:
            try:
                reset_thr = int(payload['reset_threshold'])
            except Exception:
                return jsonify({'ok': False, 'error': 'reset_threshold must be an integer'}), 400
            if reset_thr < 0 or reset_thr > 4095:
                return jsonify({'ok': False, 'error': 'reset_threshold must be between 0 and 4095'}), 400

            # Determine the effective threshold: from payload or device meta
            effective_threshold = payload.get('threshold')
            if effective_threshold is None:
                device = Device.query.filter_by(id=device_id).first()
                if device and device.meta:
                    try:
                        import json as _json
                        meta = _json.loads(device.meta) if isinstance(device.meta, str) else device.meta
                        effective_threshold = meta.get('metrics', {}).get('threshold')
                    except Exception:
                        effective_threshold = None

            # Validate reset_threshold < threshold
            if effective_threshold is not None:
                try:
                    effective_threshold = int(effective_threshold)
                    if reset_thr >= effective_threshold:
                        return jsonify({'ok': False, 'error': 'reset_threshold must be strictly less than threshold'}), 400
                except (ValueError, TypeError):
                    pass

            # Normalize to int in payload
            payload['reset_threshold'] = reset_thr

        publish_control(device_id, payload, retain=False)
        return jsonify({'ok': True})

    @app.get('/healthz')
    def health():
        return {'status': 'ok'}

    @app.get('/system')
    @login_required
    def system_health():
        """System health monitoring page"""
        return render_template('system_health.html')

    @app.get('/api/system/health')
    def system_health_api():
        """System health metrics API"""
        import time
        import json as _json
        
        # CPU and Memory
        cpu_percent = psutil.cpu_percent(interval=0.1)
        memory = psutil.virtual_memory()
        
        # Database health
        db_healthy = False
        db_error = None
        total_samples = 0
        total_devices = 0
        try:
            total_devices = db.session.query(Device).count()
            total_samples = db.session.query(Sample).count()
            db_healthy = True
        except Exception as e:
            db_error = str(e)
        
        # MQTT client status
        mqtt_connected = False
        if hasattr(app, 'mqtt_client') and app.mqtt_client:
            mqtt_connected = app.mqtt_client.is_connected()
        
        # Live stream buffer stats
        stream_buffer_size = 0
        if hasattr(app, 'live_stream') and app.live_stream:
            stream_buffer_size = len(app.live_stream._buf)
        
        # Process info
        process = psutil.Process()
        uptime = time.time() - process.create_time()
        
        return jsonify({
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'system': {
                'cpu_percent': round(cpu_percent, 1),
                'memory_percent': round(memory.percent, 1),
                'memory_used_mb': round(memory.used / 1024 / 1024, 1),
                'memory_total_mb': round(memory.total / 1024 / 1024, 1),
            },
            'process': {
                'uptime_seconds': round(uptime, 1),
                'memory_mb': round(process.memory_info().rss / 1024 / 1024, 1),
                'threads': process.num_threads(),
            },
            'database': {
                'healthy': db_healthy,
                'error': db_error,
                'total_devices': total_devices,
                'total_samples': total_samples,
            },
            'mqtt': {
                'connected': mqtt_connected,
                'stream_buffer_size': stream_buffer_size,
            }
        })

    @app.get('/api/system/sar/cpu')
    def sar_cpu():
        """SAR CPU usage history"""
        days_back = request.args.get('days_back', default='0', type=int)
        data = SARParser.get_cpu_history(days_back=days_back)
        return jsonify({
            'metric_type': 'cpu',
            'days_back': days_back,
            'samples': len(data),
            'data': data
        })

    @app.get('/api/system/sar/memory')
    def sar_memory():
        """SAR memory usage history"""
        days_back = request.args.get('days_back', default='0', type=int)
        data = SARParser.get_memory_history(days_back=days_back)
        return jsonify({
            'metric_type': 'memory',
            'days_back': days_back,
            'samples': len(data),
            'data': data
        })

    @app.get('/api/system/sar/disk')
    def sar_disk():
        """SAR disk I/O history"""
        days_back = request.args.get('days_back', default='0', type=int)
        data = SARParser.get_disk_io_history(days_back=days_back)
        return jsonify({
            'metric_type': 'disk_io',
            'days_back': days_back,
            'samples': len(data),
            'data': data
        })

    with app.app_context():
        db.create_all()

    return app

app = create_app()
