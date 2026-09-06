#!/usr/bin/env python3
"""Compare panel command streams: device `[EPD] c=XX len=N ..` lines vs emulator JSON lines.

  epdtrace.py device.log emulator.jsonl [--from-cmd 0x00] [--count N]

Prints both opcode sequences aligned (with data lengths) and the first divergence.
The device log is a console capture; the emulator trace comes from `x4emu run --trace-epd`.
"""
import argparse, json, re, sys

def load(path):
    out = []
    for line in open(path, errors='replace'):
        m = re.search(r'\[EPD\] c=([0-9A-Fa-f]{2}) len=(\d+)((?: [0-9A-Fa-f]{2})*)', line)
        if m:
            out.append((int(m.group(1), 16), int(m.group(2)), m.group(3).strip().lower()))
            continue
        if '"cmd":"0x' in line:
            j = json.loads(line)
            out.append((int(j['cmd'], 16), int(j['len']), j.get('data', '')[:23]))
    return out

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('device'); ap.add_argument('emulator')
    ap.add_argument('--from-cmd', help='start both streams at the first occurrence of this opcode (e.g. 0x00)')
    ap.add_argument('--count', type=int, default=60)
    a = ap.parse_args()
    d, e = load(a.device), load(a.emulator)
    if a.from_cmd:
        fc = int(a.from_cmd, 16)
        d = d[next((i for i, x in enumerate(d) if x[0] == fc), 0):]
        e = e[next((i for i, x in enumerate(e) if x[0] == fc), 0):]
    n = max(len(d), len(e))
    first = None
    for i in range(min(a.count, n)):
        x = d[i] if i < len(d) else None
        y = e[i] if i < len(e) else None
        same = x is not None and y is not None and x[0] == y[0] and x[1] == y[1]
        if not same and first is None:
            first = i
        fx = f"{x[0]:02X} len={x[1]:<6} {x[2]}" if x else '-'
        fy = f"{y[0]:02X} len={y[1]:<6} {y[2]}" if y else '-'
        print(f"{'  ' if same else '!!'} {i:3d}  dev: {fx:40s} emu: {fy}")
    ops_d = [x[0] for x in d]; ops_e = [x[0] for x in e]
    print(f"device: {len(d)} commands, emulator: {len(e)} commands; opcode sequences equal: {ops_d == ops_e}")
    if first is not None:
        print(f"first divergence at index {first}")
        sys.exit(1)

if __name__ == '__main__':
    main()
