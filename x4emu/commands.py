"""The commands themselves. Every one of them prints its human line through `out.line` and its
machine fields through `out.set`/`out.obj`, so `--json` changes the presentation and nothing else.
The human strings are contracts: tests/*.py and tools/x4emu_mcp.py parse some of them."""
import json
import os
import re
import shlex
import signal
import socket
import subprocess
import sys
import time

from .output import out
from .paths import idir, qemu, root
from .qmp import BOARD, INPUT, QMP, connect, pid_alive

PANEL_W, PANEL_H = 800, 480

# `run --boot-hold-power` without a value. The stock's boot-preflight starts measuring the power
# button at guest time ~530 ms and stops at its "effective" window (600 ms on this dump), i.e. it
# wants the button still down at guest time ~1130 ms. Early boot (ROM, flash) runs at roughly half
# real time here, so that point is reached after ~1.8 s of wall time; 3000 ms leaves margin on a
# slower or loaded host and still costs nothing (the guest boots on, `run` just returns later).
# The preflight's own `hold=…ms decision=…` console line says what the guest measured.
BOOT_HOLD_POWER_MS = 3000


# ---------------------------------------------------------------- lifecycle
def cmd_run(a):
    d = idir(a.name)
    if pid_alive(a.name):
        sys.exit(f'instance "{a.name}" already running (pid {pid_alive(a.name)}); `x4emu stop` first')
    os.makedirs(d, exist_ok=True)
    for f in ('console.log', 'uart0.log', 'qemu.log', 'stderr.log'):
        try: os.remove(os.path.join(d, f))
        except FileNotFoundError: pass
    flash = os.path.abspath(a.flash)
    if os.path.getsize(flash) != 16 * 1024 * 1024:
        sys.exit(f'{flash} is not a 16 MB image (use tools/mkflash.py)')
    efuse = os.path.abspath(a.efuse) if a.efuse else None
    if efuse is None:
        efuse = os.path.join(d, 'efuse.bin')
        if not os.path.exists(efuse):
            subprocess.run([sys.executable, os.path.join(root(), 'tools', 'mkefuse.py'), efuse,
                            '--dump', os.path.join(root(), 'docs', 'device', 'efuse-dump.txt')],
                           check=True, stdout=subprocess.DEVNULL)
    cmd = [qemu(), '-machine', a.machine, '-m', '8M',
           '-global', 'driver=ssi_psram,property=is_octal,value=true',
           '-drive', f'file={flash},if=mtd,format=raw',
           '-drive', f'file={efuse},if=none,format=raw,id=efuse',
           '-global', 'driver=nvram.esp32s3.efuse,property=drive,value=efuse',
           '-display', 'none', '-monitor', 'none',
           '-serial', f'file:{os.path.join(d, "uart0.log")}',
           '-chardev', f'socket,id=usbcon,path={os.path.join(d, "console.sock")},server=on,wait=off,logfile={os.path.join(d, "console.log")}',
           '-global', 'driver=esp32s3.usj,property=chardev,value=usbcon',
           '-qmp', f'unix:{os.path.join(d, "qmp.sock")},server,nowait',
           '-d', a.debug, '-D', os.path.join(d, 'qemu.log')]
    if a.sd:
        sd = os.path.abspath(a.sd)
        sz = os.path.getsize(sd)
        if sz & (sz - 1):
            sys.exit(f'{sd}: size must be a power of two')
        cmd += ['-drive', f'file={sd},if=sd,format=raw']
    if a.no_usb_host:
        cmd += ['-global', 'driver=esp32s3.usj,property=host-connected,value=false']
    if a.gdb:
        cmd += ['-s', '-S']
    if a.boot_hold_power:
        if a.gdb:
            sys.exit('--boot-hold-power cannot be combined with --gdb (gdb owns the halted start)')
        cmd += ['-S']            # start halted, press power, then `cont`: see boot_hold()
    if a.icount:
        cmd += ['-icount', a.icount]
    for k, v in (('panel', a.panel), ('fast-epd', 'on' if a.fast_epd else None),
                 ('trace-epd', os.path.abspath(a.trace_epd) if a.trace_epd else None)):
        if v:
            cmd += ['-global', f'driver=x4pro.epd,property={k},value={v}']
    if a.trace_i2c:
        cmd += ['-global', f'driver=esp32s3.i2c,property=trace-i2c,value={os.path.abspath(a.trace_i2c)}']
    cmd += a.extra
    json.dump({'cmd': cmd, 'started': time.time(), 'flash': flash, 'sd': a.sd,
               'boot_hold_power_ms': a.boot_hold_power}, open(os.path.join(d, 'run.json'), 'w'), indent=1)
    if a.dry_run:
        out.line(' '.join(shlex.quote(c) for c in cmd))
        out.set(name=a.name, dry_run=True, cmd=cmd, boot_hold_power_ms=a.boot_hold_power)
        return
    launch(a.name, cmd, a.boot_hold_power)


