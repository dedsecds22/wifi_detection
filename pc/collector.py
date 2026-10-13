"""ESPectre multi-node collector (P1/P2).

Connects to one or more ESPectre Direct HTTP nodes, reads the SSE `motion`
stream and writes timestamped samples to telemetry.csv. Missing data is never
turned into zero motion: a silent node becomes STALE, then OFFLINE.

Usage: python collector.py 192.168.0.202 [192.168.0.203 ...] [--out sessions]
"""
import argparse
import csv
import http.client
import json
import os
import threading
import time
import uuid

PORT = 62587
BASE = "/espectre/v1"
FIELDS = [
    "schema_version", "device_id", "boot_id", "sequence", "device_time_ms",
    "pc_receive_time_ms", "pc_receive_iso", "ready", "calibrated",
    "motion_detected", "motion_score", "threshold", "wifi_connected",
]


class Node(threading.Thread):
    def __init__(self, host, writer, stale_s, offline_s):
        super().__init__(daemon=True)
        self.host = host
        self.writer = writer
        self.stale_s = stale_s
        self.offline_s = offline_s
        self.device_id = None
        self.boot_id = None
        self.sequence = 0
        self.last_device_ms = None
        self.last_rx = None
        self.last_score = None
        self.last_state = None
        self.ready = False
        self.threshold = None
        self.samples = 0
        self.boots = 0
        self.reconnects = 0

    def get_json(self, path):
        conn = http.client.HTTPConnection(self.host, PORT, timeout=5)
        try:
            conn.request("GET", BASE + path)
            return json.loads(conn.getresponse().read())
        finally:
            conn.close()

    def refresh_sensing(self):
        sensing = self.get_json("/sensing")
        self.ready = bool(sensing.get("ready"))
        self.threshold = sensing.get("threshold")

    def new_boot(self):
        self.boot_id = uuid.uuid4().hex[:8]
        self.sequence = 0
        self.last_device_ms = None
        self.boots += 1

    def status(self):
        if self.last_rx is None:
            return "OFFLINE"
        age = time.monotonic() - self.last_rx
        if age > self.offline_s:
            return "OFFLINE"
        if age > self.stale_s:
            return "STALE"
        return "ONLINE" if self.ready else "NOT_READY"

    def record(self, event):
        device_ms = int(event["timestamp_ms"])
        if self.last_device_ms is not None and device_ms < self.last_device_ms:
            self.new_boot()  # device clock went backwards: the node rebooted
        self.last_device_ms = device_ms
        self.sequence += 1
        now = time.time()
        self.last_rx = time.monotonic()
        self.last_score = float(event["score"])
        self.last_state = event["state"]
        self.samples += 1
        self.writer.write({
            "schema_version": 1,
            "device_id": self.device_id,
            "boot_id": self.boot_id,
            "sequence": self.sequence,
            "device_time_ms": device_ms,
            "pc_receive_time_ms": int(now * 1000),
            "pc_receive_iso": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now)),
            "ready": self.ready,
            "calibrated": self.ready,
            "motion_detected": event["state"] == "motion",
            "motion_score": event["score"],
            "threshold": self.threshold,
            "wifi_connected": True,
        })

    def stream(self):
        self.device_id = self.get_json("/device")["device_id"]
        self.new_boot()
        self.refresh_sensing()
        conn = http.client.HTTPConnection(self.host, PORT, timeout=self.offline_s)
        conn.request("GET", BASE + "/events", headers={"Accept": "text/event-stream"})
        response = conn.getresponse()
        if response.status != 200:
            raise OSError("events HTTP %d" % response.status)
        name = None
        next_refresh = time.monotonic() + 5
        while True:
            line = response.readline()
            if not line:
                raise OSError("stream closed")
            line = line.decode("utf-8", "replace").rstrip("\r\n")
            if line.startswith("event:"):
                name = line[6:].strip()
            elif line.startswith("data:"):
                data = json.loads(line[5:])
                if name == "motion":
                    self.record(data)
                elif name == "sensing":
                    self.ready = bool(data.get("ready"))
                    self.threshold = data.get("threshold")
            if time.monotonic() >= next_refresh:
                self.refresh_sensing()
                next_refresh = time.monotonic() + 5

    def run(self):
        while True:
            try:
                self.stream()
            except Exception:
                pass
            self.reconnects += 1
            time.sleep(2)


class CsvWriter:
    def __init__(self, path):
        self.lock = threading.Lock()
        self.file = open(path, "w", newline="", encoding="utf-8")
        self.csv = csv.DictWriter(self.file, fieldnames=FIELDS)
        self.csv.writeheader()
        self.rows = 0

    def write(self, row):
        with self.lock:
            self.csv.writerow(row)
            self.rows += 1
            if self.rows % 20 == 0:
                self.file.flush()

    def close(self):
        with self.lock:
            self.file.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("hosts", nargs="+")
    parser.add_argument("--out", default="sessions")
    parser.add_argument("--stale", type=float, default=3.0, help="seconds without data -> STALE")
    parser.add_argument("--offline", type=float, default=10.0, help="seconds without data -> OFFLINE")
    parser.add_argument("--duration", type=float, default=0, help="stop after N seconds (0 = run until Ctrl+C)")
    args = parser.parse_args()

    session = time.strftime("%Y%m%d_%H%M%S")
    folder = os.path.join(args.out, session)
    os.makedirs(folder, exist_ok=True)
    writer = CsvWriter(os.path.join(folder, "telemetry.csv"))
    nodes = [Node(h, writer, args.stale, args.offline) for h in args.hosts]
    with open(os.path.join(folder, "session.json"), "w", encoding="utf-8") as f:
        json.dump({"started": session, "hosts": args.hosts, "stale_s": args.stale,
                   "offline_s": args.offline}, f, indent=2)
    for node in nodes:
        node.start()

    print("Writing", folder)
    started = time.monotonic()
    try:
        while args.duration == 0 or time.monotonic() - started < args.duration:
            time.sleep(2)
            for n in nodes:
                score = "-" if n.last_score is None else "%.4f" % n.last_score
                print("%-16s %-10s id=%s state=%-6s score=%s samples=%d boots=%d reconnects=%d" % (
                    n.host, n.status(), n.device_id, n.last_state, score, n.samples, n.boots, n.reconnects))
            print()
    except KeyboardInterrupt:
        pass
    finally:
        writer.close()


if __name__ == "__main__":
    main()
