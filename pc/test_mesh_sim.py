"""Headless end-to-end test: simulator -> report text -> parser -> engine -> RTI -> tracker.

Runs on virtual time. Prints the localisation error of the tracks against the simulated ground truth.
Usage: python test_mesh_sim.py [people] [seconds]
"""
import math
import sys

import mesh_core
import mesh_sim


def run(n_people, seconds, seed=1, verbose=True):
    clock = [1000.0]
    topo = mesh_core.Topology()
    sim = mesh_sim.Simulator(topo, n_people=n_people, seed=seed)
    sim.apply_macs()
    eng = mesh_core.Engine(topo, clock=lambda: clock[0])
    errs, counts = [], []
    steps = int(seconds / 0.5)
    for i in range(steps):
        clock[0] += 0.5
        active = not eng.calibrating
        for text in sim.step(clock[0], active=active):
            eng.ingest(mesh_core.parse_reports(text), now=clock[0])
        st = eng.update(now=clock[0])
        if st["calibrating"] or i < int((mesh_core.CAL_SECONDS + 10) / 0.5):
            continue
        truth = sim.truth()
        counts.append((len(truth), len(st["people"])))
        for p in st["people"]:
            if truth:
                errs.append(min(math.hypot(p["x"] - t["x"], p["y"] - t["y"]) for t in truth))
    if not errs:
        return None
    errs.sort()
    res = {"median": errs[len(errs) // 2], "p90": errs[int(len(errs) * 0.9)],
           "count_ok": sum(1 for a, b in counts if a == b) / len(counts), "n": len(errs)}
    if verbose:
        print(f"people={n_people}: median err {res['median']:.2f} m, p90 {res['p90']:.2f} m, "
              f"correct count {res['count_ok'] * 100:.0f}% ({res['n']} track samples)")
    return res


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    secs = float(sys.argv[2]) if len(sys.argv) > 2 else 120
    run(n, secs)