def boot_hold(q, ms):
    """Hold the power button (GPIO3, active-low) from reset for `ms` milliseconds.

    The VM was started with `-S`, so the button is asserted before the first instruction runs;
    `cont` then releases the CPUs and the guest finds the button already down when it samples it.
    The stock firmware's boot-preflight wants that (`x4emu press power` after its deep sleep is the
    other way in). Returns the query-status reply after the release."""
    q.qom_set(INPUT, 'btn-power', True)
    q.cmd('cont')
    time.sleep(ms / 1000.0)
    q.qom_set(INPUT, 'btn-power', False)
    return q.cmd('query-status')


def launch(name, cmd, boot_hold_power=None):
    d = idir(name)
    for f in ('console.log', 'uart0.log', 'qemu.log', 'stderr.log', 'console.sock'):
        try: os.remove(os.path.join(d, f))
        except FileNotFoundError: pass
    err = open(os.path.join(d, 'stderr.log'), 'wb')
    p = subprocess.Popen(cmd, stdout=err, stderr=err, stdin=subprocess.DEVNULL, start_new_session=True, cwd=root())
    open(os.path.join(d, 'pid'), 'w').write(str(p.pid))
    t0 = time.time()
    while time.time() - t0 < 15:
        if p.poll() is not None:
            sys.exit(f'qemu exited with {p.returncode}:\n' + open(os.path.join(d, 'stderr.log')).read())
        if os.path.exists(os.path.join(d, 'qmp.sock')):
            try:
                q = QMP(os.path.join(d, 'qmp.sock')); st = q.cmd('query-status')
            except (OSError, EOFError):
                time.sleep(0.1)
                continue                  # the socket exists but QEMU is not listening yet
            if boot_hold_power:           # the VM is halted (-S): press power, `cont`, release
                st = boot_hold(q, boot_hold_power)
                out.line(f'{name}: held power for {boot_hold_power} ms from reset')
            q.close()
            console = os.path.join(d, 'console.log')
            out.line(f'{name}: pid {p.pid}, qmp ok, status {st["status"]}, console {console}')
            out.set(name=name, pid=p.pid, status=st['status'], console=console,
                    boot_hold_power_ms=boot_hold_power)
            return
        time.sleep(0.1)
    sys.exit('qemu did not answer on QMP within 15 s')


def cmd_stop(a):
    pid = pid_alive(a.name)
    if not pid:
        out.line(f'{a.name}: not running')
        out.set(name=a.name, pid=None, stopped=False, running=False)
        return
    try:
        q = connect(a.name, 3); q.cmd('quit')
    except Exception:
        pass
    for _ in range(30):
        if not pid_alive(a.name):
            break
        time.sleep(0.1)
    else:
        os.kill(pid, signal.SIGKILL)
    out.line(f'{a.name}: stopped')
    out.set(name=a.name, pid=pid, stopped=True, running=False)


def cmd_reset(a):
    q = connect(a.name); q.cmd('system_reset')
    out.line('reset')
    out.set(name=a.name, reset=True)


