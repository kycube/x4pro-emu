#!/usr/bin/env python3
"""MCP server for the Xteink X4 Pro emulator and the desk device.

Exposes the x4emu CLI, the device toolbox and the build steps as typed tools, with screenshots
returned inline as images. Register (project scope) with the `.mcp.json` at the repo root, or:

  claude mcp add x4emu -- /Users/mini/x4pro-emu/.venv/bin/python /Users/mini/x4pro-emu/tools/x4emu_mcp.py

Every emulator tool takes `name` (instance, default dev0); instances live in .x4emu/<name>/.
Device tools never write flash unless `confirm=True`, and the underlying scripts still re-read
the device and refuse without the verified backup (see CLAUDE.md, device rules).

The emulator tools run the `x4emu` package in-process (`x4emu.api.run`/`run_text`, see
docs/x4emu.md) instead of shelling out to `tools/x4emu`: same commands, same output, one Python
process instead of one subprocess per call. `device_*` and `build_*` stay on `subprocess` — that is
the right tool for another program (pio, device.py) or for something that must survive this
process (a stopped emulator instance keeps running; a device write should not).
"""
import glob, json, os, subprocess, sys, time
from mcp.server.mcpserver import MCPServer, Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)              # import the package from this checkout, not site-packages
from x4emu import api                 # noqa: E402

PY = sys.executable
DEVICE = os.path.join(ROOT, 'tools', 'device.py')

server = MCPServer('x4emu', instructions=(
    'Xteink X4 Pro emulator (QEMU, ESP32-S3) and desk-device tools. Typical loop: build_flash_image -> '
    'emu_run -> emu_wait_text("Entering activity: Home") -> emu_wait_quiet -> emu_tap/emu_press -> '
    'emu_screenshot. Inputs are dropped while the firmware paints: use quiet>0 or emu_wait_quiet first. '
    'Panel coordinates are the 800x480 landscape screenshot; CrossPoint draws its portrait UI rotated on it.'))

def _idir(name):
    return os.path.join(ROOT, '.x4emu', name)

def _sargs(*parts):
    """Command-line arguments for `api.run`/`api.run_text`: every element as a string (argparse
    expects strings; the callers below build lists mixing flags, text and numbers)."""
    return [str(p) for p in parts]

# ---------------------------------------------------------------- emulator lifecycle
@server.tool()
def emu_run(flash: str, name: str = 'dev0', sd: str | None = None, fast_epd: bool = True,
            panel: str | None = None, trace_epd: bool = False, trace_i2c: bool = False,
            gdb: bool = False, icount: int | None = None, efuse: str | None = None,
            boot_hold_power: int | None = None, deterministic: bool = False) -> str:
    """Start an emulator instance from a 16 MB flash image (see build_flash_image). fast_epd makes
    panel refreshes take 2 ms instead of the device's 40/1326/483 ms. panel: ssd1677|uc8179|uc8279
    (default uc8279, the desk unit). Traces land in .x4emu/<name>/{epd,i2c}.jsonl. boot_hold_power
    holds the power button from reset for MS ms (default 3000 with no value given): the way the
    stock firmware's boot-preflight accepts a cold boot instead of deep-sleeping. deterministic
    makes guest time follow the instruction count and pins the RTC, so a `emu_record`ed script
    replays onto the same pixels."""
    args = ['run', '--flash', flash]
    if sd: args += ['--sd', sd]
    if fast_epd: args += ['--fast-epd']
    if panel: args += ['--panel', panel]
    if trace_epd: args += ['--trace-epd', os.path.join(_idir(name), 'epd.jsonl')]
    if trace_i2c: args += ['--trace-i2c', os.path.join(_idir(name), 'i2c.jsonl')]
    if gdb: args += ['--gdb']
    if icount is not None: args += _sargs('--icount', icount)
    if efuse: args += ['--efuse', efuse]
    if boot_hold_power is not None: args += _sargs('--boot-hold-power', boot_hold_power)
    if deterministic: args += ['--deterministic']
    return api.run_text(args, name=name)

@server.tool()
def emu_stop(name: str = 'dev0') -> str:
    """Stop an instance (QMP quit, SIGKILL fallback)."""
    return api.run_text(['stop'], name=name)

