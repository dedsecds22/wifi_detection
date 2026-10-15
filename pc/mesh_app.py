"""Mesh motion localisation app: live (UDP from the nodes) or simulated data, with a drag-and-drop
topology editor and a real-time multi-person map in the browser.

Usage:
  python mesh_app.py --sim              # no hardware: simulated people walking between 6 virtual nodes
  python mesh_app.py                    # live: nodes send UDP reports to port 5005 of this PC
Open http://127.0.0.1:8081
"""
import argparse
import json
import os
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import mesh_core
import mesh_sim

HERE = os.path.dirname(os.path.abspath(__file__))
TICK_S = 0.5


class App:
    def __init__(self, args):
        self.sim_mode = args.sim
        path = os.path.join(HERE, "topology_sim.json" if args.sim else "topology.json")
        self.topo = mesh_core.Topology(path)
        self.engine = mesh_core.Engine(self.topo)
        self.sim = None
        if args.sim:
            self.sim = mesh_sim.Simulator(self.topo, n_people=args.people)
            self.sim.apply_macs()
        self.cond = threading.Condition()
        self.seq = 0
        self.payload = b"{}"
        self.udp_port = args.udp_port

    def publish(self, state):
        state["mode"] = "sim" if self.sim_mode else "live"
        if self.sim:
            state["truth"] = self.sim.truth()
            state["sim_people"] = len(self.sim.people)
        with self.cond:
            self.seq += 1
            self.payload = json.dumps(state, separators=(",", ":")).encode()
            self.cond.notify_all()

    def tick_loop(self):
        nxt = time.time()
        while True:
            nxt += TICK_S
            if self.sim:
                for text in self.sim.step(time.time(), active=not self.engine.calibrating):
                    self.engine.ingest(mesh_core.parse_reports(text))
            self.publish(self.engine.update())
            time.sleep(max(0.0, nxt - time.time()))

    def udp_loop(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(("0.0.0.0", self.udp_port))
        while True:
            data, _ = sock.recvfrom(4096)
            self.engine.ingest(mesh_core.parse_reports(data.decode(errors="replace")))


def make_handler(app, port):
    allowed = {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _json(self, obj, code=200):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path in ("/", "/index.html"):
                with open(os.path.join(HERE, "mesh_app.html"), "rb") as f:
                    body = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif self.path == "/api/state":
                with app.cond:
                    body = app.payload
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif self.path == "/events":
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                last = 0
                try:
                    while True:
                        with app.cond:
                            if app.seq == last:
                                app.cond.wait(15)
                            seq, body = app.seq, app.payload
                        if seq == last:
                            self.wfile.write(b": ping\n\n")
                        else:
                            last = seq
                            self.wfile.write(b"data: " + body + b"\n\n")
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass
            else:
                self.send_error(404)

        def do_POST(self):
            origin = self.headers.get("Origin")
            if origin and origin not in allowed:
                self.send_error(403)
                return
            try:
                n = int(self.headers.get("Content-Length", 0))
                data = json.loads(self.rfile.read(n) or b"{}")
            except ValueError:
                self.send_error(400)
                return
            if self.path == "/api/topology":
                app.topo.update_nodes(data.get("nodes", []))
            elif self.path == "/api/grid":
                app.topo.reset_grid(int(data.get("cols", 3)), int(data.get("rows", 2)),
                                    float(data.get("dx", 2.0)), float(data.get("dy", 3.0)))
                if app.sim:
                    app.sim.apply_macs()
            elif self.path == "/api/calibrate":
                app.engine.calibrate()
            elif self.path == "/api/sim" and app.sim:
                app.sim.set_people(max(0, min(5, int(data.get("people", 1)))))
            else:
                self.send_error(404)
                return
            self._json({"ok": True})

    return Handler


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sim", action="store_true", help="simulated nodes and people, no hardware needed")
    ap.add_argument("--people", type=int, default=2, help="simulated people")
    ap.add_argument("--udp-port", type=int, default=5005)
    ap.add_argument("--http-port", type=int, default=8081)
    args = ap.parse_args()
    app = App(args)
    threading.Thread(target=app.tick_loop, daemon=True).start()
    if not args.sim:
        threading.Thread(target=app.udp_loop, daemon=True).start()
    srv = ThreadingHTTPServer(("127.0.0.1", args.http_port), make_handler(app, args.http_port))
    print(f"Mesh app: http://127.0.0.1:{args.http_port}  ({'simulation' if args.sim else 'live UDP ' + str(args.udp_port)})")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
