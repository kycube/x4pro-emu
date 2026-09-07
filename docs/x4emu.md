# `x4emu` — the emulator CLI

One command drives the whole Xteink X4 Pro emulator: boot a firmware image, press buttons, tap the
screen, read the console, dump the e-paper panel to PNG, ask the board for its state, record an
input script and replay it onto a fresh boot for the same pixels. Every command
is non-blocking past its timeout and needs no terminal, so it works the same from a shell, a test
and an agent. With `--json` every command prints one JSON object and nothing else.

- The CLI lives in the `x4emu` package at the repository root (`x4emu/cli.py` builds the parser,
  `x4emu/commands.py` implements the commands, `x4emu/qmp.py` talks QMP, `x4emu/paths.py` finds the
  checkout, `x4emu/output.py` decides human text vs JSON vs in-process, `x4emu/api.py` runs a
  command in-process for `shell` and the MCP server, `x4emu/shell.py` and `x4emu/watch.py` are the
  `shell`/`watch` subcommands — see "Developer experience" below).
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
| `record.json` | `{"file": …}` while `record` is journalling this instance's inputs — `run` removes it |

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
| `run` | `--flash F` (16 MB, from `tools/mkflash.py`), `--sd IMG` (power-of-two size), `--efuse F`, `--panel ssd1677\|uc8179\|uc8279`, `--fast-epd` (2 ms refreshes instead of the device's 40/1326/483 ms), `--gdb` (start halted on `:1234`), `--boot-hold-power [MS]` (hold the power button from reset, see below), `--deterministic` (guest time follows the instruction count and the RTC starts from a fixed epoch, see below), `--trace-epd F`, `--trace-i2c F`, `--icount N`, `--no-usb-host`, `--machine M`, `--debug D`, `--dry-run`, plus any extra QEMU arguments | `{"name", "pid", "status", "console", "boot_hold_power_ms"}` (`null` without the flag); with `--dry-run` `{"name", "dry_run": true, "cmd": [...], "boot_hold_power_ms"}` |
| `stop` | — | `{"name", "pid", "stopped", "running": false}` (`stopped: false` when it was not running) |
| `reset` | — | `{"name", "reset": true}` |
| `status` | — | `{"name", "pid", "status", "running", "deep_sleep"}`; not running: `{"name", "pid": null, "status": "not running", "running": false, "deep_sleep": false}` |
| `state` | — | the board's whole state object (uptime, panel, `refresh_count`, `last_mode`, `busy`, GPIO, buttons, touch, `gt911`, battery, `rtc`, `ledc`, `sleep`, `ana_i2c`, `saradc`, `rf`, `iolog_hot`, …) — the same JSON the human mode pretty-prints |
| `flash-app` | `--app APP.bin`, `--build DIR` (bootloader + partition table + app) | `{"flash", "parts": [{"offset", "size", "path"}], "name", "pid", "status", "console"}` — the instance is stopped, patched in place (NVS/otadata/SD survive) and relaunched with its old command line (a `--boot-hold-power` run holds the button again) |

#### `run --boot-hold-power [MS]` — a cold boot the stock firmware accepts

The stock `xteink_app` runs a **boot-preflight** about 550 ms after every reset: it samples the power
button (GPIO3, active-low) and boots only if the button is still held when its window closes. On a
plain power-on it prints

```
boot-preflight: source=1 validation=2 gate=0 configured=1000ms effective=600ms hold=0ms decision=2 reason=3
```

and deep-sleeps; the way in used to be `x4emu press power` (a 1.5 s hold), which wakes it into a
second boot. With `--boot-hold-power` the button is held from the first instruction instead: QEMU is
started halted (`-S`), `btn-power` is set over QMP, the CPUs are released (`cont`), and the button is
released MS milliseconds later. The preflight then measures its full window and accepts the cold
boot — `hold=600ms decision=0 reason=11` — so there is no deep sleep, no wake and no second boot
(about 1–2 s and one `press power` saved per script; `tests/test_stock.py` boots this way).

`MS` is optional and defaults to **3000**: it is wall-clock time, and early boot (ROM, flash) runs at
roughly half real time here, so the guest reaches the end of the 600 ms window after ~1.8 s of wall
time — 3000 leaves margin without costing anything, since the guest boots on while `run` waits and
Home is painted about 5 s in. On a slower or heavily loaded host, raise it: the preflight's own
`hold=…ms decision=…` line in `console.log` says exactly what the guest measured (`decision=2
reason=3` means the hold was too short). The flag cannot be combined with `--gdb`, which owns the
halted start. `flash-app` repeats the hold when it relaunches such an instance.

One caveat: the stock's **WiFi start does not survive a cold boot** in the emulator today. After a
deep-sleep wake ESP-IDF reuses the PHY calibration kept in RTC memory; after a POWERON it runs the
full calibration, and that blocks here after three `phy: error: pll_cal exceeds 2ms!!!` lines (the
`radio_wifi` task stops — `state.ana_i2c`, `state.rf` and `state.saradc` stop counting at the
analog-master transaction `m1 0x6b/0x02=0x4e` — while the rest of the system keeps running). With
`user_config/net_en = 1` and the radio wanted, boot through the deep sleep and `press power`
(`tests/test_stock.py::test_stock_wifi_fails_fast` does); everything else is happier cold.

#### `run --deterministic` — guest time that does not depend on the host

`--deterministic` is `-icount 3` (unless `--icount N` is given explicitly, which then wins): the
guest's virtual clock advances with the *instruction count*, not with the host's clock, so the same
instruction stream reaches the same point at the same guest millisecond on every run and on every
machine. That is what makes a recorded input script replayable — `x4emu replay` waits for a guest
time, and a `--deterministic` guest arrives there having executed the same code — and it also makes
the emulator's speed independent of how loaded the host is (`-icount 3` = 8 ns per instruction;
CrossPoint boots to Home at guest time ≈ 1.4 s, a few host seconds).

