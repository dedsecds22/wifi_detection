"""UDP collector for mesh nodes: receives "L," link reports on port 5005 and logs them.

Usage: python mesh_udp_collector.py [--seconds N] [--csv out.csv]
Prints a per-link summary every 2 s (rate of reports, mean RSSI, last turbulence).
"""
import argparse
import socket
import time

ap = argparse.ArgumentParser()
ap.add_argument("--port", type=int, default=5005)
ap.add_argument("--seconds", type=float, default=0)
ap.add_argument("--csv")
args = ap.parse_args()

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind(("0.0.0.0", args.port))
sock.settimeout(0.5)
csv = open(args.csv, "w") if args.csv else None
if csv:
    csv.write("pc_time,rx,tx,node_ms,espnow_rx,csi_n,csi_len,lost,rssi,turb_mean,turb_var,diff,tvar,amp,src_ip\n")

stats = {}
t0 = last = time.time()
print(f"listening on UDP {args.port}")
while args.seconds <= 0 or time.time() - t0 < args.seconds:
    try:
        data, addr = sock.recvfrom(2048)
    except socket.timeout:
        data = None
    if data:
        for line in data.decode(errors="replace").splitlines():
            f = line.split(",")
            if f[0] != "L" or len(f) != 14:
                continue
            key = (f[1], f[2])
            s = stats.setdefault(key, {"n": 0, "rssi": 0.0, "csi": 0, "ip": addr[0], "turb": 0.0})
            s["n"] += 1
            s["rssi"] += float(f[8])
            s["csi"] += int(f[5])
            s["turb"] = float(f[9])
            if csv:
                csv.write(f"{time.time():.3f}," + ",".join(f[1:]) + f",{addr[0]}\n")
    if time.time() - last >= 2:
        last = time.time()
        for (rx, tx), s in sorted(stats.items()):
            print(f"{tx[-4:]}->{rx[-4:]} ({s['ip']}) reports={s['n']} csi/s={s['csi'] / max(1, s['n']):.0f} "
                  f"rssi={s['rssi'] / max(1, s['n']):.1f} turb={s['turb']:.4f}")
        stats.clear()
        print("--")
if csv:
    csv.close()
