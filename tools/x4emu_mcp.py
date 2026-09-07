#!/usr/bin/env python3
"""MCP server for the Xteink X4 Pro emulator and the desk device.

Exposes the x4emu CLI, the device toolbox and the build steps as typed tools, with screenshots
returned inline as images. Register (project scope) with the `.mcp.json` at the repo root, or:

  claude mcp add x4emu -- /Users/mini/x4pro-emu/.venv/bin/python /Users/mini/x4pro-emu/tools/x4emu_mcp.py

Every emulator tool takes `name` (instance, default dev0); instances live in .x4emu/<name>/.
Device tools never write flash unless `confirm=True`, and the underlying scripts still re-read
the device and refuse without the verified backup (see CLAUDE.md, device rules).
"""
import glob, json, os, subprocess, sys, time
from mcp.server.mcpserver import MCPServer, Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
X4EMU = os.path.join(ROOT, 'tools', 'x4emu')
DEVICE = os.path.join(ROOT, 'tools', 'device.py')

server = MCPServer('x4emu', instructions=(
    'Xteink X4 Pro emulator (QEMU, ESP32-S3) and desk-device tools. Typical loop: build_flash_image -> '
    'emu_run -> emu_wait_text("Entering activity: Home") -> emu_wait_quiet -> emu_tap/emu_press -> '
    'emu_screenshot. Inputs are dropped while the firmware paints: use quiet>0 or emu_wait_quiet first. '
    'Panel coordinates are the 800x480 landscape screenshot; CrossPoint draws its portrait UI rotated on it.'))

def _x4(name, *args, timeout=180):
    r = subprocess.run([PY, X4EMU, '--name', name, *map(str, args)], capture_output=True, text=True, timeout=timeout)
    out = (r.stdout + ('\n' + r.stderr if r.stderr.strip() else '')).strip()
    if r.returncode:
        raise RuntimeError(f'x4emu {" ".join(map(str, args))} failed ({r.returncode}): {out}')
    return out

def _idir(name):
    return os.path.join(ROOT, '.x4emu', name)

# ---------------------------------------------------------------- emulator lifecycle
@server.tool()
def emu_run(flash: str, name: str = 'dev0', sd: str | None = None, fast_epd: bool = True,
            panel: str | None = None, trace_epd: bool = False, trace_i2c: bool = False,
            gdb: bool = False, icount: int | None = None, efuse: str | None = None) -> str:
    """Start an emulator instance from a 16 MB flash image (see build_flash_image). fast_epd makes
    panel refreshes take 2 ms instead of the device's 40/1326/483 ms. panel: ssd1677|uc8179|uc8279
    (default uc8279, the desk unit). Traces land in .x4emu/<name>/{epd,i2c}.jsonl."""
    args = ['run', '--flash', flash]
    if sd: args += ['--sd', sd]
    if fast_epd: args += ['--fast-epd']
    if panel: args += ['--panel', panel]
    if trace_epd: args += ['--trace-epd', os.path.join(_idir(name), 'epd.jsonl')]
    if trace_i2c: args += ['--trace-i2c', os.path.join(_idir(name), 'i2c.jsonl')]
    if gdb: args += ['--gdb']
    if icount is not None: args += ['--icount', icount]
    if efuse: args += ['--efuse', efuse]
    return _x4(name, *args)

@server.tool()
def emu_stop(name: str = 'dev0') -> str:
    """Stop an instance (QMP quit, SIGKILL fallback)."""
    return _x4(name, 'stop')

@server.tool()
def emu_reset(name: str = 'dev0') -> str:
    """System reset (like a hardware reset; panel RAM and image survive)."""
    return _x4(name, 'reset')

@server.tool()
def emu_status(name: str = 'dev0') -> str:
    """pid, QEMU run state, and whether the sleep model has paused it (deep sleep)."""
    return _x4(name, 'status')

@server.tool()
def emu_state(name: str = 'dev0') -> dict:
    """Board state JSON: panel/refresh counters, BUSY, GPIO levels, buttons, touch, GT911, I2C,
    battery, RTC, LEDC channels (frontlight duty), sleep state, console/SPI counters, analog-master
    (ana_i2c) / SENS (saradc) / radio-stub (rf, with `hot` polls) counters, iolog_hot (unmodelled
    registers polled past the log cap)."""
    return json.loads(_x4(name, 'state'))

# ---------------------------------------------------------------- observation
@server.tool()
def emu_screenshot(name: str = 'dev0', save_path: str | None = None, diff_path: str | None = None) -> list:
    """Screenshot of the e-paper panel (800x480 PNG) returned inline. diff_path: another PNG or a
    device BMP (480x800, un-rotated automatically); the text part reports the percentage of pixels
    that differ."""
    out = save_path or os.path.join(_idir(name), f'shot-{int(time.time())}.png')
    args = ['screenshot', out]
    if diff_path: args += ['--diff', diff_path]
    r = subprocess.run([PY, X4EMU, '--name', name, *args], capture_output=True, text=True, timeout=60)
    text = r.stdout.strip()
    if not os.path.exists(out):
        raise RuntimeError(f'screenshot failed: {text} {r.stderr}')
    return [f'{out}\n{text}', Image(data=open(out, 'rb').read(), format='png')]

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
    return _x4(name, 'wait-text', text, '--timeout', timeout, '--file', file, timeout=timeout + 10)

@server.tool()
def emu_wait_refresh(name: str = 'dev0', count: int = 1, total: int | None = None, timeout: float = 30) -> str:
    """Wait for `count` more panel refreshes, or until refresh_count >= total."""
    args = ['wait-refresh', '--count', count, '--timeout', timeout]
    if total is not None: args += ['--total', total]
    return _x4(name, *args, timeout=timeout + 10)

