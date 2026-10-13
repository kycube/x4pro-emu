"""The commands themselves. Every one of them prints its human line through `out.line` and its
machine fields through `out.set`/`out.obj`, so `--json` changes the presentation and nothing else.
The human strings are contracts: tests/*.py and tools/x4emu_mcp.py parse some of them."""
import argparse
import functools
import json
import os
import re
import shlex
import signal
import socket
import subprocess
import sys
import time

from .output import out, suspended
from .paths import idir, qemu, root
from .qmp import BOARD, INPUT, QMP, connect, pid_alive

PANEL_W, PANEL_H = 800, 480

# `run --boot-hold-power` without a value holds the button until the stock's boot-preflight has
# decided: it samples the power button from guest time ~530 ms through its "effective" window (600
# ms on the device's dumps) and then prints `boot-preflight: source=… hold=…ms decision=…` (~1.1 s
# of guest time into the boot). The button is released when that line is in the console, or after
# BOOT_HOLD_POWER_MS of guest time for a firmware that never prints it (CrossPoint). Both are guest
# events, so a loaded host cannot cut the hold short. `--boot-hold-power MS` holds for MS ms of guest
# time instead, whatever the console says (a deliberately short hold, say).
BOOT_HOLD_POWER_MS = 3000
BOOT_HOLD_AUTO = -1                       # argparse `const` of the bare flag (never a real duration)
BOOT_PREFLIGHT = re.compile(rb'boot-preflight: source=[^\n]*\n')

# `run --deterministic` is `-icount 3` (guest time follows the instruction count) plus a fixed RTC
# seed: `cmd_run` passes `-global driver=x4pro.pcf8563,property=base-epoch,value=N` so the BM8563
# model starts from this epoch second instead of the host's wall clock (0 = host time). None disables
# the seed (a QEMU without the property rejects the -global).
DETERMINISTIC_RTC_EPOCH = 1767225600      # 2026-01-01T00:00:00Z (the property landed with QEMU patch 0014)


# ------------------------------------------------- record / replay of input commands
def guest_ms(q):
    """Guest virtual time in milliseconds (`state.uptime_us`, QEMU's virtual clock)."""
    return json.loads(q.qom_get(BOARD, 'state'))['uptime_us'] // 1000


def record_marker(name):
    """`.x4emu/NAME/record.json` — its existence is what "this instance is recording" means."""
    return os.path.join(idir(name), 'record.json')


def journal_file(name):
    """The journal `x4emu record` is appending this instance's inputs to, or None."""
    try:
        with open(record_marker(name)) as f:
            return json.load(f)['file']
    except (OSError, ValueError, KeyError, TypeError):
        return None


def journal_append(path, t_ms, cmd, args):
    with open(path, 'a') as f:
        f.write(json.dumps({'t_ms': t_ms, 'cmd': cmd, 'args': args}) + '\n')


JOURNAL = {}      # journal command name -> (the undecorated function, its argument names)


def records(argnames, also=()):
    """Mark a command as an *input* command: one journal line per call while the instance records.

    The line carries the guest time the command was issued at (before it waits for anything), its
    name, and the arguments exactly as it received them, so `x4emu replay` can call the same
    function with the same arguments. The recording is invisible in human mode (`--json` gains
    `"recorded": true`); with no recording running it costs one failed open()."""
    def deco(fn):
        keys = tuple(argnames.split())
        name = fn.__name__[4:].replace('_', '-')          # cmd_console_send -> console-send
        for n in (name,) + tuple(also):
            JOURNAL[n] = (fn, keys)

        @functools.wraps(fn)
        def wrapped(a):
            path = journal_file(a.name)
            t_ms = None
            if path:
                try:
                    q = connect(a.name)
                    t_ms = guest_ms(q)
                    q.close()
                except (OSError, EOFError, RuntimeError, ValueError, KeyError):
                    pass                                   # journal the step without a guest time
            rc = fn(a)
            if path:
                journal_append(path, t_ms, getattr(a, 'cmd', None) or name,
                               {k: getattr(a, k, None) for k in keys})
                out.set(recorded=True)
            return rc
        return wrapped
    return deco


def reconnect(name, tries=10):
    """`connect`, retried: QEMU's QMP chardev serves one client at a time, and after a close it can
    need a moment before it accepts the next connection (replay opens one per step)."""
    for _ in range(tries - 1):
        try:
            return connect(name)
        except (OSError, EOFError):
            time.sleep(0.2)
    return connect(name)


