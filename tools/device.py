#!/usr/bin/env python3
"""Toolbox for the physical Xteink X4 Pro on the desk.

Safety rules (see CLAUDE.md): nothing here erases flash, burns efuses, or writes below
0x10000. That is enforced structurally: every esptool invocation goes through `esptool()`
or `esp_run()`, and both refuse an `erase-*` subcommand outright and refuse a `write-flash`
carrying any address below the first app slot (`FIRST_WRITABLE` = 0x10000). Nothing here
writes otadata. `flash-crosspoint` and `restore-stock` refuse to run unless a verified
double backup exists under images/device/. Every write-capable subcommand prints what it
will do and the port it will use before doing it.

The flash layout is **read, not assumed**. The partition table at 0x8000 is parsed into
its app slots and its otadata partition, and otadata is decoded the way the ESP-IDF
bootloader decodes it: of the two `esp_ota_select_entry_t` sectors, the one with the
highest sequence number whose CRC-32 checks out wins, and the slot it selects is
`(seq - 1) % <number of OTA app slots>`. This is not academic on this device. Until
2026-09-07 app0 held the app that boots; then the owner ran the device's own Upgrade menu,
the OTA wrote stock 7.5.4 into **app1** and moved otadata to it, and app0 (stock 7.2.4) is
no longer started at all. A tool that assumed app0 would now write an app the bootloader
never runs and report success.

So: `slots` prints the whole picture read-only, offline from a dump or live from the
device; `flash-crosspoint` and `restore-stock` read otadata before they write anything and
refuse, with an explanation and without touching the flash, when the slot they would write
is not the slot that boots.

Subcommands:
  inventory        USB descriptor, port, chip-id (resets the device)
  efuse-summary    espefuse summary + dump into docs/device/
  backup           read the full 16 MB twice, compare SHA-256
  console          capture the console with timestamps (optionally reset first)
  image-sd         dd the device's SD card (exposed over USB MSC) read-only to an image
  slots            read-only: partition table, otadata, which slot the bootloader starts,
                   and what each app slot holds (`--from-dump FILE` needs no device)
  flash-crosspoint write a CrossPoint firmware.bin into an app slot, gated on a verified
                   backup and on that slot being the one that boots
  restore-stock    write the stock app back from the verified backup, same gates
"""
import argparse, binascii, datetime, glob, hashlib, os, struct, subprocess, sys, tempfile, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
FLASH_SIZE = 0x1000000

# The lowest address anything in this file may write. Below it live the second-stage
# bootloader (0x0), the partition table (0x8000) and otadata (0xE000); CLAUDE.md rule 2
# forbids writing any of them without the owner's explicit decision, which has not been
# given. `_refuse_forbidden_write()` enforces this for every esptool call below.
FIRST_WRITABLE = 0x10000

def _refuse_forbidden_write(args):
    """Structural guard on every esptool command this file builds: no erase, and no
    write-flash that names an address below FIRST_WRITABLE. Exits before the subprocess
    starts, so a mistaken caller cannot reach the flash at all."""
    args = [str(x) for x in args]
    for tok in args:
        if tok.replace('_', '-').startswith('erase-'):
            sys.exit(f'REFUSING: `{tok}` -- nothing here erases flash (CLAUDE.md rule 2)')
    if not any(t.replace('_', '-') == 'write-flash' for t in args):
        return
    for tok in args:
        try:
            addr = int(tok, 0)
        except ValueError:
            continue
        if addr < FIRST_WRITABLE:
            sys.exit(f'REFUSING to write flash at {addr:#x}: everything below {FIRST_WRITABLE:#x} '
                     '(bootloader, partition table, otadata) is off limits (CLAUDE.md rule 2). '
                     'Moving the boot pointer needs the owner\'s explicit decision, not this tool.')

def find_port(explicit=None):
    if explicit:
        return explicit
    cands = sorted(glob.glob('/dev/cu.usbmodem*') + glob.glob('/dev/ttyACM*'))
    if not cands:
        sys.exit('no USB serial port found (/dev/cu.usbmodem* or /dev/ttyACM*)')
    return cands[0]

def esptool(port, *args, before='default-reset', after='hard-reset', check=True):
    _refuse_forbidden_write(args)
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