@server.tool()
def emu_wait_quiet(name: str = 'dev0', seconds: float = 2, timeout: float = 60) -> str:
    """Wait until the panel has been idle (no refresh, BUSY low) for `seconds`."""
    return _x4(name, 'wait-quiet', '--seconds', seconds, '--timeout', timeout, timeout=timeout + 10)

@server.tool()
def emu_console_send(name: str = 'dev0', text: str = '', newline: bool = True) -> str:
    """Send text into the guest's USB Serial/JTAG console (for firmware debug consoles)."""
    args = ['console-send', text] + ([] if newline else ['--no-newline'])
    return _x4(name, *args)

@server.tool()
def emu_flash_app(name: str = 'dev0', app: str | None = None, build_dir: str | None = None) -> str:
    """Swap the app (0x10000) and/or bootloader+partition table from a build dir inside the running
    instance's flash image, keeping NVS/otadata and the SD card, then relaunch with the same
    command line. The edit-to-pixels loop: build_firmware -> emu_flash_app -> emu_wait_text."""
    args = ['flash-app']
    if app: args += ['--app', app]
    if build_dir: args += ['--build', build_dir]
    return _x4(name, *args)

# ---------------------------------------------------------------- input
@server.tool()
def emu_press(name: str = 'dev0', button: str = 'right', ms: int = 120, wait: float = 10, quiet: float = 1) -> str:
    """Press a button: left (GPIO0, up/prev), right (GPIO7, down/next), power (GPIO3). quiet waits for
    an idle panel first; wait reports the refresh that follows. A power press while the model is in
    deep sleep is extended to 1.5 s (CrossPoint verifies the hold after the wake)."""
    return _x4(name, 'press', button, '--ms', ms, '--wait', wait, '--quiet', quiet, timeout=wait + quiet + 40)

@server.tool()
def emu_hold(name: str = 'dev0', button: str = 'power', ms: int = 700, wait: float = 10) -> str:
    """Hold a button for `ms` milliseconds (power >= 400 ms sleeps CrossPoint)."""
    return _x4(name, 'hold', button, '--ms', ms, '--wait', wait, timeout=wait + ms / 1000 + 40)

@server.tool()
def emu_chord(name: str = 'dev0', buttons: list[str] = ['power', 'right'], ms: int = 300, wait: float = 10) -> str:
    """Press several buttons together (power+right = CrossPoint's screenshot chord -> /screenshots/*.bmp on the card)."""
    return _x4(name, 'chord', *buttons, '--ms', ms, '--wait', wait, timeout=wait + 40)

@server.tool()
def emu_tap(name: str = 'dev0', x: int = 400, y: int = 240, ms: int = 120, wait: float = 10, quiet: float = 1) -> str:
    """Tap at landscape panel pixel (x, y); converted to the GT911's portrait frame."""
    return _x4(name, 'tap', x, y, '--ms', ms, '--wait', wait, '--quiet', quiet, timeout=wait + quiet + 40)

@server.tool()
def emu_swipe(name: str = 'dev0', x1: int = 600, y1: int = 240, x2: int = 200, y2: int = 240, ms: int = 250) -> str:
    """Swipe from (x1,y1) to (x2,y2) over `ms` milliseconds."""
    return _x4(name, 'swipe', x1, y1, x2, y2, '--ms', ms, timeout=60)

@server.tool()
def emu_home(name: str = 'dev0', ms: int = 120, wait: float = 10) -> str:
    """Press the capacitive Home pad (GT911 key)."""
    return _x4(name, 'home', '--ms', ms, '--wait', wait, timeout=wait + 40)

@server.tool()
def emu_battery(name: str = 'dev0', soc: int | None = None, mv: int | None = None, charging: bool | None = None) -> dict:
    """Set/read the CW2017 gauge (percent, millivolts) and the charger STAT line (GPIO21)."""
    args = ['battery']
    if soc is not None: args += ['--soc', soc]
    if mv is not None: args += ['--mv', mv]
    if charging is not None: args += ['--charging', 'on' if charging else 'off']
    return json.loads(_x4(name, *args))

@server.tool()
def emu_light(name: str = 'dev0') -> dict:
    """Frontlight PWM: duty (permille) and frequency of the LEDC channels routed to GPIO8 (cool) and GPIO9 (warm)."""
    return json.loads(_x4(name, 'light', '-v'))

@server.tool()
def emu_qmp(name: str = 'dev0', execute: str = 'query-status', arguments: dict | None = None) -> dict | list | str:
    """Raw QMP command (e.g. human-monitor-command with {"command-line": "info registers -a"})."""
    msg = {'execute': execute}
    if arguments: msg['arguments'] = arguments
    out = _x4(name, 'qmp', json.dumps(msg))
    try:
        return json.loads(out)
    except ValueError:
        return out

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
    """Write a CrossPoint firmware.bin into app0 (0x10000) over the stock bootloader/table, after
    re-reading the device against the verified backup. Without confirm=True only the plan is shown."""
    args = [PY, DEVICE, 'flash-crosspoint']
    if firmware: args += ['--firmware', firmware]
    if preserve_stock: args.append('--preserve-stock')
    if confirm: args.append('--yes')
    r = subprocess.run(args, capture_output=True, text=True, cwd=ROOT, timeout=900)
    return (r.stdout + r.stderr).strip()

@server.tool()
def device_restore_stock(confirm: bool = False) -> str:
    """Write the stock app image from the verified backup back into app0. Plan only unless confirm=True."""
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
