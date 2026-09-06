#!/usr/bin/env python3
"""Toolbox for the physical Xteink X4 Pro on the desk.

Safety rules (see CLAUDE.md): nothing here erases flash, burns efuses, or
writes below 0x10000. `flash-crosspoint` refuses to run unless a verified
double backup exists under images/device/. Every write-capable subcommand
prints what it will do and the port it will use before doing it.

Subcommands:
  inventory        USB descriptor, port, chip-id (resets the device)
  efuse-summary    espefuse summary + dump into docs/device/
  backup           read the full 16 MB twice, compare SHA-256
  console          capture the console with timestamps (optionally reset first)
  image-sd         dd the device's SD card (exposed over USB MSC) read-only to an image
  flash-crosspoint pio run -e x4pro -t upload, gated on a verified backup
"""
import argparse, datetime, glob, hashlib, os, subprocess, sys, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
FLASH_SIZE = 0x1000000

def find_port(explicit=None):
    if explicit:
        return explicit
    cands = sorted(glob.glob('/dev/cu.usbmodem*') + glob.glob('/dev/ttyACM*'))
    if not cands:
        sys.exit('no USB serial port found (/dev/cu.usbmodem* or /dev/ttyACM*)')
    return cands[0]

def esptool(port, *args, before='default-reset', after='hard-reset', check=True):
    cmd = [PY, '-m', 'esptool', '--chip', 'esp32s3', '-p', port, '--before', before, '--after', after] + list(args)
    print('+', ' '.join(cmd), flush=True)
    return subprocess.run(cmd, check=check)

def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()

def verified_backup():
    """Return (a, b) paths of a verified backup pair, or None."""
    pairs = {}
    for p in glob.glob(os.path.join(ROOT, 'images/device/flash-*-a.bin')):
        b = p[:-5] + 'b.bin'
        if os.path.exists(b) and os.path.getsize(p) == FLASH_SIZE and os.path.getsize(b) == FLASH_SIZE:
            pairs[p] = b
    for a, b in sorted(pairs.items(), reverse=True):
        if sha256(a) == sha256(b):
            return a, b
    return None

def cmd_inventory(a):
    port = find_port(a.port)
    if sys.platform == 'darwin':
        out = subprocess.run(['ioreg', '-p', 'IOUSB', '-l', '-w0'], capture_output=True, text=True).stdout
        block = []
        for line in out.splitlines():
            if 'Espressif' in line or block:
                block.append(line.strip())
                if len(block) > 12:
                    break
        print('\n'.join(l for l in block if any(k in l for k in ('idVendor', 'idProduct', 'USB Product', 'USB Serial', 'bcdDevice'))))
    else:
        subprocess.run('lsusb | grep -i 303a; dmesg | tail -20', shell=True)
    print('port:', port)
    esptool(port, 'chip-id')

def cmd_efuse_summary(a):
    port = find_port(a.port)
    os.makedirs(os.path.join(ROOT, 'docs/device'), exist_ok=True)
    for sub, name in (('summary', 'efuse-summary.txt'), ('dump', 'efuse-dump.txt')):
        out = os.path.join(ROOT, 'docs/device', name)
        cmd = [PY, '-m', 'espefuse', '-p', port, '--chip', 'esp32s3', sub]
        print('+', ' '.join(cmd), '>', out, flush=True)
        with open(out, 'w') as f:
            subprocess.run(cmd, stdout=f, stderr=subprocess.STDOUT, check=True)

def cmd_backup(a):
    port = find_port(a.port)
    d = datetime.date.today().isoformat()
    tag = f'-{a.tag}' if a.tag else ''
    os.makedirs(os.path.join(ROOT, 'images/device'), exist_ok=True)
    pa = os.path.join(ROOT, f'images/device/flash-{d}{tag}-a.bin')
    pb = os.path.join(ROOT, f'images/device/flash-{d}{tag}-b.bin')
    print(f'Will read 16 MB from {port} twice into\n  {pa}\n  {pb}\nDo not touch the device until this finishes.')
    esptool(port, 'read-flash', '0', hex(FLASH_SIZE), pa, after='no-reset')
    esptool(port, 'read-flash', '0', hex(FLASH_SIZE), pb)
    ha, hb = sha256(pa), sha256(pb)
    print(ha, pa); print(hb, pb)
    if ha != hb:
        sys.exit('MISMATCH: the two reads differ; do not trust either. Check the connection and rerun.')
    with open(os.path.join(ROOT, 'docs/device/flash-backup-sha256.txt'), 'a') as f:
        f.write(f'{ha}  {os.path.relpath(pa, ROOT)}\n{hb}  {os.path.relpath(pb, ROOT)}\n')
    print('backup verified: both reads identical')

def hard_reset(ser):
    """esptool-style hard reset over the USB Serial/JTAG control lines."""
    ser.dtr = False
    ser.rts = True   # EN low
    time.sleep(0.1)
    ser.rts = False  # EN high
    ser.dtr = False