def cmd_status(a):
    pid = pid_alive(a.name)
    if not pid:
        out.line(f'{a.name}: not running')
        out.set(name=a.name, pid=None, status='not running', running=False, deep_sleep=False)
        return
    q = connect(a.name)
    st = q.cmd('query-status')
    extra = ''
    deep = False
    try:
        sl = json.loads(q.qom_get(BOARD, 'state')).get('sleep', {})
        if sl.get('sleeping'):
            deep = True
            extra = ' (deep sleep: press power to wake)'
    except Exception:
        pass
    out.line(f'{a.name}: pid {pid}, {st["status"]}, running={st["running"]}{extra}')
    out.set(name=a.name, pid=pid, status=st['status'], running=st['running'], deep_sleep=deep)


def cmd_state(a):
    q = connect(a.name)
    s = q.qom_get(BOARD, 'state')
    s = json.loads(s) if isinstance(s, str) else s
    out.line(json.dumps(s, indent=1))
    out.obj(s)


# ---------------------------------------------------------------- console
def cmd_log(a):
    p = os.path.join(idir(a.name), a.file)
    if not os.path.exists(p):
        sys.exit(f'no {p}')
    collected = []
    with open(p, 'rb') as f:
        data = f.read()
        lines = data.decode('utf-8', 'replace').splitlines()
        if a.since:
            lines = lines[-a.since:]
        for l in lines:
            out.line(l)
        collected += lines
        if a.follow:
            t0 = time.time()
            tail = ''
            while time.time() - t0 < a.timeout:
                d = f.read()
                if d:
                    text = d.decode('utf-8', 'replace')
                    out.write(text)
                    tail += text
                else:
                    time.sleep(0.1)
            collected += tail.splitlines()
    out.set(lines=collected)


def cmd_console_send(a):
    """Send text into the guest's USB Serial/JTAG console (RX path of the USJ model)."""
    p = os.path.join(idir(a.name), 'console.sock')
    data = a.text.encode() + (b'\n' if a.newline else b'')
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); s.settimeout(3); s.connect(p)
    s.sendall(data); s.close()
    out.line(f'sent {len(data)} bytes')
    out.set(name=a.name, sent=len(data))


def cmd_flash_app(a):
    """Replace the app (and optionally bootloader/partition table) inside the instance's flash
    image, keeping NVS/otadata/SD state, and relaunch with the same command line."""
    d = idir(a.name)
    rj = os.path.join(d, 'run.json')
    if not os.path.exists(rj):
        sys.exit('no run.json for this instance; use `run` first')
    info = json.load(open(rj))
    flash = info['flash']
    parts = []
    if a.build:
        b = a.build
        parts += [(0x0, os.path.join(b, 'bootloader.bin')), (0x8000, os.path.join(b, 'partitions.bin')), (0x10000, os.path.join(b, 'firmware.bin'))]
    if a.app:
        parts.append((0x10000, a.app))
    if not parts:
        sys.exit('give --app APP.bin and/or --build DIR')
    if pid_alive(a.name):
        cmd_stop(a)
    written = []
    with open(flash, 'r+b') as f:
        for off, path in parts:
            blob = open(path, 'rb').read()
            f.seek(off); f.write(blob)
            out.line(f'  {off:#09x} {len(blob):9d}  {path}')
            written.append({'offset': off, 'size': len(blob), 'path': path})
    out.set(flash=flash, parts=written)
    launch(a.name, info['cmd'], info.get('boot_hold_power_ms'))


def cmd_wait_text(a):
    p = os.path.join(idir(a.name), a.file)
    t0 = time.time()
    while time.time() - t0 < a.timeout:
        if os.path.exists(p) and a.text.encode() in open(p, 'rb').read():
            dt = time.time() - t0
            out.line(f'found {a.text!r} after {dt:.1f}s')
            out.set(found=True, seconds=round(dt, 2), text=a.text)
            return
        time.sleep(0.2)
    out.set(found=False, seconds=round(time.time() - t0, 2), text=a.text)
    sys.exit(f'timeout: {a.text!r} not seen in {a.timeout}s')