@server.tool()
def emu_reset(name: str = 'dev0') -> str:
    """System reset (like a hardware reset; panel RAM and image survive)."""
    return api.run_text(['reset'], name=name)

@server.tool()
def emu_status(name: str = 'dev0') -> str:
    """pid, QEMU run state, and whether the sleep model has paused it (deep sleep)."""
    return api.run_text(['status'], name=name)

@server.tool()
def emu_state(name: str = 'dev0') -> dict:
    """Board state JSON: panel/refresh counters, BUSY, GPIO levels, buttons, touch, GT911, I2C,
    battery, RTC, LEDC channels (frontlight duty), sleep state, console/SPI counters, analog-master
    (ana_i2c) / SENS (saradc) / radio-stub (rf, with `hot` polls) counters, iolog_hot (unmodelled
    registers polled past the log cap)."""
    return api.run(['state'], name=name)

# ---------------------------------------------------------------- observation
@server.tool()
def emu_screenshot(name: str = 'dev0', save_path: str | None = None, diff_path: str | None = None) -> list:
    """Screenshot of the e-paper panel (800x480 PNG) returned inline. diff_path: another PNG or a
    device BMP (480x800, un-rotated automatically); the text part reports the percentage of pixels
    that differ."""
    out_path = save_path or os.path.join(_idir(name), f'shot-{int(time.time())}.png')
    args = ['screenshot', out_path]
    if diff_path: args += ['--diff', diff_path]
    text = api.run_text(args, name=name)
    if not os.path.exists(out_path):
        raise RuntimeError(f'screenshot failed: {text}')
    return [f'{out_path}\n{text}', Image(data=open(out_path, 'rb').read(), format='png')]

@server.tool()
def emu_console(name: str = 'dev0', tail: int = 60, file: str = 'console.log', grep: str | None = None) -> str:
    """Tail of the USB Serial/JTAG console (file=console.log), UART0 (uart0.log) or qemu.log; grep
    filters lines by substring first."""
    p = os.path.join(_idir(name), file)
    if not os.path.exists(p):
        return f'(no {p})'
    lines = open(p, errors='replace').read().splitlines()
    if grep:
        lines = [l for l in lines if grep in l]
    return '\n'.join(lines[-tail:])

@server.tool()
def emu_trace_tail(name: str = 'dev0', kind: str = 'epd', tail: int = 40) -> str:
    """Last lines of the panel (kind=epd) or I2C (kind=i2c) JSON trace; needs trace_* in emu_run."""
    p = os.path.join(_idir(name), f'{kind}.jsonl')
    if not os.path.exists(p):
        return f'(no {p}; start with trace_{kind}=True)'
    return '\n'.join(open(p, errors='replace').read().splitlines()[-tail:])

@server.tool()
def emu_wait_text(name: str = 'dev0', text: str = '', timeout: float = 30, file: str = 'console.log') -> str:
    """Block until `text` appears in the console (timeout in seconds; errors on timeout)."""
    return api.run_text(_sargs('wait-text', text, '--timeout', timeout, '--file', file), name=name)

@server.tool()
def emu_wait_refresh(name: str = 'dev0', count: int = 1, total: int | None = None, timeout: float = 30) -> str:
    """Wait for `count` more panel refreshes, or until refresh_count >= total."""
    args = _sargs('wait-refresh', '--count', count, '--timeout', timeout)
    if total is not None: args += _sargs('--total', total)
    return api.run_text(args, name=name)

@server.tool()
def emu_wait_quiet(name: str = 'dev0', seconds: float = 2, timeout: float = 60) -> str:
    """Wait until the panel has been idle (no refresh, BUSY low) for `seconds`."""
    return api.run_text(_sargs('wait-quiet', '--seconds', seconds, '--timeout', timeout), name=name)

@server.tool()
def emu_console_send(name: str = 'dev0', text: str = '', newline: bool = True) -> str:
    """Send text into the guest's USB Serial/JTAG console (for firmware debug consoles)."""
    args = ['console-send', text] + ([] if newline else ['--no-newline'])
    return api.run_text(args, name=name)

