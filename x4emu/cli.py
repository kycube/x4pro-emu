#!/usr/bin/env python3
"""x4emu: drive the Xteink X4 Pro emulator from a shell.

  x4emu run --flash flash.bin [--sd sd.img] [--efuse efuse.bin] [--panel ssd1677|uc8179|uc8279]
            [--fast-epd] [--gdb] [--trace-epd FILE] [--trace-i2c FILE] [--name dev0] [--no-usb-host]
            [--boot-hold-power [MS]]
  x4emu stop | reset | status | state | log [--follow] [--since N] | wait-text TEXT [--timeout S]
  x4emu screenshot out.png [--diff other.png] | wait-refresh [--count N] [--timeout S]
  x4emu press left|right|power [--ms 120] | hold power --ms 3000
  x4emu tap X Y | swipe X1 Y1 X2 Y2 [--ms 250] | home [--ms 250] [--quiet S]
  x4emu battery --soc 63 --mv 3910 [--charging on|off] | light
  x4emu mem read ADDR LEN | gdb | qmp '{"execute":...}'

`--json` before the subcommand prints one JSON object per command instead of the human lines
(errors become {"error": "..."} with the same exit code). See docs/x4emu.md.

Every instance lives in .x4emu/<name>/ (qmp.sock, console.log, uart0.log, qemu.log, pid, run.json).
Nothing here blocks without a timeout; nothing needs a terminal.
"""
import argparse
import sys

from . import commands as c
from .output import out
from .paths import default_name