# ---------------------------------------------------------------- lifecycle
def cmd_run(a):
    d = idir(a.name)
    if pid_alive(a.name):
        sys.exit(f'instance "{a.name}" already running (pid {pid_alive(a.name)}); `x4emu stop` first')
    os.makedirs(d, exist_ok=True)
    # record.json goes too: a recording belongs to the instance that was booted, so a new boot
    # never appends to the journal of the last one (`x4emu record` comes after `run`).
    for f in ('console.log', 'uart0.log', 'qemu.log', 'stderr.log', 'record.json'):
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
    until_preflight = a.boot_hold_power == BOOT_HOLD_AUTO
    if until_preflight:
        a.boot_hold_power = BOOT_HOLD_POWER_MS
    if a.boot_hold_power:
        if a.gdb:
            sys.exit('--boot-hold-power cannot be combined with --gdb (gdb owns the halted start)')
        cmd += ['-S']            # start halted, press power, then `cont`: see boot_hold()
    icount = a.icount or ('3' if getattr(a, 'deterministic', False) else None)
    if icount:
        cmd += ['-icount', icount]
    if getattr(a, 'deterministic', False) and DETERMINISTIC_RTC_EPOCH is not None:
        # pin the RTC (see DETERMINISTIC_RTC_EPOCH) so the guest's wall clock is the same on every
        # run instead of following the host's
        cmd += ['-global', f'driver=x4pro.pcf8563,property=base-epoch,value={DETERMINISTIC_RTC_EPOCH}']
    for k, v in (('panel', a.panel), ('fast-epd', 'on' if a.fast_epd else None),
                 ('trace-epd', os.path.abspath(a.trace_epd) if a.trace_epd else None)):
        if v:
            cmd += ['-global', f'driver=x4pro.epd,property={k},value={v}']
    if a.trace_i2c:
        cmd += ['-global', f'driver=esp32s3.i2c,property=trace-i2c,value={os.path.abspath(a.trace_i2c)}']
    cmd += a.extra
    json.dump({'cmd': cmd, 'started': time.time(), 'flash': flash, 'sd': a.sd,
               'boot_hold_power_ms': a.boot_hold_power,
               'boot_hold_until_preflight': until_preflight and bool(a.boot_hold_power)},
              open(os.path.join(d, 'run.json'), 'w'), indent=1)
    if a.dry_run:
        out.line(' '.join(shlex.quote(c) for c in cmd))
        out.set(name=a.name, dry_run=True, cmd=cmd, boot_hold_power_ms=a.boot_hold_power)
        return
    launch(a.name, cmd, a.boot_hold_power, until_preflight)


class _ConsoleTail:
    """Incremental reader of an instance's console.log: `seen(pattern)` reads what is new and tells
    whether the pattern has appeared (patterns that straddle two reads are found)."""
    def __init__(self, path):
        self.path, self.pos, self.buf = path, 0, b''

    def seen(self, pattern):
        try:
            with open(self.path, 'rb') as f:
                f.seek(self.pos)
                new = f.read()
        except OSError:
            return False
        self.pos += len(new)
        self.buf = self.buf[-4096:] + new
        return bool(pattern.search(self.buf))


def boot_hold(q, ms, until_preflight=False):
    """Hold the power button (GPIO3, active-low) from reset: for `ms` milliseconds of guest time, or
    with `until_preflight` until the stock's boot-preflight has printed its decision (at most `ms`).

    The VM was started with `-S`, so the button is asserted before the first instruction runs;
    `cont` then releases the CPUs and the guest finds the button already down when it samples it.
    The stock firmware's boot-preflight wants that (`x4emu press power` after its deep sleep is the
    other way in). The preflight measures the hold on the guest clock, so the hold is measured there
    too (under `-icount` on a loaded host guest time runs slower than the host's); a guest clock that
    stands still for 2 host seconds in a paused VM ends it (see `hold_until`). Returns (the
    query-status reply after the release, the guest ms held, whether the preflight line was seen --
    None without `until_preflight`)."""
    q.qom_set(INPUT, 'btn-power', True)
    q.cmd('cont')
    if until_preflight:
        tail = _ConsoleTail(os.path.join(idir(q.name), 'console.log'))
        decided = lambda st: tail.seen(BOOT_PREFLIGHT)          # noqa: E731
        # hold_until holds for `ms` *and* until seen; here either ends the hold: check the line on
        # every poll and stop at the first of the two
        t0 = time.time()
        st = board(q)
        g0 = g_last = st['uptime_us']
        t_moved = t0
        seen = False
        while True:
            seen = decided(st)
            now = time.time()
            if st['uptime_us'] != g_last:
                g_last, t_moved = st['uptime_us'], now
            if seen or (st['uptime_us'] - g0) / 1000.0 >= ms or now - t0 >= 20 * ms / 1000.0 \
                    or (now - t_moved >= 2.0 and vm_paused(q)):
                break
            time.sleep(0.01)
            st = board(q)
        held_ms = (st['uptime_us'] - g0) / 1000.0
    else:
        _, held_ms, _ = hold_until(q, ms, lambda st: True, 0)
        seen = None
    q.qom_set(INPUT, 'btn-power', False)
    return q.cmd('query-status'), held_ms, seen