# ---------------------------------------------------------------- panel
def cmd_screenshot(a):
    q = connect(a.name)
    dest = os.path.abspath(a.out)
    q.cmd('screendump', filename=dest, format='png', device='epd')
    shot = os.path.join(idir(a.name), 'last.png')
    try:
        import shutil; shutil.copyfile(dest, shot)
    except OSError:
        pass
    out.line(dest)
    out.set(path=dest)
    if a.diff:
        from PIL import Image, ImageChops
        im1 = Image.open(dest).convert('L'); im2 = Image.open(a.diff).convert('L')
        if im2.size == (im1.size[1], im1.size[0]):
            # CrossPoint's on-device BMP is the frame rotated 90 deg counter-clockwise (portrait);
            # rotate it back to the panel's landscape frame
            im2 = im2.rotate(90, expand=True)
        if im2.size != im1.size:
            im2 = im2.resize(im1.size)
        diff = ImageChops.difference(im1, im2).point(lambda v: 255 if v > 64 else 0)
        n = sum(1 for v in list(diff.getdata()) if v)
        pct = 100.0 * n / (im1.size[0] * im1.size[1])
        out.line(f'{pct:.3f}% of pixels differ ({n})')
        out.set(diff=os.path.abspath(a.diff), diff_percent=round(pct, 3), diff_pixels=n)
        return 1 if n else 0


def refresh_count(q):
    return json.loads(q.qom_get(BOARD, 'state'))['refresh_count']


def cmd_wait_refresh(a):
    """Wait for --count more refreshes from now, or (with --total N) until refresh_count >= N."""
    q = connect(a.name)
    start = refresh_count(q)
    t0 = time.time()
    n = start
    while time.time() - t0 < a.timeout:
        n = refresh_count(q)
        if (a.total is not None and n >= a.total) or (a.total is None and n - start >= a.count):
            out.line(f'refresh {n} after {time.time()-t0:.1f}s')
            out.set(ok=True, refresh=n, seconds=round(time.time() - t0, 2))
            return
        time.sleep(0.05)
    out.set(ok=False, refresh=n)
    sys.exit(f'timeout waiting for refreshes (count={a.count}, total={a.total}, now={n})')


def wait_quiet(q, quiet_s, timeout):
    """Return once no refresh has started for quiet_s seconds (and BUSY is low)."""
    t0 = time.time(); last = refresh_count(q); t_last = t0
    while time.time() - t0 < timeout:
        st = json.loads(q.qom_get(BOARD, 'state'))
        if st['refresh_count'] != last:
            last = st['refresh_count']; t_last = time.time()
        if not st['busy'] and time.time() - t_last >= quiet_s:
            return True
        time.sleep(0.05)
    return False


def cmd_wait_quiet(a):
    q = connect(a.name)
    if not wait_quiet(q, a.seconds, a.timeout):
        out.set(ok=False, refresh=refresh_count(q))
        sys.exit('panel never went quiet')
    out.line(f'quiet for {a.seconds}s')
    out.set(ok=True, refresh=refresh_count(q), seconds=a.seconds)


# ---------------------------------------------------------------- input
def after_input(q, n0, a):
    """With --wait: report the refresh the input triggered (and fail if none came)."""
    out.set(refresh=None, refresh_after_s=None)
    if getattr(a, 'wait', 0):
        t0 = time.time()
        while time.time() - t0 < a.wait:
            n = refresh_count(q)
            if n > n0:
                dt = time.time() - t0
                out.line(f'refresh {n} started {dt:.2f}s after the input')
                out.set(refresh=n, refresh_after_s=round(dt, 2))
                return
            time.sleep(0.02)
        sys.exit(f'no refresh within {a.wait}s after the input (count still {n0})')