def build_parser():
    ap = argparse.ArgumentParser(prog='x4emu', description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--name', default=default_name())
    ap.add_argument('--json', dest='json_out', action='store_true',
                    help='print one JSON object per command instead of human text')
    sp = ap.add_subparsers(dest='cmd', required=True)
    p = sp.add_parser('run'); p.add_argument('--flash', required=True); p.add_argument('--sd'); p.add_argument('--efuse')
    p.add_argument('--panel', choices=['ssd1677', 'uc8179', 'uc8279']); p.add_argument('--fast-epd', action='store_true')
    p.add_argument('--gdb', action='store_true'); p.add_argument('--trace-epd'); p.add_argument('--trace-i2c')
    p.add_argument('--no-usb-host', action='store_true'); p.add_argument('--icount')
    p.add_argument('--boot-hold-power', nargs='?', type=int, const=c.BOOT_HOLD_POWER_MS, default=None, metavar='MS',
                   help='start halted, hold the power button from reset for MS ms (default '
                        f'{c.BOOT_HOLD_POWER_MS}), then release: the stock firmware boots cold '
                        'instead of deep-sleeping in its boot-preflight')
    p.add_argument('--machine', default='xteink-x4pro'); p.add_argument('--debug', default='guest_errors,unimp')
    p.add_argument('--dry-run', action='store_true'); p.add_argument('extra', nargs='*'); p.set_defaults(fn=c.cmd_run)
    sp.add_parser('stop').set_defaults(fn=c.cmd_stop)
    sp.add_parser('reset').set_defaults(fn=c.cmd_reset)
    sp.add_parser('status').set_defaults(fn=c.cmd_status)
    sp.add_parser('state').set_defaults(fn=c.cmd_state)
    p = sp.add_parser('log'); p.add_argument('--follow', action='store_true'); p.add_argument('--since', type=int)
    p.add_argument('--timeout', type=float, default=30); p.add_argument('--file', default='console.log'); p.set_defaults(fn=c.cmd_log)
    p = sp.add_parser('console-send'); p.add_argument('text'); p.add_argument('--no-newline', dest='newline', action='store_false'); p.set_defaults(fn=c.cmd_console_send)
    p = sp.add_parser('flash-app'); p.add_argument('--app'); p.add_argument('--build'); p.set_defaults(fn=c.cmd_flash_app)
    p = sp.add_parser('wait-text'); p.add_argument('text'); p.add_argument('--timeout', type=float, default=30)
    p.add_argument('--file', default='console.log'); p.set_defaults(fn=c.cmd_wait_text)
    p = sp.add_parser('screenshot'); p.add_argument('out'); p.add_argument('--diff'); p.set_defaults(fn=c.cmd_screenshot)
    p = sp.add_parser('wait-refresh'); p.add_argument('--count', type=int, default=1); p.add_argument('--total', type=int); p.add_argument('--timeout', type=float, default=30); p.set_defaults(fn=c.cmd_wait_refresh)
    for nm, dflt in (('press', 120), ('hold', 3000)):
        p = sp.add_parser(nm); p.add_argument('button', choices=['left', 'right', 'power']); p.add_argument('--ms', type=int, default=dflt)
        p.add_argument('--wait', type=float, default=0, help='wait up to S seconds for a refresh to start after the input')
        p.add_argument('--quiet', type=float, default=0, help='first wait until the panel has been idle for S seconds'); p.set_defaults(fn=c.cmd_press)
    p = sp.add_parser('chord'); p.add_argument('buttons', nargs='+', choices=['left', 'right', 'power']); p.add_argument('--ms', type=int, default=300)
    p.add_argument('--wait', type=float, default=0); p.add_argument('--quiet', type=float, default=0); p.set_defaults(fn=c.cmd_chord)
    p = sp.add_parser('tap'); p.add_argument('x', type=int); p.add_argument('y', type=int); p.add_argument('--ms', type=int, default=120)
    p.add_argument('--wait', type=float, default=0); p.add_argument('--quiet', type=float, default=0); p.set_defaults(fn=c.cmd_tap)
    p = sp.add_parser('wait-quiet'); p.add_argument('--seconds', type=float, default=2); p.add_argument('--timeout', type=float, default=60); p.set_defaults(fn=c.cmd_wait_quiet)
    p = sp.add_parser('swipe'); [p.add_argument(n, type=int) for n in ('x1', 'y1', 'x2', 'y2')]; p.add_argument('--ms', type=int, default=250)
    p.add_argument('--wait', type=float, default=0, help='wait up to S seconds for a refresh to start after the input'); p.add_argument('--quiet', type=float, default=0, help='first wait until the panel has been idle for S seconds'); p.set_defaults(fn=c.cmd_swipe)
    p = sp.add_parser('home'); p.add_argument('--ms', type=int, default=250); p.add_argument('--wait', type=float, default=0)
    p.add_argument('--quiet', type=float, default=0, help='first wait until the panel has been idle for S seconds'); p.set_defaults(fn=c.cmd_home)
    p = sp.add_parser('battery'); p.add_argument('--soc', type=int); p.add_argument('--mv', type=int); p.add_argument('--charging', choices=['on', 'off']); p.set_defaults(fn=c.cmd_battery)
    p = sp.add_parser('light'); p.add_argument('-v', '--verbose', action='store_true'); p.set_defaults(fn=c.cmd_light)
    p = sp.add_parser('mem'); p.add_argument('op', choices=['read']); p.add_argument('addr'); p.add_argument('len', type=int); p.set_defaults(fn=c.cmd_mem)
    p = sp.add_parser('gdb'); p.add_argument('--elf'); p.set_defaults(fn=c.cmd_gdb)
    p = sp.add_parser('qmp'); p.add_argument('json'); p.set_defaults(fn=c.cmd_qmp)
    return ap


def main(argv=None):
    a = build_parser().parse_args(argv)
    out.configure(a.json_out)
    try:
        rc = a.fn(a)
    except SystemExit as e:
        # sys.exit('message') is this CLI's way of failing: in --json mode it becomes
        # {"error": ...} on stdout (plus whatever the command had already gathered), exit 1.
        if out.json_mode and isinstance(e.code, str):
            out.error(e.code)
            raise SystemExit(1) from None
        raise
    except Exception as e:                       # QMP errors, missing files, PIL, ...
        if out.json_mode:
            out.error(f'{type(e).__name__}: {e}')
            raise SystemExit(1) from None
        raise
    out.finish()
    sys.exit(rc or 0)


if __name__ == '__main__':
    main()
