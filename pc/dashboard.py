"""Live dashboard for ESPectre nodes (builds on collector.py).

Usage: python dashboard.py 192.168.0.202 192.168.0.48 [--port 8080]
Open http://127.0.0.1:8080 . Telemetry is also recorded to sessions/<time>/telemetry.csv.
"""
import argparse
import collections
import http.client
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import collector

HISTORY_S = 120
HERE = os.path.dirname(os.path.abspath(__file__))


class DashNode(collector.Node):
    def __init__(self, *args):
        super().__init__(*args)
        self.history = collections.deque()
        self.events = collections.deque(maxlen=200)
        self.sensing = {}
        self.wifi = {}
        self.device = {}
        self.health = {}
        self.last_status = None
        self.lock = threading.Lock()

    def log(self, text):
        self.events.appendleft({"t": time.time(), "text": text})

    def record(self, event):
        prev = self.last_state
        super().record(event)
        now = time.time()
        with self.lock:
            self.history.append((now, float(event["score"]), self.threshold, event["state"] == "motion"))
            while self.history and now - self.history[0][0] > HISTORY_S:
                self.history.popleft()
        if prev is not None and prev != event["state"]:
            self.log("Motion detected" if event["state"] == "motion" else "Motion ended")

    def refresh_sensing(self):
        self.sensing = self.get_json("/sensing")
        self.ready = bool(self.sensing.get("ready"))
        self.threshold = self.sensing.get("threshold")

    def poll_info(self):
        while True:
            for name, attr in (("/device", "device"), ("/wifi", "wifi"), ("/health", "health"), ("/sensing", "sensing")):
                try:
                    setattr(self, attr, self.get_json(name))
                except Exception:
                    pass
            if self.sensing:
                self.ready = bool(self.sensing.get("ready"))
                self.threshold = self.sensing.get("threshold")
            status = self.status()
            if status != self.last_status:
                self.log("Status: %s" % status)
                self.last_status = status
            time.sleep(3)

    def call(self, method, path, body=None):
        conn = http.client.HTTPConnection(self.host, collector.PORT, timeout=8)
        try:
            data = json.dumps(body) if body is not None else None
            conn.request(method, collector.BASE + path, body=data, headers={"Content-Type": "application/json"})
            response = conn.getresponse()
            return response.status, response.read().decode("utf-8", "replace")
        finally:
            conn.close()

    def snapshot(self):
        with self.lock:
            hist = [[round(t, 3), s, th, int(m)] for t, s, th, m in self.history]
        age = None if self.last_rx is None else round(time.monotonic() - self.last_rx, 1)
        return {
            "host": self.host,
            "device_id": self.device_id,
            "name": self.device.get("name"),
            "firmware": self.device.get("firmware"),
            "chip": self.device.get("chip"),
            "status": self.status(),
            "state": self.last_state,
            "score": self.last_score,
            "threshold": self.threshold,
            "ready": self.ready,
            "calibrating": self.sensing.get("calibrating"),
            "detector": self.sensing.get("detector"),
            "on_hits": self.sensing.get("motion_on_hits"),
            "off_hits": self.sensing.get("motion_off_hits"),
            "pps": self.sensing.get("csi_target_pps"),
            "rssi": self.wifi.get("rssi_dbm"),
            "channel": self.wifi.get("channel"),
            "ssid": self.wifi.get("ssid"),
            "uptime_s": self.health.get("uptime_s"),
            "samples": self.samples,
            "boots": self.boots,
            "reconnects": self.reconnects,
            "age": age,
            "history": hist,
            "events": list(self.events)[:40],
        }


class Handler(BaseHTTPRequestHandler):
    nodes = {}

    def log_message(self, *args):
        pass

    def send(self, code, body, ctype="application/json"):
        raw = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            with open(os.path.join(HERE, "dashboard.html"), "rb") as f:
                self.send(200, f.read(), "text/html; charset=utf-8")
        elif self.path == "/api/state":
            self.send(200, json.dumps({"now": time.time(), "nodes": [n.snapshot() for n in self.nodes.values()]}))
        else:
            self.send(404, "{}")

    def do_POST(self):
        parts = self.path.strip("/").split("/")
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            return self.send(400, '{"error":"bad json"}')
        if len(parts) != 4 or parts[:2] != ["api", "node"] or parts[2] not in self.nodes:
            return self.send(404, '{"error":"unknown node"}')
        node = self.nodes[parts[2]]
        try:
            if parts[3] == "threshold":
                value = float(body["threshold"])
                if not 0.0 <= value <= 1.0:
                    return self.send(400, '{"error":"threshold must be 0..1"}')
                status, text = node.call("PATCH", "/sensing", {"threshold": value})
                node.log("Threshold set to %.4f" % value)
            elif parts[3] == "recalibrate":
                status, text = node.call("POST", "/sensing/calibrations", {})
                node.log("Recalibration requested")
            else:
                return self.send(404, '{"error":"unknown action"}')
        except Exception as exc:
            return self.send(502, json.dumps({"error": str(exc)}))
        self.send(status, text)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("hosts", nargs="+")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--out", default="sessions")
    parser.add_argument("--stale", type=float, default=3.0)
    parser.add_argument("--offline", type=float, default=10.0)
    args = parser.parse_args()

    session = time.strftime("%Y%m%d_%H%M%S")
    folder = os.path.join(args.out, session)
    os.makedirs(folder, exist_ok=True)
    writer = collector.CsvWriter(os.path.join(folder, "telemetry.csv"))
    with open(os.path.join(folder, "session.json"), "w", encoding="utf-8") as f:
        json.dump({"started": session, "hosts": args.hosts}, f, indent=2)
    for host in args.hosts:
        node = DashNode(host, writer, args.stale, args.offline)
        Handler.nodes[host] = node
        node.start()
        threading.Thread(target=node.poll_info, daemon=True).start()

    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print("Dashboard: http://127.0.0.1:%d  (recording to %s)" % (args.port, folder))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        writer.close()


if __name__ == "__main__":
    main()