Two limits, both deliberate:

- **The RTC is pinned, not real.** `--deterministic` also passes `-global
  driver=x4pro.pcf8563,property=base-epoch,value=1767225600` (2026-01-01T00:00:00Z; the constant
  `DETERMINISTIC_RTC_EPOCH` in `x4emu/commands.py`), so the BM8563 model starts from that second on
  every run instead of the host's wall clock and a firmware that *draws* a clock paints the same image
  run to run. Without the flag the RTC follows host time (`base-epoch` 0).
- **Guest timestamps are not reproduced exactly**, only the pixels. `x4emu`'s waits are host-paced
  polls (every 50 ms), so a replayed input lands a few milliseconds after the recorded guest time.
  Record inputs at moments when the firmware is idle (`--quiet 2`, `wait-quiet`) and that slack
  changes nothing; record them mid-repaint and no amount of determinism will help.

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

### Guest time, record and replay

`wait-guest-ms` waits on the *guest's* virtual clock (`state.uptime_us`), not on the host's, which
is what time-driven UI needs: an inactivity timeout, a toast, the next battery poll. It polls every
50 ms and never blocks past `--timeout` host seconds (a timeout is exit 1, like the other `wait-*`).

`record` journals the instance's **input** commands so `replay` can perform them again at the same
guest times. Only inputs are recorded — `press`, `hold`, `chord`, `tap`, `swipe`, `home`, `battery`,
`console-send`, `reset`; `state`, `screenshot`, `wait-*` and `log` are not, since a replay repeats
what was *done* to the firmware, not what was looked at. Recording is invisible in human mode (the
input commands print exactly what they always printed; `--json` gains `"recorded": true`).

| Command | Arguments | `--json` object |
|---|---|---|
| `wait-guest-ms` | `MS`, `--timeout S` (default 60) | `{"ok": true, "guest_ms", "advanced_ms", "target_ms", "seconds"}`; on timeout the same with `"ok": false` plus `"error"`, exit 1 |
| `wait-guest-until` | `MS` (absolute guest time), `--timeout S` | as `wait-guest-ms` (this is the form `replay` uses internally) |
| `record` | `FILE` (the journal; truncated, one `record` = one journal), `--stop` | start: `{"name", "recording": true, "file", "steps": 0}`; `--stop`: `{"name", "recording": false, "file", "steps": N}` (`file: null`, `steps: 0` when it was not recording) |
| `replay` | `FILE`, `--timeout S` (host seconds one step may wait for the guest clock, default 120), `--no-wait` (ignore the recorded times, run the steps back to back) | `{"file", "steps", "guest_ms_end", "seconds", "replayed": [{"t_ms", "cmd", "guest_ms"}]}`; on a failure the fields gathered so far plus `"error"`, exit 1 |

The journal is one JSON object per line (`.jsonl`): the guest time the command was issued at, its
name, and the arguments exactly as it received them, so the replay calls the same function with the
same arguments:

```json
{"t_ms": 12957, "cmd": "tap", "args": {"x": 345, "y": 350, "ms": 120, "wait": 30.0, "quiet": 2.0}}
{"t_ms": 16992, "cmd": "home", "args": {"ms": 250, "wait": 30.0, "quiet": 2.0}}
```