def launch(name, cmd, boot_hold_power=None, until_preflight=False):
    d = idir(name)
    for f in ('console.log', 'uart0.log', 'qemu.log', 'stderr.log', 'console.sock', 'last_input.json'):
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
                q = QMP(os.path.join(d, 'qmp.sock'), name=name); st = q.cmd('query-status')
            except (OSError, EOFError):
                time.sleep(0.1)
                continue                  # the socket exists but QEMU is not listening yet
            if boot_hold_power:           # the VM is halted (-S): press power, `cont`, release
                st, held_ms, seen = boot_hold(q, boot_hold_power, until_preflight)
                if seen is None:
                    out.line(f'{name}: held power for {boot_hold_power} ms from reset')
                else:
                    out.line(f'{name}: held power for {held_ms:.0f} ms from reset'
                             + (' (until the boot-preflight decided)' if seen
                                else f' (no boot-preflight line within {boot_hold_power} ms)'))
                out.set(boot_hold_held_ms=round(held_ms), boot_hold_preflight=seen)
            mark_input(q)                 # the boot (and the hold): wait-quiet counts from here
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
    forget_input(a.name)
    out.line(f'{a.name}: stopped')
    out.set(name=a.name, pid=pid, stopped=True, running=False)


@records('')
def cmd_reset(a):
    q = connect(a.name); q.cmd('system_reset')
    mark_input(q)
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


@records('text newline')
def cmd_console_send(a):
    """Send text into the guest's USB Serial/JTAG console (RX path of the USJ model)."""
    p = os.path.join(idir(a.name), 'console.sock')
    data = a.text.encode() + (b'\n' if a.newline else b'')
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); s.settimeout(3); s.connect(p)
    s.sendall(data); s.close()
    try:
        q = connect(a.name, 3); mark_input(q); q.close()
    except (OSError, EOFError, RuntimeError):
        pass
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
    launch(a.name, info['cmd'], info.get('boot_hold_power_ms'), info.get('boot_hold_until_preflight', False))


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
def screenshot_to(name, path):
    """Take a fresh panel screenshot into `path` over a short QMP connection (closed again before
    returning, since the socket serves one client at a time) and update `.x4emu/<name>/last.png`.
    Returns the absolute path written. Shared by `cmd_screenshot` and `x4emu watch`."""
    q = connect(name)
    dest = os.path.abspath(path)
    q.cmd('screendump', filename=dest, format='png', device='epd')
    q.close()
    shot = os.path.join(idir(name), 'last.png')
    try:
        import shutil; shutil.copyfile(dest, shot)
    except OSError:
        pass
    return dest


def cmd_screenshot(a):
    dest = screenshot_to(a.name, a.out)
    out.line(dest)
    out.set(path=dest)
    if a.diff:
        from PIL import Image, ImageChops
        im1 = Image.open(dest).convert('L'); im2 = Image.open(a.diff).convert('L')
        if im2.size == (im1.size[1], im1.size[0]):
            # CrossPoint's on-device BMP is the upright portrait frame, i.e. the panel rotated 90 deg
            # clockwise; rotating it 90 deg counter-clockwise gives the landscape frame back
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


# The last input to an instance, for wait-quiet: `.x4emu/NAME/last_input.json` holds the guest time
# the last input `x4emu` delivered ended, the pid of the QEMU process it went to, and the board's
# input counters right after it (`input_signature`). A mark counts only while that same process runs
# and only if it is not in that process's future, so a mark left by an earlier boot can never make
# wait-quiet wait for a guest time the new boot has not reached (`run`, `flash-app` and `stop` also
# replace or delete it). Counters that differ from the mark's mean an input arrived some other way
# -- a raw `x4emu qmp` qom-set, another QMP client -- at a time nobody wrote down: wait-quiet then
# counts from the moment it saw the counters move, as a wait that starts at the call always did.
def last_input_file(name):
    return os.path.join(idir(name), 'last_input.json')


def input_signature(st):
    """The board's input counters (GT911 input sequence, button level changes), or None (an older
    QEMU without them)."""
    g = st.get('gt911') or {}
    ch = (st.get('gpio_in') or {}).get('changes')
    if 'input_seq' not in g or not isinstance(ch, dict):
        return None
    return [g['input_seq'], ch.get('left', 0), ch.get('right', 0), ch.get('power', 0)]


