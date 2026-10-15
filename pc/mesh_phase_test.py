"""Records still / moving / still phases from several mesh nodes and ranks the link features.

Usage: python mesh_phase_test.py COM6 COM10
A beep marks each phase change. For every feature the script prints the moving/still ratio and
a separation score d' = |mean_moving - mean_still| / pooled_sd (higher is better).
"""
import csv
import statistics
import subprocess
import sys
import time
import winsound

ports = sys.argv[1:]
phases = [("still", 20), ("moving", 20), ("still2", 20)]
out = "phase_test.csv"
FEATURES = ["rssi", "turb_mean", "turb_var", "diff", "tvar", "amp"]

proc = subprocess.Popen([sys.executable, "mesh_serial_probe.py", *ports, "--seconds", str(sum(p[1] for p in phases) + 6), "--csv", out],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(5)  # port opening resets the boards; wait for the first reports
bounds = []
for name, dur in phases:
    start = time.time()
    winsound.Beep(1200, 600)
    print("PHASE", name, flush=True)
    time.sleep(dur)
    bounds.append((name, start, time.time()))
winsound.Beep(600, 600)
proc.wait()

rows = list(csv.DictReader(open(out)))
links = sorted({(r["rx"][-4:], r["tx"][-4:]) for r in rows})
print("\nlink, feature, mean_still, mean_moving, ratio, d'")
for link in links:
    def phase_vals(names, feat):
        vals = []
        for name, a, b in bounds:
            if name in names:
                vals += [float(r[feat]) for r in rows
                         if (r["rx"][-4:], r["tx"][-4:]) == link and a <= float(r["pc_time"]) < b]
        return vals
    for feat in FEATURES:
        s, m = phase_vals(("still", "still2"), feat), phase_vals(("moving",), feat)
        if len(s) < 3 or len(m) < 3:
            continue
        ms, mm = statistics.mean(s), statistics.mean(m)
        sd = ((statistics.pvariance(s) + statistics.pvariance(m)) / 2) ** 0.5 or 1e-9
        print("%s<-%s %-9s %10.6f %10.6f %6.2f %6.2f" % (link + (feat, ms, mm, mm / ms if ms else 0, abs(mm - ms) / sd)))