# No STOCK_APP0 / STOCK_APP1 / OTADATA constants any more, on purpose: those addresses are
# what the stock table happens to say today (app0 0x10000, app1 0x7F0000, otadata 0xE000),
# and assuming them is exactly the bug this file no longer has. Every offset below comes
# out of the table the device is carrying. The one address still written down is
# FIRST_WRITABLE, which is a policy floor, not a layout guess.

def esp_run(port, *args, before='default-reset', after='no-reset', quiet=False):
    _refuse_forbidden_write(args)
    cmd = [PY, '-m', 'esptool', '--chip', 'esp32s3', '-p', port, '--before', before, '--after', after] + list(args)
    if not quiet:
        print('+', ' '.join(cmd), flush=True)
    r = subprocess.run(cmd, capture_output=True, text=True)
    if not quiet:
        tail = [l for l in (r.stdout + r.stderr).splitlines() if any(k in l for k in ('Wrote', 'verified', 'Hash', 'Error', 'error', 'Read '))]
        for l in tail:
            print('  ', l)
    if r.returncode:
        sys.exit(f'esptool failed ({r.returncode}):\n{r.stdout}\n{r.stderr}')

def read_region(port, off, size, path, before='no-reset', quiet=False):
    esp_run(port, 'read-flash', hex(off), hex(size), path, before=before, quiet=quiet)
    return open(path, 'rb').read()

# ----------------------------------------------------------------- flash layout
# Everything below reads the layout; nothing below writes anything. A "reader" is just
# `read(offset, length) -> bytes`, so the same code serves a dump file and the device.

PART_TABLE_OFF = 0x8000
PART_TABLE_SIZE = 0x1000
PART_MAGIC = 0x50AA          # the bytes AA 50 at the head of every 32-byte entry
PART_MD5_MAGIC = 0xEBEB      # the table's trailing MD5 entry: end of the real entries
OTA_SECTOR = 0x1000          # otadata = two of these, one esp_ota_select_entry_t each
OTA_ENTRY = 32
APP_DESC_MAGIC = 0xABCD5432  # esp_app_desc_t, at +0x20 in an ESP app image
BLANK = 0xFFFFFFFF
OTA_STATES = {0: 'NEW', 1: 'PENDING_VERIFY', 2: 'VALID', 3: 'INVALID', 4: 'ABORTED',
              BLANK: 'UNDEFINED'}
APP_SUBTYPES = {0x00: 'factory', 0x20: 'test'}
DATA_SUBTYPES = {0x00: 'otadata', 0x01: 'phy', 0x02: 'nvs', 0x03: 'coredump', 0x04: 'nvs_keys',
                 0x05: 'efuse_em', 0x06: 'undefined', 0x80: 'esphttpd', 0x81: 'fat',
                 0x82: 'spiffs', 0x83: 'littlefs'}

def part_kind(ptype, subtype):
    """'app/ota_1', 'data/nvs', ... -- how esp-idf names a (type, subtype) pair."""
    if ptype == 0:
        if 0x10 <= subtype <= 0x1F:
            return f'app/ota_{subtype - 0x10}'
        return 'app/' + APP_SUBTYPES.get(subtype, f'{subtype:#04x}')
    if ptype == 1:
        return 'data/' + DATA_SUBTYPES.get(subtype, f'{subtype:#04x}')
    return f'{ptype:#04x}/{subtype:#04x}'

def parse_partition_table(blob):
    """The 4 KB at 0x8000 as a list of partitions. Each 32-byte entry is magic AA 50, u8
    type, u8 subtype, u32 offset, u32 size, 16-byte label, u32 flags; a blank entry or the
    0xEBEB MD5 entry ends the table."""
    parts = []
    for p in range(0, min(len(blob), PART_TABLE_SIZE), 32):
        e = blob[p:p + 32]
        if len(e) < 32:
            break
        magic, ptype, subtype, off, size = struct.unpack('<HBBII', e[:12])
        if magic in (PART_MD5_MAGIC, 0xFFFF):
            break
        if magic != PART_MAGIC:
            raise ValueError(f'partition table entry {p // 32} has magic {magic:#06x}, not {PART_MAGIC:#06x}')
        parts.append({'type': ptype, 'subtype': subtype, 'offset': off, 'size': size,
                      'label': e[12:28].split(b'\x00')[0].decode('ascii', 'replace'),
                      'flags': struct.unpack('<I', e[28:32])[0],
                      'kind': part_kind(ptype, subtype)})
    if not parts:
        raise ValueError(f'no partition table at {PART_TABLE_OFF:#x} (first entry is not {PART_MAGIC:#06x})')
    return parts

