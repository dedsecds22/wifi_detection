"""Core of the mesh motion localisation: report parsing, per-link motion scores,
radio-tomographic imaging (RTI), peak finding and multi-person tracking.

Pure Python (no numpy). Positions are in metres, y grows "up" in the UI.
"""
import json
import math
import os
import threading
import time
from collections import namedtuple

Sample = namedtuple("Sample", "rx tx node_ms espnow_rx csi_n csi_len lost rssi turb_mean turb_var diff tvar amp")

FRESH_S = 3.0       # a direction is "fresh" if a report arrived within this time
DEAD_ZONE = 1.5     # z-score below this is treated as noise
MAX_SCORE = 6.0
CAL_SECONDS = 15.0


def parse_reports(text):
    """Parse "L," lines (14 fields) sent by the mesh firmware; ignore anything else."""
    out = []
    for line in text.splitlines():
        f = line.strip().split(",")
        if len(f) != 14 or f[0] != "L":
            continue
        try:
            out.append(Sample(f[1], f[2], int(f[3]), int(f[4]), int(f[5]), int(f[6]), int(f[7]), float(f[8]),
                              float(f[9]), float(f[10]), float(f[11]), float(f[12]), float(f[13])))
        except ValueError:
            continue
    return out


# ---------------------------------------------------------------- topology

def default_nodes(cols=3, rows=2, dx=2.0, dy=3.0):
    nodes = []
    for r in range(rows):
        for c in range(cols):
            nodes.append({"id": f"N{len(nodes) + 1}", "name": f"Node {len(nodes) + 1}", "mac": None,
                          "x": round(c * dx, 3), "y": round(r * dy, 3)})
    return nodes


class Topology:
    def __init__(self, path=None):
        self.path = path
        self.version = 0
        self.nodes = default_nodes()
        if path and os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
                if data.get("nodes"):
                    self.nodes = data["nodes"]
            except (OSError, ValueError):
                pass

    def save(self):
        if not self.path:
            return
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"nodes": self.nodes}, f, indent=1)

    def reset_grid(self, cols=3, rows=2, dx=2.0, dy=3.0):
        old = {n["id"]: n for n in self.nodes}
        self.nodes = default_nodes(cols, rows, dx, dy)
        for n in self.nodes:
            if n["id"] in old:
                n["mac"] = old[n["id"]].get("mac")
        self.version += 1
        self.save()

    def node_by_id(self, nid):
        for n in self.nodes:
            if n["id"] == nid:
                return n
        return None

    def node_for_mac(self, mac, assign=True):
        for n in self.nodes:
            if n.get("mac") == mac:
                return n
        if not assign:
            return None
        for n in self.nodes:
            if not n.get("mac"):
                n["mac"] = mac
                self.save()
                return n
        return None

    def update_nodes(self, items):
        for it in items:
            n = self.node_by_id(it.get("id"))
            if not n:
                continue
            if "x" in it and "y" in it:
                n["x"], n["y"] = float(it["x"]), float(it["y"])
            if "name" in it:
                n["name"] = str(it["name"])[:24]
            if "mac" in it:
                mac = it["mac"] or None
                if mac:
                    for other in self.nodes:
                        if other is not n and other.get("mac") == mac:
                            other["mac"] = None
                n["mac"] = mac
        self.version += 1
        self.save()

    def bbox(self):
        xs = [n["x"] for n in self.nodes]
        ys = [n["y"] for n in self.nodes]
        return min(xs), min(ys), max(xs), max(ys)


# ---------------------------------------------------------------- link statistics

class Feat:
    """Running mean/variance; pure averaging while learning (alpha=0), slow EMA afterwards."""
    __slots__ = ("n", "mean", "var", "floor")

    def __init__(self, floor):
        self.n = 0
        self.mean = 0.0
        self.var = 0.0
        self.floor = floor

    def update(self, x, alpha):
        self.n += 1
        a = max(1.0 / self.n, alpha)
        d = x - self.mean
        self.mean += a * d
        self.var = (1 - a) * (self.var + a * d * d)

    def z(self, x):
        return (x - self.mean) / max(math.sqrt(self.var), self.floor)


class Direction:
    """Statistics of one directed measurement (rx hears tx)."""

    def __init__(self):
        self.t = Feat(0.15)   # ln(temporal variance of subcarriers)
        self.d = Feat(0.08)   # ln(frame-to-frame difference)
        self.r = Feat(1.0)    # RSSI in dB
        self.last = 0.0
        self.z = 0.0
        self.rssi = None
        self.lost = 0

    def observe(self, s, now, learning):
        if s.csi_n < 3 or s.tvar <= 0 or s.diff <= 0:
            return
        xt, xd, xr = math.log(s.tvar), math.log(s.diff), s.rssi
        self.last = now
        self.rssi = s.rssi
        self.lost = s.lost
        z = 0.0
        if self.t.n >= 5:
            z = (self.t.z(xt) + self.d.z(xd) + 0.7 * max(0.0, -self.r.z(xr))) / 1.6
        self.z = z
        if learning or z < 2.0:
            alpha = 0.0 if learning else 0.01
            self.t.update(xt, alpha)
            self.d.update(xd, alpha)
            self.r.update(xr, alpha)