def mark_input(q, st=None, uptime_us=None):
    """Note that an input to `q`'s instance has just ended (at `uptime_us`, default now)."""
    name = getattr(q, 'name', None)
    pid = pid_alive(name) if name else None
    if not pid:
        return
    try:
        st = st or board(q)
        path = last_input_file(name)
        tmp = f'{path}.{os.getpid()}'
        with open(tmp, 'w') as f:
            json.dump({'pid': pid, 'uptime_us': st['uptime_us'] if uptime_us is None else uptime_us,
                       'sig': input_signature(st)}, f)
        os.replace(tmp, path)                  # atomic: a concurrent reader sees the old or the new mark
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, EOFError):
        pass


def forget_input(name):
    try:
        os.remove(last_input_file(name))
    except FileNotFoundError:
        pass


def read_input_mark(name, pid, now_us):
    """The mark of the last input to this QEMU process (`pid`), or None (none, or a stale one)."""
    if not (name and pid):
        return None
    try:
        with open(last_input_file(name)) as f:
            m = json.load(f)
        g = int(m['uptime_us'])
        return m if m.get('pid') == pid and 0 <= g <= now_us else None
    except (OSError, ValueError, KeyError, TypeError):
        return None


def wait_quiet(q, quiet_s, timeout, info=None):
    """Return True once the panel has been idle for `quiet_s` seconds of *guest* time (False after
    `timeout` host seconds). Idle: BUSY low, and neither a refresh nor an input for `quiet_s`, counted
    from the later of the last refresh (`state.last_refresh_us`, the guest time it started) and the end
    of the last input (`mark_input`; an input that did not come through `x4emu` counts from when it was
    seen, see above). A panel that has already been idle that long returns at once; this used to
    restart the clock at the call and wait the full `quiet_s` again.

    Before the first refresh, and with a QEMU that lacks `last_refresh_us` or the input counters, the
    idle time starts at the call (and at every refresh seen meanwhile), as it always did. A paused VM
    -- the deep-sleep model pauses it, so does a QMP `stop` -- leaves nothing to wait for: `quiet_s`
    host seconds without the clock moving in a paused VM count as quiet, and a sleeping board is
    quiet. `info` (a dict) receives `idle_s` (guest seconds idle when it returned)
    and `waited_s` (host seconds spent here)."""
    name = getattr(q, 'name', None)
    pid = pid_alive(name) if name else None
    t0 = time.time()
    st = board(q)
    g_call = g_moved = st['uptime_us']
    t_moved = t0
    count, g_count = st['refresh_count'], g_call     # the fallback: refreshes seen from here on
    guest_idle = 'last_refresh_us' in st and input_signature(st) is not None
    first_seen = {}                                  # input counters -> guest time first seen here
    while True:
        now = time.time()
        g = st['uptime_us']
        if g != g_moved:
            g_moved, t_moved = g, now
        if st['refresh_count'] != count:
            count, g_count = st['refresh_count'], g
        since = st['last_refresh_us'] if guest_idle and st['refresh_count'] else g_count
        if guest_idle:
            sig = input_signature(st)
            seen = first_seen.setdefault(tuple(sig), g)
            m = read_input_mark(name, pid, g)
            if m is not None and m.get('sig') == sig:
                since = max(since, int(m['uptime_us']))
            else:
                since = max(since, seen)
                if seen == g_call and q.name:       # unmarked input before the call: remember it
                    mark_input(q, st, g_call)
        idle_s = (g - since) / 1e6
        paused = now - max(t_moved, t0) >= quiet_s and vm_paused(q)
        done = (not st['busy'] and idle_s >= quiet_s) or paused or st.get('sleep', {}).get('sleeping')
        if done or now - t0 >= timeout:
            if info is not None:
                info.update(idle_s=round(max(idle_s, 0.0), 3), waited_s=round(now - t0, 3))
            return bool(done)
        time.sleep(0.05)
        st = board(q)


def cmd_wait_quiet(a):
    q = connect(a.name)
    info = {}
    if not wait_quiet(q, a.seconds, a.timeout, info):
        out.set(ok=False, refresh=refresh_count(q), **info)
        sys.exit('panel never went quiet')
    out.line(f'quiet for {a.seconds}s')
    out.set(ok=True, refresh=refresh_count(q), seconds=a.seconds, **info)