`t_ms` is `state.uptime_us / 1000` at the moment the command was issued — *before* its own
`--quiet` wait, so a replay that waits for `t_ms` and then runs the command reproduces both waits.
The `args` keys per command are the ones the CLI takes: `press`/`hold` `button ms wait quiet`,
`chord` `buttons ms wait quiet`, `tap` `x y ms wait quiet`, `swipe` `x1 y1 x2 y2 ms wait quiet`,
`home` `ms wait quiet`, `battery` `soc mv charging`, `console-send` `text newline`, `reset` none.
Journals are editable and hand-writable; `t_ms: null` is allowed and means "do not wait".

`replay` runs the steps **in-process**: it calls the very functions the CLI calls, it does not shell
out to itself, so each step behaves exactly as it did while recording — including its own `--quiet`
(wait for an idle panel first) and `--wait` (report the refresh the input triggered, fail if none).
It waits for `t_ms` with the same virtual-clock poll as `wait-guest-until`, prints one line per step
in human mode, and refuses to start when the instance's guest clock is already past the first step —
that instance is not where the recording began, and its screen would not match:

```
$ x4emu --name rpa replay flow.jsonl
t=12957 ms tap 345 350
t=16992 ms home
replayed 2 steps, guest 19129 ms in 19.8s
```

A worked example — record a flow once, then reproduce its screen on a fresh boot
(`tests/test_replay.py` is exactly this, three times over, asserting 0 differing pixels):

```
x4emu --name rec run --flash images/flash.bin --sd images/sd.img --fast-epd --deterministic
x4emu --name rec wait-text "Entering activity: Home" --timeout 180
x4emu --name rec wait-quiet --seconds 2
x4emu --name rec record /tmp/flow.jsonl
x4emu --name rec tap 345 350 --quiet 2 --wait 30      # "Browse Files"
x4emu --name rec wait-quiet --seconds 2
x4emu --name rec home --quiet 2 --wait 30             # back to Home
x4emu --name rec wait-quiet --seconds 2
x4emu --name rec record --stop                        # "rec: recording stopped, 2 steps in …"
x4emu --name rec screenshot /tmp/a.png

x4emu --name rp1 run --flash images/flash.bin --sd images/sd.img --fast-epd --deterministic
x4emu --name rp1 wait-text "Entering activity: Home" --timeout 180
x4emu --name rp1 replay /tmp/flow.jsonl
x4emu --name rp1 wait-quiet --seconds 2
x4emu --name rp1 screenshot /tmp/b.png --diff /tmp/a.png     # 0.000% of pixels differ (0), exit 0
```

Replay onto an instance that is already past the first step is an error (`--no-wait` runs the steps
back to back instead, which is the way to reuse a journal as a plain macro on a live instance).

### Peripherals and low level

| Command | Arguments | `--json` object |
|---|---|---|
| `battery` | `--soc N`, `--mv N`, `--charging on\|off` (all optional; without them it only reads) | `{"soc", "mv", "charging"}` |
| `light` | `-v` | `{"cool", "warm"}` — LEDC duty in permille of GPIO8/GPIO9; with `-v` also `{"channels": [...]}` |
| `mem` | `read ADDR LEN` | `{"addr", "bytes": [...]}` (`bytes` are integers; the human mode prints QEMU's `xp` line) |
| `qmp` | `'{"execute": …, "arguments": {…}}'` | `{"return": …}` — the raw QMP reply, wrapped so the output is always an object |
| `gdb` | `--elf ELF` (default `firmware/.pio/build/x4pro/firmware.elf`) | `{"gdb", "elf", "target": ":1234"}`, printed before the process is replaced by `xtensa-esp-elf-gdb` (run the instance with `--gdb`) |

### Developer experience: `shell`, `watch`, the in-process API

`x4emu/api.py` runs one CLI command **in-process** — no subprocess, no printing, and it never raises
`SystemExit` — and is what `shell` below and `tools/x4emu_mcp.py` are built on:

- `api.run(argv, name=None) -> dict` builds the parser exactly as the CLI does, runs the command,
  and returns its `--json` object. `argv` is the subcommand and its own arguments (`['state']`,
  `['tap', '345', '350', '--quiet', '2']`); `name`, if given, is prefixed as `--name NAME` (or put
  `--name` in `argv` yourself, e.g. `api.run(['--name', 'dev0', 'status'])`). On success the return
  value is exactly the `--json` object documented above for that command. On failure — a bad
  argument, a command's own `sys.exit`, an unexpected exception — it returns that same object
  merged with `"error"` (as `--json` does) plus `"rc"`, the exit code the real CLI would have used;
  it never prints anything and never raises.
- `api.run_text(argv, name=None) -> str` is the human-mode equivalent: it returns the text the
  command would have printed to stdout. On failure it raises `RuntimeError` (never `SystemExit`)
  carrying that text plus the failure message — the same contract `tools/x4emu_mcp.py`'s old
  subprocess helper had, so callers built on it did not need to change their error handling.

Both are implemented via `output.Out`'s `collect` mode (`out.configure(json_mode, collect=True)`,
`out.collected()` / `out.text()`): the human lines and the final JSON object are buffered instead of
printed, then the API function restores `out`'s prior state before returning (the same
save-then-restore shape as `output.suspended()`, which `replay` uses for the same reason).