# ---------------------------------------------------------------- RTI

class RTI:
    """Backprojection on an ellipse model: a pixel is covered by a link if the detour d1+d2-d is small."""

    DETECT_THR = 1.2
    SUBTRACT = 1.0

    def __init__(self, res=0.1, sigma=0.25, pad=0.8):
        self.res = res
        self.sigma = sigma
        self.pad = pad
        self.links = {}
        self.w = self.h = 0
        self.x0 = self.y0 = 0.0

    def rebuild(self, nodes):
        xs = [n["x"] for n in nodes]
        ys = [n["y"] for n in nodes]
        self.x0, self.y0 = min(xs) - self.pad, min(ys) - self.pad
        self.w = int((max(xs) - min(xs) + 2 * self.pad) / self.res) + 1
        self.h = int((max(ys) - min(ys) + 2 * self.pad) / self.res) + 1
        self.links = {}
        self.link_w = {}
        cut = 2.8 * self.sigma
        for i, a in enumerate(nodes):
            for b in nodes[i + 1:]:
                d = math.hypot(a["x"] - b["x"], a["y"] - b["y"])
                if d < 0.2:
                    continue
                cells = []
                for iy in range(self.h):
                    py = self.y0 + iy * self.res
                    for ix in range(self.w):
                        px = self.x0 + ix * self.res
                        ex = math.hypot(px - a["x"], py - a["y"]) + math.hypot(px - b["x"], py - b["y"]) - d
                        if ex < cut:
                            cells.append((iy * self.w + ix, math.exp(-0.5 * (ex / self.sigma) ** 2)))
                self.links[(a["id"], b["id"])] = cells
                self.link_w[(a["id"], b["id"])] = dict(cells)

    def xy(self, idx):
        return self.x0 + (idx % self.w) * self.res, self.y0 + (idx // self.w) * self.res

    def image(self, scores):
        num = [0.0] * (self.w * self.h)
        den = [0.0] * (self.w * self.h)
        for key, s in scores.items():
            cells = self.links.get(key)
            if cells is None or s is None:
                continue
            for i, w in cells:
                num[i] += w * s
                den[i] += w
        heat = [0.0] * len(num)
        for i, dn in enumerate(den):
            if dn > 0:
                heat[i] = num[i] / (dn + 0.3) * min(1.0, dn / 2.0)
        return heat

    def detect(self, scores, thr=None, max_n=4):
        thr = self.DETECT_THR if thr is None else thr
        # CLEAN-style: take the strongest peak, remove what it explains from the link scores, repeat.
        res = {k: s for k, s in scores.items() if s is not None}
        found = []
        for _ in range(max_n):
            heat = self.image(res)
            pk = self.peaks(heat, thr, 0.0, 1)
            if not pk:
                break
            found.append(pk[0])
            idx = int(round((pk[0][1] - self.y0) / self.res)) * self.w + int(round((pk[0][0] - self.x0) / self.res))
            for k in res:
                w = self.link_w.get(k, {}).get(idx, 0.0)
                res[k] = max(0.0, res[k] - pk[0][2] * self.SUBTRACT * w)
        return found

    def peaks(self, heat, thr=1.0, min_dist=0.9, max_n=4):
        order = sorted((i for i, v in enumerate(heat) if v >= thr), key=lambda i: -heat[i])
        found = []
        r = int(0.45 / self.res)
        for i in order:
            x, y = self.xy(i)
            if any(math.hypot(x - p[0], y - p[1]) < min_dist for p in found):
                continue
            cx, cy = i % self.w, i // self.w
            sw = sx = sy = 0.0
            for yy in range(max(0, cy - r), min(self.h, cy + r + 1)):
                for xx in range(max(0, cx - r), min(self.w, cx + r + 1)):
                    v = heat[yy * self.w + xx]
                    if v > 0.5 * heat[i]:
                        sw += v
                        sx += v * (self.x0 + xx * self.res)
                        sy += v * (self.y0 + yy * self.res)
            found.append((sx / sw, sy / sw, heat[i]))
            if len(found) >= max_n:
                break
        return found


# ---------------------------------------------------------------- tracker

class Track:
    def __init__(self, tid, x, y):
        self.id = tid
        self.x, self.y = x, y
        self.vx = self.vy = 0.0
        self.hits = 1
        self.miss = 0
        self.strength = 0.0
        self.trail = [(x, y)]


class Tracker:
    ALPHA, BETA, GATE, MAX_MISS, CONFIRM = 0.6, 0.15, 1.2, 6, 3

    def __init__(self):
        self.tracks = []
        self.next_id = 1
        self.t = None

    def update(self, dets, now):
        dt = 0.5 if self.t is None else min(max(now - self.t, 0.1), 2.0)
        self.t = now
        for tr in self.tracks:
            tr.x += tr.vx * dt
            tr.y += tr.vy * dt
        pairs = sorted((math.hypot(tr.x - d[0], tr.y - d[1]), ti, di)
                       for ti, tr in enumerate(self.tracks) for di, d in enumerate(dets))
        used_t, used_d = set(), set()
        for dist, ti, di in pairs:
            if dist > self.GATE or ti in used_t or di in used_d:
                continue
            used_t.add(ti)
            used_d.add(di)
            tr = self.tracks[ti]
            rx, ry = dets[di][0] - tr.x, dets[di][1] - tr.y
            tr.x += self.ALPHA * rx
            tr.y += self.ALPHA * ry
            tr.vx += self.BETA * rx / dt
            tr.vy += self.BETA * ry / dt
            tr.hits += 1
            tr.miss = 0
            tr.strength = dets[di][2]
            tr.trail.append((tr.x, tr.y))
            del tr.trail[:-30]
        for ti, tr in enumerate(self.tracks):
            if ti not in used_t:
                tr.miss += 1
                tr.vx *= 0.5
                tr.vy *= 0.5
        for di, d in enumerate(dets):
            if di not in used_d:
                tr = Track(self.next_id, d[0], d[1])
                tr.strength = d[2]
                self.next_id += 1
                self.tracks.append(tr)
        self.tracks = [t for t in self.tracks
                       if t.miss <= (self.MAX_MISS if t.hits >= self.CONFIRM else 1)]
        return [t for t in self.tracks if t.hits >= self.CONFIRM]


# ---------------------------------------------------------------- engine

class Engine:
    def __init__(self, topo, clock=time.time):
        self.topo = topo
        self.clock = clock
        self.lock = threading.Lock()
        self.dirs = {}          # (rx_node_id, tx_node_id) -> Direction
        self.link_score = {}    # (id_a, id_b) sorted -> smoothed score
        self.rti = RTI()
        self.rti.rebuild(topo.nodes)
        self.topo_version = topo.version
        self.tracker = Tracker()
        self.known_macs = set()
        self.cal_until = 0.0
        self.calibrate(CAL_SECONDS)

    @property
    def calibrating(self):
        return self.clock() < self.cal_until

    def calibrate(self, seconds=CAL_SECONDS):
        with self.lock:
            self.dirs.clear()
            self.link_score.clear()
            self.tracker = Tracker()
            self.cal_until = self.clock() + seconds

    def ingest(self, samples, now=None):
        now = self.clock() if now is None else now
        learning = now < self.cal_until
        with self.lock:
            for s in samples:
                self.known_macs.add(s.rx)
                self.known_macs.add(s.tx)
                a = self.topo.node_for_mac(s.rx)
                b = self.topo.node_for_mac(s.tx)
                if not a or not b or a is b:
                    continue
                d = self.dirs.setdefault((a["id"], b["id"]), Direction())
                d.observe(s, now, learning)

    def update(self, now=None):
        now = self.clock() if now is None else now
        with self.lock:
            if self.topo_version != self.topo.version:
                self.rti.rebuild(self.topo.nodes)
                self.topo_version = self.topo.version
            nodes = self.topo.nodes
            links = []
            scores = {}
            for i, a in enumerate(nodes):
                for b in nodes[i + 1:]:
                    key = (a["id"], b["id"])
                    zs, rssis = [], []
                    for dk in ((a["id"], b["id"]), (b["id"], a["id"])):
                        d = self.dirs.get(dk)
                        if d and now - d.last < FRESH_S:
                            zs.append(d.z)
                            rssis.append(d.rssi)
                    if zs:
                        raw = min(MAX_SCORE, max(0.0, sum(zs) / len(zs) - DEAD_ZONE))
                        s = 0.4 * self.link_score.get(key, raw) + 0.6 * raw
                        self.link_score[key] = s
                        scores[key] = s
                    links.append({"a": a["id"], "b": b["id"], "score": scores.get(key),
                                  "rssi": sum(rssis) / len(rssis) if rssis else None})
            fresh_nodes = set()
            for (rx, tx), d in self.dirs.items():
                if now - d.last < FRESH_S:
                    fresh_nodes.add(rx)
                    fresh_nodes.add(tx)
            heat = self.rti.image(scores) if not self.calibrating else [0.0] * (self.rti.w * self.rti.h)
            dets = self.rti.detect(scores) if not self.calibrating else []
            people = self.tracker.update(dets, now)
            return {
                "t": now,
                "calibrating": self.calibrating,
                "cal_left": max(0.0, self.cal_until - now),
                "nodes": [{**n, "fresh": n["id"] in fresh_nodes} for n in nodes],
                "links": links,
                "people": [{"id": t.id, "x": round(t.x, 3), "y": round(t.y, 3),
                            "vx": round(t.vx, 3), "vy": round(t.vy, 3), "strength": round(t.strength, 2),
                            "trail": [(round(x, 2), round(y, 2)) for x, y in t.trail]} for t in people],
                "heat": {"w": self.rti.w, "h": self.rti.h, "x0": self.rti.x0, "y0": self.rti.y0,
                         "res": self.rti.res, "data": [min(255, int(v * 50)) for v in heat]},
                "known_macs": sorted(self.known_macs),
            }