def ota_app_slots(parts):
    """The OTA app partitions in bootloader order: subtype 0x10 is OTA slot 0."""
    return sorted([p for p in parts if p['type'] == 0 and 0x10 <= p['subtype'] <= 0x1F],
                  key=lambda p: p['subtype'])

def factory_slot(parts):
    return next((p for p in parts if p['type'] == 0 and p['subtype'] == 0x00), None)

def otadata_part(parts):
    return next((p for p in parts if p['type'] == 1 and p['subtype'] == 0x00), None)

def esp_crc32(data):
    """ESP-IDF's esp_crc32_le(0xFFFFFFFF, data, len). bootloader_common_ota_select_crc()
    runs it over the four ota_seq bytes; Python's binascii.crc32 with that same seed is
    bit-for-bit the same function (checked against both dumps)."""
    return binascii.crc32(data, BLANK) & 0xFFFFFFFF

def parse_ota_entry(sec):
    """One esp_ota_select_entry_t: u32 ota_seq, 20 bytes label, u32 ota_state, u32 crc."""
    sec = bytes(sec).ljust(OTA_ENTRY, b'\xff')[:OTA_ENTRY]
    seq, state, crc = (struct.unpack('<I', sec[0:4])[0], struct.unpack('<I', sec[24:28])[0],
                       struct.unpack('<I', sec[28:32])[0])
    want = esp_crc32(sec[0:4])
    return {'seq': seq, 'state': state, 'state_name': OTA_STATES.get(state, f'{state:#010x}'),
            'crc': crc, 'crc_expected': want, 'crc_ok': crc == want,
            'blank': all(b == 0xFF for b in sec),
            # bootloader_common_ota_select_valid(): a blank sector (seq 0xFFFFFFFF) is no
            # candidate, and neither is one whose CRC does not check out.
            'valid': seq != BLANK and crc == want,
            'slot': None if seq in (0, BLANK) else (seq - 1)}

def resolve_boot_slot(entries, ota_count, has_factory=False):
    """What the ESP-IDF bootloader makes of the two otadata entries.

    bootloader_common_select_otadata(): both valid -> the higher ota_seq wins, and because
    the comparison is a strict '>', equal sequence numbers hand it to entry 1. One valid ->
    that one. Neither -> the factory partition if the table has one, else OTA slot 0 as a
    fresh flash. The chosen slot is (seq - 1) % ota_count."""
    valid = [bool(e['valid']) for e in entries]
    if valid[0] and valid[1]:
        active = 0 if entries[0]['seq'] > entries[1]['seq'] else 1
    elif valid[0]:
        active = 0
    elif valid[1]:
        active = 1
    else:
        active = None
    if active is None:
        why = ('neither otadata entry is valid (blank, or the CRC does not match the sequence '
               'number), so the bootloader treats the flash as fresh')
        if has_factory:
            return {'active': None, 'seq': None, 'state': None, 'state_name': None,
                    'index': -1, 'factory': True, 'pending_verify': False,
                    'reason': why + ' and starts the factory partition'}
        return {'active': None, 'seq': None, 'state': None, 'state_name': None,
                'index': 0 if ota_count else None, 'factory': False, 'pending_verify': False,
                'reason': why + ' and starts the first OTA slot'}
    e = entries[active]
    index = (e['seq'] - 1) % ota_count if ota_count else None
    return {'active': active, 'seq': e['seq'], 'state': e['state'], 'state_name': e['state_name'],
            'index': index, 'factory': False,
            'pending_verify': e['state'] == 1,
            'reason': (f"otadata entry {active} has the highest valid sequence number ({e['seq']}, "
                       f"state {e['state_name']}), and ({e['seq']} - 1) % {ota_count} = {index}")}

