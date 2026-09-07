# `x4emu` — the emulator CLI

One command drives the whole Xteink X4 Pro emulator: boot a firmware image, press buttons, tap the
screen, read the console, dump the e-paper panel to PNG, ask the board for its state. Every command
is non-blocking past its timeout and needs no terminal, so it works the same from a shell, a test
and an agent. With `--json` every command prints one JSON object and nothing else.

- The CLI lives in the `x4emu` package at the repository root (`x4emu/cli.py` builds the parser,
  `x4emu/commands.py` implements the commands, `x4emu/qmp.py` talks QMP, `x4emu/paths.py` finds the
  checkout, `x4emu/output.py` decides human text vs JSON).
- `tools/x4emu` is a thin shim that imports the package straight from the checkout (and pins
  `X4EMU_ROOT` to it), so nothing has to be installed to use the repository exactly as before.
- The MCP server (`tools/x4emu_mcp.py`) and `tests/` call `tools/x4emu` and parse its human output;
  that output is a contract and does not change.

## Install

```
pipx install /path/to/x4pro-emu          # or: .venv/bin/pip install -e .
x4emu --help
```

Both give an `x4emu` console script. Without an install, use the shim:

```
.venv/bin/python tools/x4emu --help
```

The only dependency is Pillow (used by `screenshot --diff`). Python ≥ 3.9.

## Finding the repository

The CLI is a front end for things that live in the checkout: `qemu/build/qemu-system-xtensa`, the
efuse replay built from `docs/device/efuse-dump.txt` with `tools/mkefuse.py`, and the instance
directories under `.x4emu/`. Installed outside the checkout, it resolves that root in this order:

1. `$X4EMU_ROOT`, if set (must be a checkout, else the command fails saying so);
2. the first directory at or above the current working directory that holds `tools/x4emu` **and**
   `qemu-patches/`;
3. the checkout containing the installed package (an editable install, or the `tools/x4emu` shim,
   which pins `X4EMU_ROOT` to its own checkout);

otherwise the command exits 1 with `cannot find the x4pro-emu checkout: set X4EMU_ROOT=…`.
Resolution is lazy: `x4emu --help` works anywhere.

| Environment variable | Effect |
|---|---|
| `X4EMU_ROOT` | the checkout to drive (see above) |
| `X4EMU_QEMU` | the emulator binary, instead of `$X4EMU_ROOT/qemu/build/qemu-system-xtensa` |
| `X4EMU_NAME` | default instance name, instead of `dev0` |

## The instance model

Every instance is a directory `$X4EMU_ROOT/.x4emu/<name>/`, named with `--name` (default `dev0`,
`$X4EMU_NAME`). Instance names become UNIX socket paths: keep them short and free of `=`.
Instances are independent; several can run at once.

| File | What it is |
|---|---|
| `qmp.sock` | QMP socket — its existence is what "instance exists" means |
| `console.sock` | the guest's USB Serial/JTAG console (RX side of `console-send`) |
| `console.log` | everything the guest printed on USB Serial/JTAG (what `log` and `wait-text` read) |
| `uart0.log` | UART0 output |
| `qemu.log` | QEMU's own log (`-d guest_errors,unimp`, trace events) |
| `stderr.log` | QEMU's stderr — read this when `run` fails |
| `pid` | the QEMU process id (`status` checks it with signal 0) |
| `run.json` | the exact command line, so `flash-app` can relaunch identically |
| `efuse.bin` | the efuse replay, generated on first `run` unless `--efuse` is given |
| `last.png` | the most recent `screenshot` |

A five-command session:

```
.venv/bin/python tools/mkflash.py images/flash.bin --build firmware/.pio/build/x4pro
.venv/bin/python tools/mksd.py images/sd.img --size 256M --src path/to/books
x4emu run --flash images/flash.bin --sd images/sd.img --fast-epd
x4emu wait-text "Entering activity: Home" --timeout 60
x4emu screenshot home.png
```

## `--json`