def cmd_press(a):
    q = connect(a.name)
    if a.button == 'power' and a.ms < 1500:
        # A wake from deep sleep must still be held when CrossPoint verifies it (~1.3 s after
        # the reset), otherwise the firmware goes straight back to sleep. Hold long enough.
        try:
            if json.loads(q.qom_get(BOARD, 'state')).get('sleep', {}).get('sleeping'):
                out.line('device is in deep sleep: holding power for 1500 ms to wake it')
                out.set(woke_from_deep_sleep=True)
                a.ms = 1500
        except Exception:
            pass
    if getattr(a, 'quiet', 0):
        wait_quiet(q, a.quiet, 30)
    n0 = refresh_count(q)
    q.qom_set(INPUT, f'btn-{a.button}', True)
    time.sleep(a.ms / 1000.0)
    q.qom_set(INPUT, f'btn-{a.button}', False)
    out.line(f'pressed {a.button} for {a.ms} ms')
    out.set(button=a.button, ms=a.ms, read_after_s=None)
    after_input(q, n0, a)


def to_gt911(x, y):
    """Landscape panel pixel -> GT911 raw frame (inverse of pollGt911: swapXY, flipY)."""
    return (PANEL_H - 1 - y), x


def cmd_chord(a):
    """Press several buttons at once (e.g. power right = CrossPoint's screenshot chord)."""
    q = connect(a.name)
    if getattr(a, 'quiet', 0):
        wait_quiet(q, a.quiet, 30)
    n0 = refresh_count(q)
    for b in a.buttons:
        q.qom_set(INPUT, f'btn-{b}', True)
    time.sleep(a.ms / 1000.0)
    for b in a.buttons:
        q.qom_set(INPUT, f'btn-{b}', False)
    out.line(f'chord {"+".join(a.buttons)} for {a.ms} ms')
    out.set(buttons=list(a.buttons), ms=a.ms, read_after_s=None)
    after_input(q, n0, a)


def gt911_clears(q):
    return json.loads(q.qom_get(BOARD, 'state'))['gt911']['clears']


def hold_until_read(q, ms, clears0, max_s=5.0):
    """Keep a touch/Home-pad input asserted for at least `ms`, and until the firmware has consumed
    the GT911 frame (its write of 0 to 0x814E, `state.gt911.clears`). Firmware that polls the
    touch controller between rendering passes of several seconds (CrossPoint) would otherwise miss
    a short tap; it still sees a short touch, since it measures from its own first read. Returns
    the seconds until the frame was read, or None if it never was within max_s."""
    t0 = time.time()
    read_at = None
    while True:
        now = time.time() - t0
        if read_at is None and gt911_clears(q) > clears0:
            read_at = now
        if now >= ms / 1000.0 and (read_at is not None or now >= max_s):
            return read_at
        time.sleep(0.02)


def _read_note(read_at):
    return (f', read by the firmware after {read_at:.2f}s' if read_at is not None
            else ', not read by the firmware within 5 s')


def cmd_tap(a):
    q = connect(a.name)
    if getattr(a, 'quiet', 0):
        wait_quiet(q, a.quiet, 30)
    n0 = refresh_count(q)
    c0 = gt911_clears(q)
    rx, ry = to_gt911(a.x, a.y)
    q.qom_set(INPUT, 'touch', json.dumps([{'x': rx, 'y': ry}]))
    read_at = hold_until_read(q, a.ms, c0)
    q.qom_set(INPUT, 'touch', '[]')
    out.line(f'tapped ({a.x},{a.y}) -> gt911 ({rx},{ry})' + _read_note(read_at))
    out.set(x=a.x, y=a.y, gt911=[rx, ry], ms=a.ms,
            read_after_s=None if read_at is None else round(read_at, 2))
    after_input(q, n0, a)


