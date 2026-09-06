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

STOCK_APP0 = 0x10000
STOCK_APP1 = 0x7F0000
OTADATA = 0xE000

def esp_run(port, *args, before='default-reset', after='no-reset'):
    cmd = [PY, '-m', 'esptool', '--chip', 'esp32s3', '-p', port, '--before', before, '--after', after] + list(args)
    print('+', ' '.join(cmd), flush=True)
    r = subprocess.run(cmd, capture_output=True, text=True)
    tail = [l for l in (r.stdout + r.stderr).splitlines() if any(k in l for k in ('Wrote', 'verified', 'Hash', 'Error', 'error', 'Read '))]
    for l in tail:
        print('  ', l)
    if r.returncode:
        sys.exit(f'esptool failed ({r.returncode}):\n{r.stdout}\n{r.stderr}')

def read_region(port, off, size, path, before='no-reset'):
    esp_run(port, 'read-flash', hex(off), hex(size), path, before=before)
    return open(path, 'rb').read()

def app_image_size(blob):
    """Size of an ESP app image (header + segments + checksum pad + optional SHA-256)."""
    import struct
    if blob[0] != 0xE9:
        return None
    nseg, hash_app = blob[1], blob[23]
    p = 24
    for _ in range(nseg):
        _, size = struct.unpack('<II', blob[p:p + 8])
        p += 8 + size
    p = (p + 15) // 16 * 16
    if hash_app:
        p += 32
    return p

def cmd_flash_crosspoint(a):
    """Write a CrossPoint firmware.bin into app0 (0x10000) on top of the stock bootloader and
    partition table, the way the community web flasher does. Optionally keeps a copy of the stock
    app in app1 first. Never touches 0x0..0x10000."""
    import tempfile
    port = find_port(a.port)
    vb = verified_backup()
    if not vb:
        sys.exit('REFUSING: no verified double backup under images/device/ (run `device.py backup` first)')
    backup = open(vb[0], 'rb').read()
    fw = a.firmware or os.path.join(ROOT, 'firmware/.pio/build/x4pro/firmware.bin')
    fwdata = open(fw, 'rb').read()
    if fwdata[0] != 0xE9 or len(fwdata) > 0x7E0000:
        sys.exit(f'{fw} is not an app image that fits app0')
    stock_size = app_image_size(backup[STOCK_APP0:STOCK_APP0 + 0x7E0000])
    print(f'verified backup: {vb[0]}')
    print(f'plan: port {port}')
    if a.preserve_stock:
        print(f'  1. write the stock app0 image ({stock_size} bytes) from the backup into app1 @{STOCK_APP1:#x} (must be blank)')
    print(f'  2. write {fw} ({len(fwdata)} bytes) into app0 @{STOCK_APP0:#x}')
    print('  otadata/bootloader/partition table untouched; boot slot stays app0')
    if not a.yes:
        sys.exit('pass --yes to proceed')
    tmp = tempfile.mkdtemp()
    # sanity: the device still matches the backup where we are about to write
    head = read_region(port, STOCK_APP0, 0x10000, os.path.join(tmp, 'app0-head.bin'), before='default-reset')
    if head == backup[STOCK_APP0:STOCK_APP0 + 0x10000]:
        print('sanity: app0 still holds the stock image (matches backup)')
    else:
        # app0 was already replaced (e.g. by an earlier CrossPoint flash): accept only if it is a
        # valid app image and the stock copy is intact in app1
        h1 = read_region(port, STOCK_APP1, 0x10000, os.path.join(tmp, 'app1-head.bin'))
        if head[0] != 0xE9 or h1 != backup[STOCK_APP0:STOCK_APP0 + 0x10000]:
            sys.exit('ABORT: app0 is neither the stock image nor a re-flash over a preserved stock copy in app1')
        print('sanity: app0 holds another app image; stock copy verified in app1')
        if a.preserve_stock:
            a.preserve_stock = False
            print('(stock already preserved in app1; skipping that step)')
    if a.preserve_stock:
        h1 = read_region(port, STOCK_APP1, 0x1000, os.path.join(tmp, 'app1-head.bin'))
        if any(b != 0xFF for b in h1):
            sys.exit('ABORT: app1 is not blank; not overwriting it')
        stock_img = os.path.join(ROOT, 'images/device/stock-app0-7.2.4.bin')
        open(stock_img, 'wb').write(backup[STOCK_APP0:STOCK_APP0 + stock_size])
        esp_run(port, 'write-flash', hex(STOCK_APP1), stock_img)
        back = read_region(port, STOCK_APP1, 0x1000, os.path.join(tmp, 'app1-head2.bin'))
        if back != backup[STOCK_APP0:STOCK_APP0 + 0x1000]:
            sys.exit('ABORT: app1 read-back mismatch')
        print('app1 now holds the stock image (read-back OK)')
    esp_run(port, 'write-flash', hex(STOCK_APP0), fw)
    back = read_region(port, STOCK_APP0, 0x1000, os.path.join(tmp, 'cp-head.bin'))
    if back != fwdata[:0x1000]:
        sys.exit('ABORT: app0 read-back mismatch')
    ota = read_region(port, OTADATA, 0x2000, os.path.join(tmp, 'otadata.bin'))
    print('app0 read-back OK; otadata unchanged:', ota == backup[OTADATA:OTADATA + 0x2000])
    esp_run(port, 'chip-id', before='no-reset', after='hard-reset')
    print('device reset into the new app0')

def cmd_restore_stock(a):
    """Write the stock app image from the verified backup back into app0."""
    import tempfile
    port = find_port(a.port)
    vb = verified_backup()
    if not vb:
        sys.exit('REFUSING: no verified double backup')
    backup = open(vb[0], 'rb').read()
    size = app_image_size(backup[STOCK_APP0:STOCK_APP0 + 0x7E0000])
    img = os.path.join(ROOT, 'images/device/stock-app0-7.2.4.bin')
    open(img, 'wb').write(backup[STOCK_APP0:STOCK_APP0 + size])
    print(f'plan: write {size} bytes of stock app from {vb[0]} into app0 @{STOCK_APP0:#x} on {port}')
    if not a.yes:
        sys.exit('pass --yes to proceed')
    esp_run(port, 'write-flash', hex(STOCK_APP0), img, before='default-reset')
    tmp = tempfile.mkdtemp()
    back = read_region(port, STOCK_APP0, 0x1000, os.path.join(tmp, 'head.bin'))
    print('read-back OK:', back == backup[STOCK_APP0:STOCK_APP0 + 0x1000])
    esp_run(port, 'chip-id', before='no-reset', after='hard-reset')

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
    p = sp.add_parser('flash-crosspoint'); p.add_argument('--firmware'); p.add_argument('--preserve-stock', action='store_true'); p.add_argument('--yes', action='store_true'); p.set_defaults(fn=cmd_flash_crosspoint)
    p = sp.add_parser('restore-stock'); p.add_argument('--yes', action='store_true'); p.set_defaults(fn=cmd_restore_stock)
    a = ap.parse_args()
    a.fn(a)

if __name__ == '__main__':
    main()