@server.tool()
def emu_flash_app(name: str = 'dev0', app: str | None = None, build_dir: str | None = None) -> str:
    """Swap the app (0x10000) and/or bootloader+partition table from a build dir inside the running
    instance's flash image, keeping NVS/otadata and the SD card, then relaunch with the same
    command line. The edit-to-pixels loop: build_firmware -> emu_flash_app -> emu_wait_text."""
    args = ['flash-app']
    if app: args += ['--app', app]
    if build_dir: args += ['--build', build_dir]
    return api.run_text(args, name=name)

# ---------------------------------------------------------------- input
@server.tool()
def emu_press(name: str = 'dev0', button: str = 'right', ms: int = 120, wait: float = 10, quiet: float = 1) -> str:
    """Press a button: left (GPIO0, up/prev), right (GPIO7, down/next), power (GPIO3). quiet waits for
    an idle panel first; wait reports the refresh that follows. A power press while the model is in
    deep sleep is extended to 1.5 s (CrossPoint verifies the hold after the wake)."""
    return api.run_text(_sargs('press', button, '--ms', ms, '--wait', wait, '--quiet', quiet), name=name)

@server.tool()
def emu_hold(name: str = 'dev0', button: str = 'power', ms: int = 700, wait: float = 10) -> str:
    """Hold a button for `ms` milliseconds (power >= 400 ms sleeps CrossPoint)."""
    return api.run_text(_sargs('hold', button, '--ms', ms, '--wait', wait), name=name)

@server.tool()
def emu_chord(name: str = 'dev0', buttons: list[str] = ['power', 'right'], ms: int = 300, wait: float = 10) -> str:
    """Press several buttons together (power+right = CrossPoint's screenshot chord -> /screenshots/*.bmp on the card)."""
    return api.run_text(_sargs('chord', *buttons, '--ms', ms, '--wait', wait), name=name)

@server.tool()
def emu_tap(name: str = 'dev0', x: int = 400, y: int = 240, ms: int = 120, wait: float = 10, quiet: float = 1) -> str:
    """Tap at landscape panel pixel (x, y); converted to the GT911's portrait frame."""
    return api.run_text(_sargs('tap', x, y, '--ms', ms, '--wait', wait, '--quiet', quiet), name=name)

@server.tool()
def emu_swipe(name: str = 'dev0', x1: int = 600, y1: int = 240, x2: int = 200, y2: int = 240, ms: int = 250) -> str:
    """Swipe from (x1,y1) to (x2,y2) over `ms` milliseconds."""
    return api.run_text(_sargs('swipe', x1, y1, x2, y2, '--ms', ms), name=name)

@server.tool()
def emu_home(name: str = 'dev0', ms: int = 120, wait: float = 10) -> str:
    """Press the capacitive Home pad (GT911 key)."""
    return api.run_text(_sargs('home', '--ms', ms, '--wait', wait), name=name)

@server.tool()
def emu_battery(name: str = 'dev0', soc: int | None = None, mv: int | None = None, charging: bool | None = None) -> dict:
    """Set/read the CW2017 gauge (percent, millivolts) and the charger STAT line (GPIO21)."""
    args = ['battery']
    if soc is not None: args += _sargs('--soc', soc)
    if mv is not None: args += _sargs('--mv', mv)
    if charging is not None: args += ['--charging', 'on' if charging else 'off']
    return api.run(args, name=name)

@server.tool()
def emu_light(name: str = 'dev0') -> dict:
    """Frontlight PWM: duty (permille) and frequency of the LEDC channels routed to GPIO8 (cool) and GPIO9 (warm)."""
    return api.run(['light', '-v'], name=name)

@server.tool()
def emu_qmp(name: str = 'dev0', execute: str = 'query-status', arguments: dict | None = None) -> dict | list | str:
    """Raw QMP command (e.g. human-monitor-command with {"command-line": "info registers -a"})."""
    msg = {'execute': execute}
    if arguments: msg['arguments'] = arguments
    r = api.run(['qmp', json.dumps(msg)], name=name)
    return r.get('return', r) if isinstance(r, dict) and 'error' not in r else r