def parse_app_desc(head):
    """esp_app_desc_t out of the first bytes of an app slot, or None if there is no app
    image there. Layout: u32 magic, u32 secure_version, 8 reserved, char version[32],
    project_name[32], time[16], date[16], idf_ver[32], u8 elf_sha256[32]."""
    if len(head) < 0x120 or head[0] != 0xE9:
        return None
    d = head[0x20:0x120]
    if struct.unpack('<I', d[0:4])[0] != APP_DESC_MAGIC:
        return None
    txt = lambda b: b.split(b'\x00')[0].decode('ascii', 'replace')
    return {'secure_version': struct.unpack('<I', d[4:8])[0], 'version': txt(d[16:48]),
            'project': txt(d[48:80]), 'time': txt(d[80:96]), 'date': txt(d[96:112]),
            'idf_ver': txt(d[112:144]), 'elf_sha256': d[144:176].hex(),
            'segments': head[1]}

def app_image_size_via(read, off, cap):
    """Size of the ESP app image at `off` (header + segments + checksum pad + optional
    SHA-256), walking the segment headers through `read`. Only the 8-byte headers are
    fetched, so this stays cheap even over a serial link."""
    head = read(off, 24)
    if len(head) < 24 or head[0] != 0xE9:
        return None
    nseg, hash_app = head[1], head[23]
    p = 24
    for _ in range(nseg):
        h = read(off + p, 8)
        if len(h) < 8:
            return None
        p += 8 + struct.unpack('<II', h)[1]
        if p > cap:
            return None
    p = (p + 15) // 16 * 16
    if hash_app:
        p += 32
    return p if p <= cap else None

def app_image_size(blob):
    """Size of an ESP app image already in memory (used on the backup file)."""
    return app_image_size_via(lambda o, n: blob[o:o + n], 0, len(blob))

def dump_reader(path):
    """A reader over a raw flash dump on disk."""
    size = os.path.getsize(path)
    if size < PART_TABLE_OFF + PART_TABLE_SIZE:
        sys.exit(f'{path} is only {size} bytes: not a flash dump')
    def read(off, n):
        with open(path, 'rb') as f:
            f.seek(off)
            return f.read(n)
    return read

def device_reader(port, tmp):
    """A reader over the live device (esptool read-flash into `tmp`). Read-only: the guard
    in esp_run() would refuse anything else."""
    first = [True]
    def read(off, n):
        path = os.path.join(tmp, f'read-{off:08x}-{n:x}.bin')
        blob = read_region(port, off, n, path, before='default-reset' if first[0] else 'no-reset',
                           quiet=True)
        first[0] = False
        return blob
    return read

def read_layout(read, source='', deep=True):
    """Partition table + otadata + what is in each app slot, from a dump or the device.

    `deep` walks each app image's segment headers for its exact size. That is free on a
    dump but costs a small read per segment over the serial link, so the write-capable
    commands ask for the cheap version: they need the labels, offsets and versions, and
    every extra transaction is another chance for the magnetic pogo adapter to let go."""
    parts = parse_partition_table(read(PART_TABLE_OFF, PART_TABLE_SIZE))
    od = otadata_part(parts)
    ota_parts = ota_app_slots(parts)
    fact = factory_slot(parts)
    entries = []
    if od:
        blob = read(od['offset'], min(od['size'], 2 * OTA_SECTOR))
        entries = [parse_ota_entry(blob[i * OTA_SECTOR:i * OTA_SECTOR + OTA_ENTRY]) for i in range(2)]
    boot = (resolve_boot_slot(entries, len(ota_parts), fact is not None) if len(entries) == 2 else
            {'active': None, 'seq': None, 'state': None, 'state_name': None, 'index': None,
             'factory': False, 'pending_verify': False,
             'reason': 'the partition table has no otadata partition'})
    slots = []
    for i, p in enumerate(ota_parts):
        s = dict(p)
        head = read(p['offset'], 0x1000)
        s['ota_index'] = i
        s['blank'] = all(b == 0xFF for b in head[:0x1000])
        s['desc'] = parse_app_desc(head)
        s['image_size'] = (None if s['blank'] or not deep else
                           app_image_size_via(read, p['offset'], p['size']))
        s['boots'] = (i == boot['index'])
        slots.append(s)
    boot['label'] = ('factory' if boot['index'] == -1 else
                     next((s['label'] for s in slots if s['boots']), None))
    return {'source': source, 'partitions': parts, 'otadata': od, 'entries': entries,
            'apps': slots, 'factory': fact, 'boot': boot}

