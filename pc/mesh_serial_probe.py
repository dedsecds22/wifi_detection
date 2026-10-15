"""P3a serial reader: reads "L," link lines from several ESP32 nodes and prints a summary.

Usage: python mesh_serial_probe.py COM6 COM10 [--seconds 30] [--csv out.csv]
"""
import argparse
import threading
import time

import serial

rows = []
lock = threading.Lock()


def reader(port, stop):
    ser = serial.Serial(port, 115200, timeout=1)
    ser.dtr = False
    ser.rts = False
    while not stop.is_set():
        line = ser.readline().decode("utf-8", "replace").strip()
        if line.startswith("L,"):
            parts = line.split(",")
            try:
                if len(parts) != 14:
                    raise ValueError
                [float(x) for x in parts[3:]]
            except ValueError:
                continue
            with lock:
                rows.append((time.time(), port, parts))
        elif line and not line.startswith(("T,", "\x1b")) and "mesh" in line:
            print(port, line)
    ser.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("ports", nargs="+")
    parser.add_argument("--seconds", type=float, default=30)
    parser.add_argument("--csv")
    args = parser.parse_args()
    stop = threading.Event()
    threads = [threading.Thread(target=reader, args=(p, stop), daemon=True) for p in args.ports]
    for t in threads:
        t.start()
    time.sleep(args.seconds)
    stop.set()
    for t in threads:
        t.join(3)
    print("rx_mac <- tx_mac : seconds, avg espnow/s, avg csi/s, csi_len, lost/s, rssi, turb_mean, turb_var, diff")
    keys = sorted({(r[2][1], r[2][2]) for r in rows})
    for key in keys:
        sel = [r[2] for r in rows if (r[2][1], r[2][2]) == key]
        n = len(sel)
        f = lambda i: sum(float(x[i]) for x in sel) / n
        print(key[0], "<-", key[1], ":", n, "s,", "%.1f" % f(4), "%.1f" % f(5), sel[-1][6], "%.1f" % f(7),
              "%.1f" % f(8), "%.4f" % f(9), "%.6f" % f(10), "%.4f" % f(11))
    if args.csv:
        with open(args.csv, "w") as fh:
            fh.write("pc_time,port,rx,tx,node_ms,espnow_rx,csi_n,csi_len,lost,rssi,turb_mean,turb_var,diff,tvar,amp\n")
            for t, port, x in rows:
                fh.write("%.3f,%s,%s\n" % (t, port, ",".join(x[1:])))


if __name__ == "__main__":
    main()