# ---------------------------------------------------------------- record / replay / guest time
@server.tool()
def emu_record(name: str = 'dev0', file: str | None = None, stop: bool = False) -> dict:
    """Start (file=...) or stop (stop=True) journalling this instance's input commands (press,
    hold, chord, tap, swipe, home, battery, console-send, reset) at the guest time each was issued,
    so `emu_replay` can perform the same steps again onto a fresh boot."""
    if stop:
        return api.run(['record', '--stop'], name=name)
    if not file:
        raise RuntimeError('give file=... to start recording, or stop=True to stop')
    return api.run(['record', file], name=name)

@server.tool()
def emu_replay(name: str = 'dev0', file: str = '', no_wait: bool = False, timeout: float = 120) -> dict:
    """Replay a journal from emu_record onto this instance: waits for the guest clock to reach each
    step's recorded time (no_wait=True runs the steps back to back instead), then performs the same
    input again in-process, including its own quiet/wait behaviour. Best on a freshly booted,
    deterministic=True instance, so the replay lands on the same pixels."""
    args = _sargs('replay', file, '--timeout', timeout)
    if no_wait: args += ['--no-wait']
    return api.run(args, name=name)

@server.tool()
def emu_wait_guest_ms(name: str = 'dev0', ms: int = 1000, timeout: float = 60) -> dict:
    """Wait until the instance's guest virtual clock (state.uptime_us) has advanced by `ms`
    milliseconds from now — for time-driven UI (inactivity timeouts, toasts, battery polls), not
    host time."""
    return api.run(_sargs('wait-guest-ms', ms, '--timeout', timeout), name=name)

# ---------------------------------------------------------------- build
@server.tool()
def build_firmware(env: str = 'x4pro', timeout: float = 1800) -> str:
    """`pio run -e <env>` in firmware/ (CrossPoint). Returns the tail of the build log."""
    r = subprocess.run(['pio', 'run', '-e', env], cwd=os.path.join(ROOT, 'firmware'), capture_output=True, text=True, timeout=timeout)
    lines = (r.stdout + r.stderr).splitlines()
    keep = [l for l in lines if any(k in l for k in ('error', 'Error', 'SUCCESS', 'FAILED', 'RAM:', 'Flash:'))]
    return '\n'.join(keep[-20:] or lines[-20:]) + f'\n(exit {r.returncode})'

@server.tool()
def build_flash_image(out: str = 'images/flash.bin', build_dir: str = 'firmware/.pio/build/x4pro',
                      raw: str | None = None, base: str | None = None) -> str:
    """Assemble a 16 MB flash image: from a pioarduino build dir (bootloader@0, partitions@0x8000,
    boot_app0@0xE000, app@0x10000), or from a raw dump (raw=...), optionally on top of base=... (keeps NVS)."""
    out = out if os.path.isabs(out) else os.path.join(ROOT, out)
    args = [PY, os.path.join(ROOT, 'tools', 'mkflash.py'), out]
    if raw: args += ['--raw', raw]
    else: args += ['--build', build_dir if os.path.isabs(build_dir) else os.path.join(ROOT, build_dir)]
    if base: args += ['--base', base]
    r = subprocess.run(args, capture_output=True, text=True, cwd=ROOT, timeout=120)
    if r.returncode: raise RuntimeError(r.stdout + r.stderr)
    return r.stdout.strip()

@server.tool()
def build_sd_image(out: str = 'images/sd.img', src_dir: str | None = None, size: str = '256M') -> str:
    """Create an MBR + FAT32 SD image (power-of-two size) populated from a directory (mtools, no root)."""
    out = out if os.path.isabs(out) else os.path.join(ROOT, out)
    args = [PY, os.path.join(ROOT, 'tools', 'mksd.py'), out, '--size', size, '--force']
    if src_dir: args += ['--src', src_dir]
    r = subprocess.run(args, capture_output=True, text=True, cwd=ROOT, timeout=300)
    if r.returncode: raise RuntimeError(r.stdout + r.stderr)
    return r.stdout.strip()

# ---------------------------------------------------------------- device (read-only unless confirm)
def _port():
    c = sorted(glob.glob('/dev/cu.usbmodem*') + glob.glob('/dev/ttyACM*'))
    return c[0] if c else None

