"""Simulator of the mesh: produces the same "L," report text as the firmware, so the whole PC pipeline
(parsing, link scores, RTI, tracking) can be tested without any hardware.

Model: a person shadows a link in proportion to exp(-0.5 * (detour / 0.25 m)^2), where
detour = |p-a| + |p-b| - |a-b|. Shadowing raises the subcarrier temporal variance (tvar), the frame
difference and lowers RSSI, with noise sized like the real two-node measurements.
"""
import math
import random


def fake_mac(index):
    return "5e00000000%02x" % (index + 1)


class Simulator:
    def __init__(self, topo, n_people=2, seed=None):
        self.topo = topo
        self.rng = random.Random(seed)
        self.people = []
        self.base = {}
        self.t = None
        self.set_people(n_people)

    def macs(self):
        return {n["id"]: fake_mac(i) for i, n in enumerate(self.topo.nodes)}

    def apply_macs(self):
        for n, mac in zip(self.topo.nodes, self.macs().values()):
            n["mac"] = mac

    def _bounds(self):
        x0, y0, x1, y1 = self.topo.bbox()
        m = 0.3
        return x0 + m, y0 + m, x1 - m, y1 - m

    def set_people(self, n):
        x0, y0, x1, y1 = self._bounds()
        while len(self.people) < n:
            sp, ang = self.rng.uniform(0.4, 0.9), self.rng.uniform(0, 2 * math.pi)
            self.people.append({"x": self.rng.uniform(x0, x1), "y": self.rng.uniform(y0, y1),
                                "vx": sp * math.cos(ang), "vy": sp * math.sin(ang)})
        del self.people[n:]

    def _move(self, dt):
        x0, y0, x1, y1 = self._bounds()
        for p in self.people:
            if self.rng.random() < 0.05:
                sp, ang = self.rng.uniform(0.4, 0.9), self.rng.uniform(0, 2 * math.pi)
                p["vx"], p["vy"] = sp * math.cos(ang), sp * math.sin(ang)
            p["x"] += p["vx"] * dt
            p["y"] += p["vy"] * dt
            if not x0 <= p["x"] <= x1:
                p["vx"] = -p["vx"]
                p["x"] = min(max(p["x"], x0), x1)
            if not y0 <= p["y"] <= y1:
                p["vy"] = -p["vy"]
                p["y"] = min(max(p["y"], y0), y1)

    def _shadow(self, a, b):
        d = math.hypot(a["x"] - b["x"], a["y"] - b["y"])
        g = 0.0
        for p in self.people:
            ex = math.hypot(p["x"] - a["x"], p["y"] - a["y"]) + math.hypot(p["x"] - b["x"], p["y"] - b["y"]) - d
            g += math.exp(-0.5 * (ex / 0.25) ** 2)
        return min(g, 1.5)

    def step(self, now, active=True):
        """Return one report text per receiving node (a list of strings)."""
        dt = 0.5 if self.t is None else min(now - self.t, 2.0)
        self.t = now
        if active:
            self._move(dt)
        macs = self.macs()
        nodes = self.topo.nodes
        out = []
        for rx in nodes:
            lines = []
            for tx in nodes:
                if tx is rx:
                    continue
                key = (rx["id"], tx["id"])
                if key not in self.base:
                    d = math.hypot(rx["x"] - tx["x"], rx["y"] - tx["y"])
                    self.base[key] = (math.log(self.rng.uniform(0.0008, 0.002)),
                                      math.log(self.rng.uniform(0.025, 0.045)),
                                      -28 - 12 * math.log10(max(d, 0.5)) + self.rng.uniform(-2, 2))
                bt, bd, br = self.base[key]
                g = self._shadow(rx, tx) if active else 0.0
                tvar = math.exp(bt + 0.7 * g + self.rng.gauss(0, 0.25))
                diff = math.exp(bd + 0.25 * g + self.rng.gauss(0, 0.1))
                rssi = br - 1.7 * g + self.rng.gauss(0, 0.8)
                n = self.rng.randint(12, 16)
                lines.append("L,%s,%s,%d,%d,%d,128,%d,%.1f,0.1,0.0001,%.6f,%.6f,45.0" % (
                    macs[rx["id"]], macs[tx["id"]], int(now * 1000) & 0xFFFFFFFF, n, n,
                    16 - n, rssi, diff, tvar))
            lines.append("T,16,0")
            out.append("\n".join(lines) + "\n")
        return out

    def truth(self):
        return [{"x": round(p["x"], 3), "y": round(p["y"], 3)} for p in self.people]