`--json` goes **before** the subcommand (`x4emu --json --name dev0 status`). In that mode:

- stdout carries exactly one JSON object and nothing else; the human lines are not printed;
- failures print `{"error": "..."}` — merged with whatever the command had already established, so
  a `wait-text` timeout is `{"found": false, "seconds": 30.0, "text": "…", "error": "timeout: …"}` —
  and keep the exit code they have without `--json` (1);
- exit codes are unchanged, including `screenshot --diff`, which exits 1 when pixels differ while
  still printing its normal object (that is a result, not an error);
- argparse's own usage errors are unchanged: they go to stderr with exit 2, leaving stdout empty.

Without `--json`, output is byte-for-byte what it always was.

## Commands

`--name NAME` and `--json` are global (before the subcommand). Times are seconds unless the flag
says `ms`. Panel coordinates are the landscape 800x480 screenshot; CrossPoint draws its portrait UI
rotated on it.

### Lifecycle

| Command | Arguments | `--json` object |
|---|---|---|
| `run` | `--flash F` (16 MB, from `tools/mkflash.py`), `--sd IMG` (power-of-two size), `--efuse F`, `--panel ssd1677\|uc8179\|uc8279`, `--fast-epd` (2 ms refreshes instead of the device's 40/1326/483 ms), `--gdb` (start halted on `:1234`), `--trace-epd F`, `--trace-i2c F`, `--icount N`, `--no-usb-host`, `--machine M`, `--debug D`, `--dry-run`, plus any extra QEMU arguments | `{"name", "pid", "status", "console"}`; with `--dry-run` `{"name", "dry_run": true, "cmd": [...]}` |
| `stop` | — | `{"name", "pid", "stopped", "running": false}` (`stopped: false` when it was not running) |
| `reset` | — | `{"name", "reset": true}` |
| `status` | — | `{"name", "pid", "status", "running", "deep_sleep"}`; not running: `{"name", "pid": null, "status": "not running", "running": false, "deep_sleep": false}` |
| `state` | — | the board's whole state object (uptime, panel, `refresh_count`, `last_mode`, `busy`, GPIO, buttons, touch, `gt911`, battery, `rtc`, `ledc`, `sleep`, `ana_i2c`, `saradc`, `rf`, `iolog_hot`, …) — the same JSON the human mode pretty-prints |
| `flash-app` | `--app APP.bin`, `--build DIR` (bootloader + partition table + app) | `{"flash", "parts": [{"offset", "size", "path"}], "name", "pid", "status", "console"}` — the instance is stopped, patched in place (NVS/otadata/SD survive) and relaunched with its old command line |

### Console

| Command | Arguments | `--json` object |
|---|---|---|
| `log` | `--since N` (last N lines), `--follow` (until `--timeout S`, default 30), `--file console.log\|uart0.log\|qemu.log` | `{"lines": [...]}` (with `--follow`, the lines seen during the window are included) |
| `wait-text` | `TEXT`, `--timeout S` (default 30), `--file F` | `{"found": true, "seconds", "text"}`; on timeout `{"found": false, …, "error": …}` and exit 1 |
| `console-send` | `TEXT`, `--no-newline` | `{"name", "sent": bytes}` |

### Panel

| Command | Arguments | `--json` object |
|---|---|---|
| `screenshot` | `OUT.png`, `--diff OTHER` (PNG, or a 480x800 device BMP, which is un-rotated automatically) | `{"path"}`, and with `--diff` also `{"diff", "diff_percent", "diff_pixels"}`; exit 1 when any pixel differs |
| `wait-refresh` | `--count N` (more refreshes from now, default 1), `--total N` (until `refresh_count ≥ N`), `--timeout S` | `{"ok": true, "refresh", "seconds"}`; on timeout `{"ok": false, "refresh", "error"}` and exit 1 |
| `wait-quiet` | `--seconds S` (idle for S, default 2), `--timeout S` | `{"ok": true, "refresh", "seconds"}`; on timeout `{"ok": false, "refresh", "error"}` and exit 1 |

### Input

CrossPoint ignores input while it is painting: use `--quiet 2` (or `wait-quiet`) before an input in
scripts, and `--wait S` to have the command report the refresh the input triggered — the refresh is
`null` when `--wait` is not given, and a `--wait` that sees no refresh is an error (exit 1).
`tap` and `home` hold the touch for at least `--ms` **and** until the firmware has read the GT911
frame (up to 5 s), so a tap into a multi-second rendering pass is not lost; `read_after_s` is when
that read happened, or `null` if it never did. For buttons `read_after_s` is always `null`.

| Command | Arguments | `--json` object |
|---|---|---|
| `press` | `left\|right\|power`, `--ms 120`, `--wait S`, `--quiet S` | `{"button", "ms", "read_after_s": null, "refresh", "refresh_after_s"}`; a power press while the sleep model is asleep is extended to 1500 ms and adds `"woke_from_deep_sleep": true` |
| `hold` | same, `--ms 3000` | as `press` |
| `chord` | `BTN [BTN …]`, `--ms 300`, `--wait S`, `--quiet S` | `{"buttons": [...], "ms", "read_after_s": null, "refresh", "refresh_after_s"}` (power + right is CrossPoint's screenshot chord) |
| `tap` | `X Y`, `--ms 120`, `--wait S`, `--quiet S` | `{"x", "y", "gt911": [rx, ry], "ms", "read_after_s", "refresh", "refresh_after_s"}` |
| `home` | `--ms 250`, `--wait S`, `--quiet S` | `{"ms", "read_after_s", "refresh", "refresh_after_s"}` — the capacitive Home pad (the stock app ignores it) |
| `swipe` | `X1 Y1 X2 Y2`, `--ms 250` | `{"x1", "y1", "x2", "y2", "ms", "steps"}` |

### Peripherals and low level

| Command | Arguments | `--json` object |
|---|---|---|
| `battery` | `--soc N`, `--mv N`, `--charging on\|off` (all optional; without them it only reads) | `{"soc", "mv", "charging"}` |
| `light` | `-v` | `{"cool", "warm"}` — LEDC duty in permille of GPIO8/GPIO9; with `-v` also `{"channels": [...]}` |
| `mem` | `read ADDR LEN` | `{"addr", "bytes": [...]}` (`bytes` are integers; the human mode prints QEMU's `xp` line) |
| `qmp` | `'{"execute": …, "arguments": {…}}'` | `{"return": …}` — the raw QMP reply, wrapped so the output is always an object |
| `gdb` | `--elf ELF` (default `firmware/.pio/build/x4pro/firmware.elf`) | `{"gdb", "elf", "target": ":1234"}`, printed before the process is replaced by `xtensa-esp-elf-gdb` (run the instance with `--gdb`) |

## Recipes

```
# boot, wait for the home screen, tap "Browse Files", screenshot — machine readable
x4emu --json --name dev0 run --flash images/flash.bin --sd images/sd.img --fast-epd
x4emu --json --name dev0 wait-text "Entering activity: Home" --timeout 60
x4emu --json --name dev0 wait-quiet --seconds 2
x4emu --json --name dev0 tap 345 350 --quiet 2 --wait 20 | jq .refresh_after_s
x4emu --json --name dev0 screenshot /tmp/browse.png --diff tests/golden/device-home-screenshot-3824.bmp

# how bright is the frontlight, and what is the panel doing?
x4emu --json light | jq .warm
x4emu --json state | jq '{refresh: .refresh_count, busy, sleep: .sleep.sleeping}'

# edit-to-pixels loop
cd firmware && pio run -e x4pro && cd ..
x4emu --json flash-app --build firmware/.pio/build/x4pro
x4emu --json wait-text "Entering activity" --timeout 90
```

## Exit codes

`0` success. `1` any failure (`{"error": …}` in JSON mode, a message on stderr otherwise) and also
`screenshot --diff` when pixels differ. `2` argparse usage errors (stderr, stdout untouched).