def describe_app(slot):
    """One line saying what an app slot holds, in the owner's words."""
    if slot['blank']:
        return 'blank (erased, 0xFF)'
    d = slot['desc']
    if not d:
        return 'an app image with no esp_app_desc (or something that is not an app image)'
    size = f", {slot['image_size']:,} bytes" if slot['image_size'] else ''
    return f"{d['project']} {d['version']} (built {d['date']} {d['time']}, IDF {d['idf_ver']}{size})"

def _human(n):
    for unit, div in (('MB', 1 << 20), ('KB', 1 << 10)):
        if n >= div and n % div == 0:
            return f'{n // div} {unit}'
    return f'{n} B'

def format_layout(layout):
    """The `slots` report: plain words, no serial port, nothing written."""
    L = [f"source: {layout['source']}", '',
         f"Partition table at {PART_TABLE_OFF:#x}:"]
    L.append('  {:<10} {:<14} {:<10} {}'.format('label', 'kind', 'offset', 'size'))
    for p in layout['partitions']:
        note = ''
        if p['type'] == 0 and 0x10 <= p['subtype'] <= 0x1F:
            s = next((s for s in layout['apps'] if s['offset'] == p['offset']), None)
            note = '<- the bootloader starts this one' if s and s['boots'] else ''
        L.append('  {:<10} {:<14} {:#010x} {:#010x} {:>8}  {}'.format(
            p['label'], p['kind'], p['offset'], p['size'], _human(p['size']), note).rstrip())
    L.append('')
    od = layout['otadata']
    if od and len(layout['entries']) == 2:
        L.append(f"otadata ({od['label']}, two {_human(OTA_SECTOR)} sectors at {od['offset']:#x}):")
        for i, e in enumerate(layout['entries']):
            if e['blank']:
                L.append(f'  entry {i}: blank (never written) -- not a candidate')
                continue
            crc = 'CRC ok' if e['crc_ok'] else f"CRC BAD (stored {e['crc']:#010x}, computed {e['crc_expected']:#010x})"
            picks = ('' if e['slot'] is None or not layout['apps'] else
                     f", picks OTA slot {e['slot'] % len(layout['apps'])}")
            L.append(f"  entry {i}: seq {e['seq']}, state {e['state_name']}, {crc}{picks}"
                     + ('  <- active' if layout['boot']['active'] == i else ''))
    else:
        L.append('otadata: none in this partition table')
    L.append('')
    b = layout['boot']
    L.append(f"Boot slot: {b['label'] or 'unknown'}")
    L.append(f"  why: {b['reason']}.")
    if b['pending_verify']:
        L.append('  NOTE: that entry is PENDING_VERIFY. If the bootloader was built with rollback '
                 'enabled it marks this one ABORTED and starts the other slot instead.')
    L.append('')
    L.append('App slots:')
    for s in layout['apps']:
        mark = 'BOOTS  ' if s['boots'] else '       '
        L.append(f"  {mark}{s['label']} (OTA slot {s['ota_index']}) @{s['offset']:#x}, "
                 f"{_human(s['size'])} of room: {describe_app(s)}")
    if layout['factory']:
        L.append(f"  (a factory partition also exists at {layout['factory']['offset']:#x})")
    return '\n'.join(L)

def slot_by_label(layout, label):
    return next((s for s in layout['apps'] if s['label'] == label), None)