def cmd_swipe(a):
    """A drag from (x1,y1) to (x2,y2) in landscape pixels: one GT911 point every ~20 ms over --ms,
    each held until the firmware has consumed its frame (up to 0.5 s; the model keeps only the latest
    point, so a point the firmware never read would be lost and the gesture misread), the last one
    released only once read as well. Firmware sees a complete, evenly sampled gesture whatever its
    polling cadence."""
    q = connect(a.name)
    if getattr(a, 'quiet', 0):
        wait_quiet(q, a.quiet, 30)
    n0 = refresh_count(q)
    steps = max(2, a.ms // 20)
    per_ms = a.ms / steps
    unread = 0
    for i in range(steps + 1):
        x = a.x1 + (a.x2 - a.x1) * i // steps
        y = a.y1 + (a.y2 - a.y1) * i // steps
        rx, ry = to_gt911(x, y)
        c0 = gt911_clears(q)
        q.qom_set(INPUT, 'touch', json.dumps([{'x': rx, 'y': ry}]))
        if hold_until_read(q, per_ms, c0, max_s=0.5) is None:
            unread += 1
    q.qom_set(INPUT, 'touch', '[]')
    out.line(f'swiped ({a.x1},{a.y1})->({a.x2},{a.y2}) in {steps + 1} points' + (f', {unread} not read by the firmware' if unread else ', every point read by the firmware'))
    out.set(x1=a.x1, y1=a.y1, x2=a.x2, y2=a.y2, points=steps + 1, unread=unread)
    after_input(q, n0, a)

def cmd_home(a):
    """The capacitive Home pad (GT911 key bit): held for at least --ms and until the firmware reads it."""
    q = connect(a.name)
    if getattr(a, 'quiet', 0):
        wait_quiet(q, a.quiet, 30)
    n0 = refresh_count(q)
    c0 = gt911_clears(q)
    q.qom_set(INPUT, 'home', True)
    read_at = hold_until_read(q, a.ms, c0)
    q.qom_set(INPUT, 'home', False)
    out.line(f'home for {a.ms} ms' + _read_note(read_at))
    out.set(ms=a.ms, read_after_s=None if read_at is None else round(read_at, 2))
    after_input(q, n0, a)


# ---------------------------------------------------------------- peripherals
def cmd_battery(a):
    q = connect(a.name)
    if a.soc is not None: q.qom_set(BOARD, 'battery-soc', a.soc)
    if a.mv is not None: q.qom_set(BOARD, 'battery-mv', a.mv)
    if a.charging is not None: q.qom_set(INPUT, 'charging', a.charging == 'on')
    res = {'soc': q.qom_get(BOARD, 'battery-soc'), 'mv': q.qom_get(BOARD, 'battery-mv'),
           'charging': q.qom_get(INPUT, 'charging')}
    out.line(json.dumps(res))
    out.obj(res)


def cmd_light(a):
    """Frontlight: LEDC duty of the channels routed to GPIO8 (cool) and GPIO9 (warm)."""
    q = connect(a.name)
    s = json.loads(q.qom_get(BOARD, 'state'))
    chans = s.get('ledc', {}).get('channels', [])
    res = {'cool': None, 'warm': None, 'channels': chans}
    for c in chans:
        if c['gpio'] == 8: res['cool'] = c['duty_permille']
        if c['gpio'] == 9: res['warm'] = c['duty_permille']
    res = res if a.verbose else {'cool': res['cool'], 'warm': res['warm']}
    out.line(json.dumps(res))
    out.obj(res)


# ---------------------------------------------------------------- low level
HEX_BYTE = re.compile(r'0x([0-9a-fA-F]{2})\b')


def cmd_mem(a):
    q = connect(a.name)
    text = q.cmd('human-monitor-command', **{'command-line': f'xp /{a.len}xb {a.addr}'})
    out.line(text)
    out.set(addr=a.addr, bytes=[int(v, 16) for v in HEX_BYTE.findall(text or '')])


def cmd_gdb(a):
    import glob
    cands = sorted(glob.glob(os.path.expanduser('~/.platformio/packages/tool-xtensa-esp-elf-gdb/bin/xtensa-esp-elf-gdb*')))
    if not cands:
        sys.exit('xtensa-esp-elf-gdb not found under ~/.platformio')
    elf = a.elf or os.path.join(root(), 'firmware', '.pio', 'build', 'x4pro', 'firmware.elf')
    out.set(gdb=cands[-1], elf=elf, target=':1234')
    out.finish()
    os.execv(cands[-1], [cands[-1], elf, '-ex', 'set arch xtensa', '-ex', 'target remote :1234'])


def cmd_qmp(a):
    q = connect(a.name)
    msg = json.loads(a.json)
    r = q.cmd(msg['execute'], **msg.get('arguments', {}))
    out.line(json.dumps(r, indent=1))
    out.set(**{'return': r})