# ---------------------------------------------------------------- input
# How an input reaches the firmware (docs/x4emu.md, "Input delivery"). Both firmwares sample their
# inputs between pieces of work that can take seconds, and guest time follows the host clock, so on a
# loaded host the guest executes less per guest millisecond and polls less often: an input held for
# a fixed number of *host* milliseconds can fall between two polls, or into a single one. So every
# input is held the way a finger holds it, in guest terms:
#   - at least --ms of *guest* time (state.uptime_us), and
#   - until the firmware has seen it: for the GT911 (touch, the Home pad) until it has consumed a
#     frame that carries it (`state.gt911.read_seq` reaching the input's `input_seq`; with an older
#     QEMU, until `clears` moves); for a button until the firmware has sampled the pressed level
#     BUTTON_SAMPLES times, sample-gap-us of guest time apart (`state.gpio_in.samples`: CrossPoint
#     commits a button change only when two polls more than 5 ms apart agree);
#   - and then released, and held released until the firmware has seen the release as well (the
#     next input cannot merge with this one, and `--wait` starts from an input the firmware has
#     completed).
# Every wait is bounded in host time, so a firmware that never reads an input costs seconds, not a
# hang; the output says what was and was not seen.
SEEN_MAX_S = 5.0          # host seconds an input stays asserted waiting for the firmware to read it
RELEASE_MAX_S = 5.0       # host seconds `x4emu` waits for the firmware to see the release
BUTTON_SAMPLES = 2        # samples of the pressed (and then the released) level a button waits for
BUTTON_SEEN_MAX_S = 2.0   # a button is not held past this (host s) for want of samples: a long hold
                          # changes its meaning (power >= 400 ms sleeps CrossPoint)


def board(q):
    s = q.qom_get(BOARD, 'state')
    return json.loads(s) if isinstance(s, str) else s


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


def vm_paused(q):
    """True when the VM is not running (the deep-sleep model's pause, a QMP `stop`)."""
    try:
        return not q.cmd('query-status').get('running', True)
    except (RuntimeError, OSError, EOFError):
        return False


def hold_until(q, ms, seen, max_s):
    """Keep an input as it is for at least `ms` of guest time and until `seen(state)` holds.

    `max_s` (host seconds) bounds the wait for `seen`; the guest-time part is bounded too: it gives
    up once the guest clock has stood still for 2 host seconds in a paused VM (deep sleep, a `stop`)
    or after 20x its length in host time. A clock that stands still in a *running* VM does not end
    it: under `-icount` guest time only moves with executed instructions, and a vCPU blocked on the
    host's disk or CPU for a moment would otherwise cut the hold short. Returns (host seconds until
    `seen` first held, or None; guest ms the input was held; the last state)."""
    t0 = time.time()
    st = board(q)
    g0 = g_last = st['uptime_us']
    t_moved = t0
    seen_at = None
    while True:
        now = time.time()
        if seen_at is None and seen(st):
            seen_at = now - t0
        held_ms = (st['uptime_us'] - g0) / 1000.0
        if st['uptime_us'] != g_last:
            g_last, t_moved = st['uptime_us'], now
        stalled = now - t_moved >= 2.0 and vm_paused(q)
        held = held_ms >= ms or stalled or now - t0 >= 20 * ms / 1000.0 + max_s
        if held and (seen_at is not None or now - t0 >= max_s or stalled):
            return seen_at, held_ms, st
        time.sleep(0.01)
        st = board(q)


def _gt911_seen(q, clears0):
    """A predicate "the firmware has consumed the frame carrying the input just set". With the GT911
    model's frame sequence numbers (input_seq / read_seq) that is exact; an older QEMU only offers
    `clears`, i.e. "some frame was consumed since"."""
    g = board(q)['gt911']
    if 'input_seq' in g:
        seq = g['input_seq']
        return lambda st: st['gt911']['read_seq'] >= seq
    return lambda st: st['gt911']['clears'] > clears0


def gt911_input(q, prop, down, up, ms, max_s=SEEN_MAX_S):
    """Assert a GT911 input (`touch` points or the `home` key), hold it (see above), release it and
    wait for the release to be consumed. Returns (read_at, held_ms, release_read_at)."""
    c0 = board(q)['gt911']['clears']
    q.qom_set(INPUT, prop, down)
    read_at, held_ms, st = hold_until(q, ms, _gt911_seen(q, c0), max_s)
    c1 = st['gt911']['clears']
    q.qom_set(INPUT, prop, up)
    released = _gt911_seen(q, c1)
    rel_at, _, _ = hold_until(q, 0, released, RELEASE_MAX_S)
    mark_input(q)
    return read_at, held_ms, rel_at


def _read_note(read_at, rel_at=None, what='the firmware'):
    s = (f', read by {what} after {read_at:.2f}s' if read_at is not None
         else f', not read by {what} within {SEEN_MAX_S:.0f} s')
    if read_at is not None and rel_at is None:
        s += ', release not read'
    return s