def boot_slot_refusal(layout, target, action, subcommand):
    """The otadata gate shared by flash-crosspoint and restore-stock. Returns None when the
    write may go ahead, or the text to print and exit with when it may not.

    Pure and side-effect free on purpose: it takes the parsed layout, so the tests drive it
    from the dumps without a serial port (tests/test_device_otadata.py)."""
    b = layout['boot']
    if b['label'] is None or b['index'] is None:
        return (f'REFUSING: nothing has been written.\n'
                f"  This device's otadata does not name a slot to boot ({b['reason']}).\n"
                '  Run `tools/device.py slots` and work out what the device is doing before writing to it.')
    if target['label'] == b['label']:
        return None
    boot_slot = slot_by_label(layout, b['label'])
    if boot_slot is None:      # e.g. otadata is unusable and a factory partition takes over
        return ('REFUSING: nothing has been written.\n'
                f"  The bootloader does not start any OTA app slot on this device: {b['reason']}.\n"
                f"  {action} would put an app into {target['label']}, which would not run.\n"
                '  Read `tools/device.py slots` and decide with the owner what should happen.')
    holds = describe_app(boot_slot)
    lines = [
        'REFUSING: nothing has been written.',
        '',
        f"  The bootloader starts {b['label']}, not {target['label']}: {b['reason']}.",
        f"  {b['label']} holds {holds}.",
        f"  {action} would put an app into {target['label']}, which this device does not start."
        ' It would come back up running exactly what it runs now and look untouched.',
        '',
        '  What can be done instead:',
        f"    * aim this command at the slot that boots:  tools/device.py {subcommand} --slot {b['label']} ...",
        f"      That overwrites what {b['label']} holds now. It is in the verified backup byte for byte,"
        f" and `restore-stock --slot {b['label']}` puts it back.",
        '    * or leave the flash alone and let the device move its own boot pointer: its Upgrade menu'
        ' writes otadata, which is how app1 became the boot slot on 2026-09-07.',
        '',
        '  This tool will not move the boot pointer itself: otadata sits below'
        f' {FIRST_WRITABLE:#x} and CLAUDE.md rule 2 forbids writing there without the owner saying so.',
        # A future `flash-stock-patched` (docs/NEXT_PHASE.md §0.4.c) wants exactly that: the
        # patched app into the *other* slot plus an otadata entry marked PENDING_VERIFY so a
        # bad image rolls back. That needs the owner's explicit go-ahead first, so no code
        # for it exists here.
    ]
    return '\n'.join(lines)

def layout_from_dump(path):
    """Convenience for offline use and for the tests."""
    return read_layout(dump_reader(path), f'{path} (dump)')

def cmd_slots(a):
    """Read the flash layout and say, in plain words, what the device will boot.

    Reads only. Prints the partition table as the device carries it, both otadata entries
    (sequence number, state, whether the CRC-32 checks out), the slot the ESP-IDF
    bootloader picks from them and why, and what each app slot holds -- the project name,
    version, build date and image size out of its esp_app_desc, or "blank".

    With --from-dump FILE this runs entirely off a saved 16 MB dump and never opens the
    serial port, which is the way to try it while the device is not on the desk:

      tools/device.py slots --from-dump images/device/flash-2026-09-07-a.bin

    Without it the same numbers are read from the device over esptool (a handful of small
    read-flash calls; nothing is written)."""
    try:
        if a.from_dump:
            layout = layout_from_dump(a.from_dump)
        else:
            port = find_port(a.port)
            layout = read_layout(device_reader(port, tempfile.mkdtemp()), f'device on {port} (live read)')
    except ValueError as e:
        sys.exit(f'cannot read the flash layout: {e}')
    print(format_layout(layout))

def device_layout(port, why):
    """Read the live layout before a write, so the decision is made on what the device is
    actually carrying rather than on constants in this file."""
    print(f'reading the partition table and otadata from {port} ({why})...', flush=True)
    try:
        layout = read_layout(device_reader(port, tempfile.mkdtemp()), f'device on {port} (live read)',
                             deep=False)
    except ValueError as e:
        sys.exit(f'REFUSING: cannot read the flash layout from the device ({e}). Nothing written.')
    print(f"  the bootloader starts {layout['boot']['label']}: {layout['boot']['reason']}", flush=True)
    return layout

def check_backup_matches(layout, backup):
    """The backup is indexed by the device's own partition offsets, so the two tables have
    to be the same table."""
    try:
        theirs = parse_partition_table(backup[PART_TABLE_OFF:PART_TABLE_OFF + PART_TABLE_SIZE])
    except ValueError as e:
        sys.exit(f'REFUSING: the backup has no readable partition table ({e})')
    if theirs != layout['partitions']:
        sys.exit('REFUSING: the backup was taken under a different partition table than the device '
                 'is carrying now. Take a fresh backup (`tools/device.py backup`) and look at both '
                 'with `tools/device.py slots --from-dump`.')

