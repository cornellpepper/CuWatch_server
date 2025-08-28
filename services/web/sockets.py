from flask_sock import Sock
sock = Sock()

def init_sockets(app, stream):
    sock.init_app(app)

    @sock.route("/ws")
    def ws(ws):
        # Send a small snapshot first
        for line in stream.snapshot(50):
            ws.send(line)
        # Then stream new messages
        while True:
            msg = stream.wait(timeout=1.0)
            if msg is not None:
                ws.send(msg)
            try:
                ws.receive(timeout=0.5)  # heartbeat
            except Exception:
                break