| Command | Arguments | Notes |
|---|---|---|
| `shell` | — | a REPL: one command per line from stdin (`shlex.split`), run in-process through the parser, printing exactly what `x4emu <line>` would (or, when the shell itself was started with `--json`, one JSON object per line via `api.run`). `exit`, `quit` or EOF end the session; `name NEW` switches the instance later lines target; a failing line (a bad argument, a command's own failure) is reported — to stderr in human mode, as `{"error": ..., "rc": ...}` in `--json` mode — and the loop continues. The prompt `x4emu NAME> ` is printed only when stdin is a TTY, so `printf 'status\nstate\n' \| x4emu --name X shell` works non-interactively and prints nothing but each line's own output. |
| `watch` | `--port P` (default 8420, `0` = any free port), `--seconds S` (default: run until Ctrl-C), `--out FILE` | a small HTTP server on `127.0.0.1:P`: `/` is a live page showing the panel and refreshing only when `refresh_count` changes (polls `/state.json` every 300 ms); `/panel.png` is a fresh screenshot (`commands.screenshot_to`, the same helper `screenshot` uses) and `/state.json` is `state`'s object — each request opens its own short QMP connection and closes it before responding, so other `x4emu` commands keep working the whole time `watch` is up. `--out FILE` also writes the PNG to `FILE` on every `/panel.png` request. The URL is printed first (`http://127.0.0.1:P/`, and in `--json` mode `{"url", "port"}`) before `watch` blocks serving requests. |

`shell` recipe (piped, non-interactive):

```
$ printf 'status\nstate\nscreenshot /tmp/a.png\nexit\n' | x4emu --name dev0 shell
dev0: pid 1234, running, running=True
{
 "uptime_us": 12345678,
 ...
}
/tmp/a.png
```

`watch` recipe:

```
$ x4emu --name dev0 watch --port 0 &
http://127.0.0.1:54321/
$ open http://127.0.0.1:54321/          # live panel + state in a browser
$ x4emu --name dev0 tap 345 350 --quiet 2   # still works: watch only holds the QMP socket briefly
```

## Recipes

```
# boot, wait for the home screen, tap "Browse Files", screenshot — machine readable
x4emu --json --name dev0 run --flash images/flash.bin --sd images/sd.img --fast-epd
x4emu --json --name dev0 wait-text "Entering activity: Home" --timeout 60
x4emu --json --name dev0 wait-quiet --seconds 2
x4emu --json --name dev0 tap 345 350 --quiet 2 --wait 20 | jq .refresh_after_s
x4emu --json --name dev0 screenshot /tmp/browse.png --diff tests/golden/device-home-screenshot-3824.bmp

# the stock firmware, cold boot: no deep-sleep/wake detour, Home is refresh 2 plus an idle panel
x4emu --name stock run --flash images/stock.bin --sd images/sd-device.img --boot-hold-power
x4emu --name stock wait-text "main_task: Returned from app_main()" --timeout 60
x4emu --name stock wait-refresh --total 2 --timeout 90    # 1 = panel init, 2 = Home; 3 is the clock
x4emu --name stock wait-quiet --seconds 2
x4emu --name stock screenshot /tmp/stock-home.png

# a deterministic run, an input script recorded and replayed onto a fresh boot
x4emu --json --name rec run --flash images/flash.bin --sd images/sd.img --fast-epd --deterministic
x4emu --json --name rec record /tmp/flow.jsonl
x4emu --json --name rec tap 345 350 --quiet 2 --wait 30 | jq .recorded      # true
x4emu --json --name rec record --stop | jq .steps
x4emu --json --name rp1 run --flash images/flash.bin --sd images/sd.img --fast-epd --deterministic
x4emu --json --name rp1 wait-text "Entering activity: Home" --timeout 180
x4emu --json --name rp1 replay /tmp/flow.jsonl | jq '{steps, guest_ms_end}'

# wait on the guest's clock, not the host's (inactivity timeouts, toasts, battery polls)
x4emu --json wait-guest-ms 30000 --timeout 120 | jq .guest_ms

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