def pick_target(layout, label):
    """The app slot a write-capable command will work on."""
    target = slot_by_label(layout, label)
    if not target:
        sys.exit(f'no app slot called {label!r} in the device\'s partition table '
                 f"(it has: {', '.join(s['label'] for s in layout['apps'])})")
    return target

def extracted_stock_path(slot, desc):
    ver = desc['version'] if desc else 'unknown'
    return os.path.join(ROOT, 'images/device', f"stock-{slot['label']}-{ver}.bin")

def cmd_flash_crosspoint(a):
    """Write a CrossPoint firmware.bin into an app slot on top of the stock bootloader and
    partition table, the way the community web flasher does. The slot defaults to app0 and
    must be the slot otadata makes the bootloader start, or the command refuses. Optionally
    keeps a copy of the stock app in the other slot first. Never touches 0x0..0x10000."""
    port = find_port(a.port)
    vb = verified_backup()
    if not vb:
        sys.exit('REFUSING: no verified double backup under images/device/ (run `device.py backup` first)')
    backup = open(vb[0], 'rb').read()
    fw = a.firmware or os.path.join(ROOT, 'firmware/.pio/build/x4pro/firmware.bin')
    fwdata = open(fw, 'rb').read()
    layout = device_layout(port, 'which slot boots decides everything below')
    check_backup_matches(layout, backup)
    target = pick_target(layout, a.slot)
    refusal = boot_slot_refusal(layout, target, f'Writing {os.path.basename(fw)}',
                                'flash-crosspoint')
    if refusal:
        sys.exit(refusal)
    if fwdata[0] != 0xE9 or len(fwdata) > target['size']:
        sys.exit(f"{fw} is not an app image that fits {target['label']} ({target['size']} bytes)")
    off = target['offset']
    other = next((s for s in layout['apps'] if s['label'] != target['label']), None)
    stock_desc = parse_app_desc(backup[off:off + 0x120])
    stock_size = app_image_size(backup[off:off + target['size']])
    print(f'verified backup: {vb[0]}')
    print(f'plan: port {port}')
    print(f"  0. {target['label']} is the slot the bootloader starts ({layout['boot']['reason']})")
    if a.preserve_stock:
        if not other:
            sys.exit('ABORT: --preserve-stock needs a second OTA app slot; this table has one')
        if not stock_size:
            sys.exit(f"ABORT: the backup holds no app image in {target['label']} to preserve")
        print(f"  1. copy the stock image now in {target['label']} ({stock_size} bytes, from the backup) "
              f"into {other['label']} @{other['offset']:#x} (must be blank)")
    print(f"  2. write {fw} ({len(fwdata)} bytes) into {target['label']} @{off:#x}")
    print('  otadata/bootloader/partition table untouched; the boot slot does not move')
    if not a.yes:
        sys.exit('pass --yes to proceed')
    tmp = tempfile.mkdtemp()
    # sanity: the device still matches the backup where we are about to write
    head = read_region(port, off, 0x10000, os.path.join(tmp, 'target-head.bin'), before='default-reset')
    if head == backup[off:off + 0x10000]:
        print(f"sanity: {target['label']} still holds the image the backup has (matches backup)")
    elif other:
        # the slot was already replaced (e.g. by an earlier CrossPoint flash): accept only if it
        # is a valid app image and the stock copy is intact in the other slot
        h1 = read_region(port, other['offset'], 0x10000, os.path.join(tmp, 'other-head.bin'))
        if head[0] != 0xE9 or h1 != backup[off:off + 0x10000]:
            sys.exit(f"ABORT: {target['label']} is neither the image in the backup nor a re-flash "
                     f"over a stock copy preserved in {other['label']}")
        print(f"sanity: {target['label']} holds another app image; stock copy verified in {other['label']}")
        if a.preserve_stock:
            a.preserve_stock = False
            print(f"(stock already preserved in {other['label']}; skipping that step)")
    else:
        sys.exit(f"ABORT: {target['label']} does not match the backup and there is no second slot to check")
    if a.preserve_stock:
        h1 = read_region(port, other['offset'], 0x1000, os.path.join(tmp, 'other-head.bin'))
        if any(b != 0xFF for b in h1):
            sys.exit(f"ABORT: {other['label']} is not blank; not overwriting it")
        stock_img = extracted_stock_path(target, stock_desc)
        open(stock_img, 'wb').write(backup[off:off + stock_size])
        esp_run(port, 'write-flash', hex(other['offset']), stock_img)
        back = read_region(port, other['offset'], 0x1000, os.path.join(tmp, 'other-head2.bin'))
        if back != backup[off:off + 0x1000]:
            sys.exit(f"ABORT: {other['label']} read-back mismatch")
        print(f"{other['label']} now holds the stock image (read-back OK)")
    esp_run(port, 'write-flash', hex(off), fw)
    back = read_region(port, off, 0x1000, os.path.join(tmp, 'cp-head.bin'))
    if back != fwdata[:0x1000]:
        sys.exit(f"ABORT: {target['label']} read-back mismatch")
    od = layout['otadata']
    if od:
        ota = read_region(port, od['offset'], od['size'], os.path.join(tmp, 'otadata.bin'))
        print(f"{target['label']} read-back OK; otadata unchanged:",
              ota == backup[od['offset']:od['offset'] + od['size']])
    esp_run(port, 'chip-id', before='no-reset', after='hard-reset')
    print(f"device reset into the new {target['label']}")