@server.tool()
def device_status() -> dict:
    """USB personality of the desk device (Serial/JTAG vs mass storage), serial port, mounted card."""
    usb = subprocess.run(['ioreg', '-p', 'IOUSB', '-l', '-w0'], capture_output=True, text=True).stdout
    names = [l.split('=')[-1].strip().strip('"') for l in usb.splitlines() if '"USB Product Name"' in l and 'Hub' not in l]
    card = [v for v in glob.glob('/Volumes/*') if os.path.isdir(os.path.join(v, 'XTData')) or os.path.isdir(os.path.join(v, 'screenshots'))]
    return {'usb_products': names, 'port': _port(), 'card_mounted_at': card,
            'note': 'no port + no product = deep sleep or adapter unseated; "CrossPoint_X4_Pro"/"XTEink X4 Pro" = mass-storage mode'}

@server.tool()
def device_console(seconds: float = 30, reset: bool = True, out: str | None = None) -> str:
    """Capture the device console for `seconds` (reset=True toggles RTS first so the whole boot is
    seen; the port is reopened when it drops). Returns the capture (timestamps in host seconds)."""
    if not _port():
        return 'no serial port: device asleep, in mass-storage mode, or adapter unseated'
    out = out or os.path.join(ROOT, 'docs', 'device', f'console-{int(time.time())}.log')
    args = [PY, DEVICE, 'console', '--seconds', str(seconds), '--out', out, '--quiet']
    if reset: args.append('--reset')
    subprocess.run(args, capture_output=True, text=True, timeout=seconds + 30)
    return open(out, errors='replace').read() if os.path.exists(out) else '(nothing captured)'

@server.tool()
def device_flash_crosspoint(firmware: str | None = None, preserve_stock: bool = False, confirm: bool = False) -> str:
    """Write a CrossPoint firmware.bin into an app slot over the stock bootloader/table, after
    re-reading the device against the verified backup. Without confirm=True only the plan is shown."""
    args = [PY, DEVICE, 'flash-crosspoint']
    if firmware: args += ['--firmware', firmware]
    if preserve_stock: args.append('--preserve-stock')
    if confirm: args.append('--yes')
    r = subprocess.run(args, capture_output=True, text=True, cwd=ROOT, timeout=900)
    return (r.stdout + r.stderr).strip()

@server.tool()
def device_restore_stock(confirm: bool = False) -> str:
    """Write the stock app image from the verified backup back into its app slot. Plan only unless
    confirm=True. Since the owner's OTA update the bootloader starts app1, and `tools/device.py` reads
    otadata: with no --slot both writers refuse and name the slot that would actually boot
    (`device.py slots` reports it). Nothing here writes otadata."""
    args = [PY, DEVICE, 'restore-stock'] + (['--yes'] if confirm else [])
    r = subprocess.run(args, capture_output=True, text=True, cwd=ROOT, timeout=900)
    return (r.stdout + r.stderr).strip()

@server.tool()
def device_fetch_screenshots() -> list:
    """Copy CrossPoint screenshots from the mounted card (File Transfer mode) into
    docs/device/screenshots/ and return the newest one inline, un-rotated to landscape."""
    from PIL import Image as PILImage
    cards = [v for v in glob.glob('/Volumes/*') if os.path.isdir(os.path.join(v, 'screenshots'))]
    if not cards:
        return ['card not mounted (enter File Transfer on the device)']
    dst = os.path.join(ROOT, 'docs', 'device', 'screenshots'); os.makedirs(dst, exist_ok=True)
    files = sorted(glob.glob(os.path.join(cards[0], 'screenshots', '*.bmp')), key=os.path.getmtime)
    for f in files:
        subprocess.run(['cp', '-n', f, dst])
    if not files:
        return ['no screenshots on the card']
    im = PILImage.open(files[-1]).convert('L')
    if im.size == (480, 800): im = im.rotate(90, expand=True)
    png = os.path.join(dst, os.path.basename(files[-1]).replace('.bmp', '-landscape.png')); im.save(png)
    return [f'{len(files)} screenshot(s) copied to {dst}; newest: {files[-1]}', Image(path=png)]

if __name__ == '__main__':
    server.run(transport='stdio')