def gt911_clears(q):
    return board(q)['gt911']['clears']


def button_input(q, buttons, ms):
    """Press `buttons` together, hold them (see above), release them, and wait until the firmware
    has sampled the release. Returns (read_at, held_ms, release_read_at, samples while pressed)."""
    sampled = 'gpio_in' in board(q)
    for b in buttons:
        q.qom_set(INPUT, f'btn-{b}', True)
    if sampled:
        def seen(st):
            return all(st['gpio_in']['samples'][b] >= BUTTON_SAMPLES for b in buttons)
    else:
        def seen(st):                                     # an older QEMU: guest time alone
            return False
    read_at, held_ms, st = hold_until(q, ms, seen, BUTTON_SEEN_MAX_S if sampled else 0)
    pressed_samples = {b: st['gpio_in']['samples'][b] for b in buttons} if sampled else None
    for b in buttons:
        q.qom_set(INPUT, f'btn-{b}', False)
    rel_at = None
    if sampled:
        def released(st):
            # a press that put the firmware to sleep is not going to be sampled again
            return st.get('sleep', {}).get('sleeping') or seen(st)
        rel_at, _, _ = hold_until(q, 0, released, BUTTON_SEEN_MAX_S)
    mark_input(q)
    return read_at, held_ms, rel_at, pressed_samples


def _button_note(read_at, rel_at, sampled):
    if not sampled:
        return ''
    if read_at is None:
        return f', not sampled {BUTTON_SAMPLES}x by the firmware within {BUTTON_SEEN_MAX_S:.0f} s'
    return f', sampled by the firmware after {read_at:.2f}s' + ('' if rel_at is not None else ', release not sampled')


@records('button ms wait quiet', also=('hold',))
def cmd_press(a):
    q = connect(a.name)
    if a.button == 'power' and a.ms < 1500:
        # A wake from deep sleep must still be held when CrossPoint verifies it (~1.3 s after
        # the reset), otherwise the firmware goes straight back to sleep. Hold long enough (guest
        # time: the paused VM's clock starts again with the wake).
        try:
            if board(q).get('sleep', {}).get('sleeping'):
                out.line('device is in deep sleep: holding power for 1500 ms to wake it')
                out.set(woke_from_deep_sleep=True)
                a.ms = 1500
        except Exception:
            pass
    if getattr(a, 'quiet', 0):
        wait_quiet(q, a.quiet, 30)
    n0 = refresh_count(q)
    sampled = 'gpio_in' in board(q)
    read_at, held_ms, rel_at, _ = button_input(q, [a.button], a.ms)
    out.line(f'pressed {a.button} for {a.ms} ms' + _button_note(read_at, rel_at, sampled))
    out.set(button=a.button, ms=a.ms, held_ms=round(held_ms), read_after_s=None if read_at is None else round(read_at, 2),
            release_read=None if not sampled else rel_at is not None)
    after_input(q, n0, a)


def to_gt911(x, y):
    """Landscape panel pixel -> GT911 raw frame (inverse of pollGt911: swapXY, flipY)."""
    return (PANEL_H - 1 - y), x


@records('buttons ms wait quiet')
def cmd_chord(a):
    """Press several buttons at once (e.g. power right = CrossPoint's screenshot chord)."""
    q = connect(a.name)
    if getattr(a, 'quiet', 0):
        wait_quiet(q, a.quiet, 30)
    n0 = refresh_count(q)
    sampled = 'gpio_in' in board(q)
    read_at, held_ms, rel_at, _ = button_input(q, list(a.buttons), a.ms)
    out.line(f'chord {"+".join(a.buttons)} for {a.ms} ms' + _button_note(read_at, rel_at, sampled))
    out.set(buttons=list(a.buttons), ms=a.ms, held_ms=round(held_ms),
            read_after_s=None if read_at is None else round(read_at, 2),
            release_read=None if not sampled else rel_at is not None)
    after_input(q, n0, a)


@records('x y ms wait quiet')
def cmd_tap(a):
    q = connect(a.name)
    if getattr(a, 'quiet', 0):
        wait_quiet(q, a.quiet, 30)
    n0 = refresh_count(q)
    rx, ry = to_gt911(a.x, a.y)
    read_at, held_ms, rel_at = gt911_input(q, 'touch', json.dumps([{'x': rx, 'y': ry}]), '[]', a.ms)
    out.line(f'tapped ({a.x},{a.y}) -> gt911 ({rx},{ry})' + _read_note(read_at, rel_at))
    out.set(x=a.x, y=a.y, gt911=[rx, ry], ms=a.ms, held_ms=round(held_ms),
            read_after_s=None if read_at is None else round(read_at, 2), release_read=rel_at is not None)
    after_input(q, n0, a)