def cmd_restore_stock(a):
    """Write the stock app image from the verified backup back into an app slot. The slot
    defaults to app0 and must be the slot otadata makes the bootloader start, or the
    command refuses (restoring a slot the device never runs changes nothing)."""
    port = find_port(a.port)
    vb = verified_backup()
    if not vb:
        sys.exit('REFUSING: no verified double backup')
    backup = open(vb[0], 'rb').read()
    layout = device_layout(port, 'which slot boots decides everything below')
    check_backup_matches(layout, backup)
    target = pick_target(layout, a.slot)
    refusal = boot_slot_refusal(layout, target, 'Restoring the stock app', 'restore-stock')
    if refusal:
        sys.exit(refusal)
    off = target['offset']
    desc = parse_app_desc(backup[off:off + 0x120])
    size = app_image_size(backup[off:off + target['size']])
    if not size:
        sys.exit(f"ABORT: the backup holds no app image in {target['label']} to restore")
    img = extracted_stock_path(target, desc)
    open(img, 'wb').write(backup[off:off + size])
    what = f"{desc['project']} {desc['version']}" if desc else 'the app'
    print(f"plan: write {size} bytes of {what} from {vb[0]} into {target['label']} @{off:#x} on {port}")
    if not a.yes:
        sys.exit('pass --yes to proceed')
    esp_run(port, 'write-flash', hex(off), img, before='default-reset')
    tmp = tempfile.mkdtemp()
    back = read_region(port, off, 0x1000, os.path.join(tmp, 'head.bin'))
    print('read-back OK:', back == backup[off:off + 0x1000])
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

    p = sp.add_parser('slots', help='read-only: partition table, otadata, which slot boots, what is in each app slot',
                      description=cmd_slots.__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--from-dump', metavar='FILE',
                   help='read a raw 16 MB flash dump (images/device/flash-*.bin) instead of the device; '
                        'no serial port is opened')
    p.set_defaults(fn=cmd_slots)

    p = sp.add_parser('flash-crosspoint', help='write a CrossPoint firmware.bin into the app slot that boots',
                      description=cmd_flash_crosspoint.__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--firmware', help='the app image to write (default: firmware/.pio/build/x4pro/firmware.bin)')
    p.add_argument('--slot', default='app0', help='app slot to write, by partition label (default: app0). '
                                                  'Must be the slot otadata makes the bootloader start.')
    p.add_argument('--preserve-stock', action='store_true',
                   help='first copy the stock app out of the target slot into the other app slot (must be blank)')
    p.add_argument('--yes', action='store_true', help='actually write; without it only the plan is printed')
    p.set_defaults(fn=cmd_flash_crosspoint)

    p = sp.add_parser('restore-stock', help='write the stock app back from the verified backup',
                      description=cmd_restore_stock.__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--slot', default='app0', help='app slot to restore, by partition label (default: app0). '
                                                  'Must be the slot otadata makes the bootloader start.')
    p.add_argument('--yes', action='store_true', help='actually write; without it only the plan is printed')
    p.set_defaults(fn=cmd_restore_stock)
    a = ap.parse_args()
    a.fn(a)

if __name__ == '__main__':
    main()