def cmd_console(a):
    import serial
    port = find_port(a.port)
    out = a.out or os.path.join(ROOT, 'docs/device', f'console-{datetime.datetime.now():%Y%m%d-%H%M%S}.log')
    print(f'capturing {port} @115200 -> {out} for {a.seconds}s (reset first: {a.reset})', flush=True)
    t0 = time.time()
    ser = None
    buf = b''
    with open(out, 'w') as f:
        def emit(line):
            ts = time.time() - t0
            s = line.decode('utf-8', 'replace').rstrip('\r\n')
            f.write(f'[{ts:8.3f}] {s}\n'); f.flush()
            if not a.quiet:
                print(f'[{ts:8.3f}] {s}', flush=True)
        while time.time() - t0 < a.seconds:
            if ser is None:
                try:
                    ser = serial.Serial(port, 115200, timeout=0.2)
                    if a.reset:
                        hard_reset(ser); a.reset = False
                        f.write(f'[{time.time()-t0:8.3f}] <hard reset via RTS>\n')
                except Exception as e:
                    time.sleep(0.2)
                    continue
            try:
                d = ser.read(4096)
            except Exception as e:
                f.write(f'[{time.time()-t0:8.3f}] <port error: {e}; reopening>\n'); f.flush()
                try: ser.close()
                except Exception: pass
                ser = None
                continue
            if d:
                buf += d
                while b'\n' in buf:
                    line, buf = buf.split(b'\n', 1)
                    emit(line)
        if buf:
            emit(buf)
    print('saved', out)

def cmd_image_sd(a):
    # List block devices; user identifies the card by size/model. Read-only dd.
    if sys.platform == 'darwin':
        subprocess.run(['diskutil', 'list', 'external'])
    else:
        subprocess.run(['lsblk', '-o', 'NAME,SIZE,MODEL,TRAN,MOUNTPOINT'])
    if not a.dev:
        print('rerun with --dev /dev/rdiskN (macOS) or /dev/sdX (Linux) once you have identified the card'); return
    out = a.out or os.path.join(ROOT, 'images/device/sd.img')
    print(f'Will READ {a.dev} into {out} (never writes to the device).')
    if sys.platform == 'darwin':
        subprocess.run(['diskutil', 'unmountDisk', a.dev.replace('/dev/r', '/dev/')], check=False)
    subprocess.run(['dd', f'if={a.dev}', f'of={out}', 'bs=4m' if sys.platform == 'darwin' else 'bs=4M', 'status=progress'], check=True)
    size = os.path.getsize(out)
    p2 = 1 << (size - 1).bit_length()
    if p2 != size:
        print(f'padding {size} -> {p2} bytes (power of two for QEMU)')
        with open(out, 'r+b') as f:
            f.truncate(p2)
    print('saved', out)

def cmd_flash_crosspoint(a):
    port = find_port(a.port)
    vb = verified_backup()
    if not vb:
        sys.exit('REFUSING: no verified double backup under images/device/ (run `device.py backup` first)')
    print(f'verified backup present: {vb[0]}')
    env = dict(os.environ, PLATFORMIO_UPLOAD_PORT=port)
    cmd = ['pio', 'run', '-e', a.env, '-t', 'upload', '--upload-port', port]
    print(f'Will run in {os.path.join(ROOT, "firmware")}: {" ".join(cmd)}')
    print('This writes an OTA app slot only (pioarduino upload flashes bootloader/partitions/app; the app goes to app0 at 0x10000).')
    if not a.yes:
        sys.exit('pass --yes to proceed')
    subprocess.run(cmd, cwd=os.path.join(ROOT, 'firmware'), env=env, check=True)

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('-p', '--port')
    sp = ap.add_subparsers(dest='cmd', required=True)
    sp.add_parser('inventory').set_defaults(fn=cmd_inventory)
    sp.add_parser('efuse-summary').set_defaults(fn=cmd_efuse_summary)
    p = sp.add_parser('backup'); p.add_argument('--tag'); p.set_defaults(fn=cmd_backup)
    p = sp.add_parser('console'); p.add_argument('--seconds', type=float, default=30); p.add_argument('--reset', action='store_true')
    p.add_argument('--out'); p.add_argument('--quiet', action='store_true'); p.set_defaults(fn=cmd_console)
    p = sp.add_parser('image-sd'); p.add_argument('--dev'); p.add_argument('--out'); p.set_defaults(fn=cmd_image_sd)
    p = sp.add_parser('flash-crosspoint'); p.add_argument('--env', default='x4pro'); p.add_argument('--yes', action='store_true'); p.set_defaults(fn=cmd_flash_crosspoint)
    a = ap.parse_args()
    a.fn(a)

if __name__ == '__main__':
    main()