@records('x1 y1 x2 y2 ms wait quiet')
def cmd_swipe(a):
    """A drag from (x1,y1) to (x2,y2) in landscape pixels: one GT911 point every ~20 ms of guest time
    over --ms, each held until the firmware has consumed a frame carrying it (up to 0.5 s; the model
    reports only the latest point, so a point the firmware never read would be lost and the gesture
    misread), the release held until consumed as well. Firmware sees a complete, evenly sampled
    gesture whatever its polling cadence."""
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
        if hold_until(q, per_ms, _gt911_seen(q, c0), 0.5)[0] is None:
            unread += 1
    c1 = gt911_clears(q)
    q.qom_set(INPUT, 'touch', '[]')
    rel_at = hold_until(q, 0, _gt911_seen(q, c1), RELEASE_MAX_S)[0]
    mark_input(q)
    out.line(f'swiped ({a.x1},{a.y1})->({a.x2},{a.y2}) in {steps + 1} points'
             + (f', {unread} not read by the firmware' if unread else ', every point read by the firmware')
             + ('' if rel_at is not None else ', release not read'))
    out.set(x1=a.x1, y1=a.y1, x2=a.x2, y2=a.y2, points=steps + 1, unread=unread, release_read=rel_at is not None)
    after_input(q, n0, a)


@records('ms wait quiet')
def cmd_home(a):
    """The capacitive Home pad (GT911 key bit): held for at least --ms and until the firmware reads it."""
    q = connect(a.name)
    if getattr(a, 'quiet', 0):
        wait_quiet(q, a.quiet, 30)
    n0 = refresh_count(q)
    read_at, held_ms, rel_at = gt911_input(q, 'home', True, False, a.ms)
    out.line(f'home for {a.ms} ms' + _read_note(read_at, rel_at))
    out.set(ms=a.ms, held_ms=round(held_ms), read_after_s=None if read_at is None else round(read_at, 2),
            release_read=rel_at is not None)
    after_input(q, n0, a)


# ---------------------------------------------------------------- peripherals
@records('soc mv charging')
def cmd_battery(a):
    q = connect(a.name)
    if a.soc is not None: q.qom_set(BOARD, 'battery-soc', a.soc)
    if a.mv is not None: q.qom_set(BOARD, 'battery-mv', a.mv)
    if a.charging is not None: q.qom_set(INPUT, 'charging', a.charging == 'on')
    res = {'soc': q.qom_get(BOARD, 'battery-soc'), 'mv': q.qom_get(BOARD, 'battery-mv'),
           'charging': q.qom_get(INPUT, 'charging')}
    mark_input(q)
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


# ---------------------------------------------------------------- record / replay / guest time
def cmd_record(a):
    """Start (or `--stop`, end) journalling this instance's input commands into FILE.

    While the marker exists every input command (press, hold, chord, tap, swipe, home, battery,
    console-send, reset) appends one line to FILE; nothing else is recorded and nothing is printed."""
    marker = record_marker(a.name)
    if a.stop:
        path = journal_file(a.name)
        try:
            os.remove(marker)
        except FileNotFoundError:
            path = None
        if path is None:
            out.line(f'{a.name}: not recording')
            out.set(name=a.name, recording=False, file=None, steps=0)
            return
        steps = len(read_journal(path, strict=False))
        out.line(f'{a.name}: recording stopped, {steps} steps in {path}')
        out.set(name=a.name, recording=False, file=path, steps=steps)
        return
    if not a.file:
        sys.exit('give a journal file (`x4emu record FILE`) or `--stop`')
    path = os.path.abspath(a.file)
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    open(path, 'w').close()                     # one `record` = one journal, from scratch
    os.makedirs(idir(a.name), exist_ok=True)
    with open(marker, 'w') as f:
        json.dump({'file': path}, f)
    out.line(f'{a.name}: recording input to {path}')
    out.set(name=a.name, recording=True, file=path, steps=0)


def wait_guest_until(q, target_ms, timeout):
    """Poll the board's virtual clock until it reaches `target_ms`; the guest time reached, or None
    if `timeout` host seconds passed first (this never blocks the host past its timeout)."""
    t0 = time.time()
    while True:
        now = guest_ms(q)
        if now >= target_ms:
            return now
        if time.time() - t0 >= timeout:
            return None
        time.sleep(0.05)


def guest_wait(a, target):
    """Shared body of wait-guest-ms / wait-guest-until: wait for the guest clock, print where it
    got to. `target(start_ms)` says which guest time to wait for. A host timeout is a failure
    (exit 1), as with the other wait-* commands."""
    q = connect(a.name)
    start = guest_ms(q)
    target_ms = target(start)
    t0 = time.time()
    now = wait_guest_until(q, target_ms, a.timeout)
    ok = now is not None
    if not ok:
        now = guest_ms(q)
    fields = dict(ok=ok, guest_ms=now, advanced_ms=now - start, target_ms=target_ms,
                  seconds=round(time.time() - t0, 2))
    if not ok:
        out.set(**fields)
        sys.exit(f'timeout: guest time reached {now} ms, not {target_ms} ms, in {a.timeout}s')
    out.line(f'guest {now} ms (+{now - start} ms in {time.time() - t0:.1f}s)')
    out.set(**fields)


def cmd_wait_guest_ms(a):
    """Wait until the guest's virtual clock has advanced by MS from now (not a host sleep)."""
    guest_wait(a, lambda start: start + a.ms)


def cmd_wait_guest_until(a):
    """Wait until the guest's virtual clock reaches MS (the absolute form `replay` uses)."""
    guest_wait(a, lambda start: a.ms)


# What a replayed step shows in human mode after `t=… ms <cmd>` (the rest of the arguments are in
# the journal): the values that identify the input.
STEP_SHOW = {'tap': ('x', 'y'), 'swipe': ('x1', 'y1', 'x2', 'y2'), 'press': ('button',),
             'hold': ('button',), 'chord': ('buttons',), 'home': (), 'reset': (),
             'battery': ('soc', 'mv', 'charging'), 'console-send': ('text',)}


def step_text(step):
    args = step.get('args') or {}
    vals = [v for k in STEP_SHOW.get(step['cmd'], ()) if (v := args.get(k)) is not None]
    return ''.join(' ' + ('+'.join(map(str, v)) if isinstance(v, list) else str(v)) for v in vals)


def read_journal(path, strict=True):
    """The steps of a journal file: one {"t_ms", "cmd", "args"} object per line."""
    steps = []
    if not os.path.exists(path):
        if strict:
            sys.exit(f'no journal {path}')
        return steps
    with open(path) as f:
        for n, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                step = json.loads(line)
            except ValueError:
                if strict:
                    sys.exit(f'{path}:{n}: not a JSON line')
                continue
            if strict and step.get('cmd') not in JOURNAL:
                sys.exit(f'{path}:{n}: {step.get("cmd")!r} is not a replayable command')
            steps.append(step)
    return steps


def cmd_replay(a):
    """Replay a journal into this instance: wait for the guest clock to reach each step's t_ms,
    then call the same command function with the recorded arguments (in this process, so the
    replayed commands behave exactly as they did while recording — including their own
    `--quiet`/`--wait`). Their output is suspended; one line per step is printed instead."""
    path = os.path.abspath(a.file)
    steps = read_journal(path)
    if not steps:
        sys.exit(f'{path}: nothing to replay')
    q = connect(a.name)
    now = guest_ms(q)
    first = steps[0].get('t_ms')
    if a.wait_times and first is not None and now > first:
        out.set(file=path, steps=0, guest_ms_end=now)
        sys.exit(f'guest time is already {now} ms, past the first step ({first} ms): replay onto a '
                 'freshly booted instance, or pass --no-wait to run the steps back to back')
    done = []
    t0 = time.time()
    for n, step in enumerate(steps, 1):
        t_ms = step.get('t_ms')
        if a.wait_times and t_ms is not None and wait_guest_until(q, t_ms, a.timeout) is None:
            out.set(file=path, steps=len(done), guest_ms_end=guest_ms(q), replayed=done)
            sys.exit(f'step {n} ({step["cmd"]}): guest time did not reach {t_ms} ms in {a.timeout}s')
        fn, keys = JOURNAL[step['cmd']]
        args = step.get('args') or {}
        ns = argparse.Namespace(name=a.name, cmd=step['cmd'],
                                **{k: args.get(k) for k in keys})
        q.close()                       # the command opens its own connection (one client at a time)
        with suspended():
            fn(ns)
        q = reconnect(a.name)
        out.line(f't={t_ms} ms {step["cmd"]}{step_text(step)}')
        done.append({'t_ms': t_ms, 'cmd': step['cmd'], 'guest_ms': guest_ms(q)})
    end = guest_ms(q)
    out.line(f'replayed {len(done)} steps, guest {end} ms in {time.time() - t0:.1f}s')
    out.set(file=path, steps=len(done), guest_ms_end=end, seconds=round(time.time() - t0, 2),
            replayed=done)
