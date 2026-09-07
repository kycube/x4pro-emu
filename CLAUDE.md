# x4pro-emu — Xteink X4 Pro emulator (QEMU, ESP32-S3)

Linux/macOS-hosted emulator for unmodified Xteink X4 Pro firmware images, driven from a shell:
buttons, touch, console, panel screenshots, JSON state. CrossPoint 1.6.0 boots to its home screen,
opens books, pages, sleeps and wakes in it; the stock `xteink_app` 7.2.4 boots to its home screen,
paints, lights the frontlight, takes touch, and its WiFi start comes up and stops itself (no radio).
Progress and dead ends: `docs/log.md`. **Next agent: read `docs/NEXT_PHASE.md`**, §0 (resume) and §10 (the
work runs as squads: the coordinator forms agents per milestone, Opus 5 for well-specified tasks, Fable 5.1
for symbol-less firmware reading and new models).

## What is NOT emulated (how it shows up for a firmware developer)

- **USB mass storage / TinyUSB**: the "File Transfer" screen does nothing; the host never sees a disk.
- **WiFi / BLE**: no radio. The blocks a WiFi start touches are answered (analog-master I2C block, SENS
  temperature sensor and SAR oneshot, FE/RX/BB/MAC register stub with status bits that read as set), so
  ESP-IDF's driver comes up, finds no air and stops itself about 8 s after `wifi:mode : sta`; nothing
  enumerates on the network side. BLE is untested.
- **Grayscale**: the two-plane 4-level rendering is an approximation (`state.gray_approx` is true
  after such a refresh); anti-aliased text will not look exactly like the glass.
- **Deep sleep**: modelled as a paused VM followed by a system reset with reset cause 5 (DSLEEP),
  EXT1 wake status = the power button. RTC-domain retention beyond RTC RAM, power draw, and GPIO
  hold latches are not simulated; the guest's `millis()` keeps counting across the modelled reset.
- **Light sleep**: returns immediately (no time passes).
- **Timing**: BUSY durations are the device's measured values (UC8279: PON 40 ms, GC 1326 ms, DU
  483 ms) or 2 ms with `--fast-epd`; the RTC and PWM follow guest time; nothing is cycle-accurate.
  CPU-frequency changes by the firmware do not change emulation speed.
- **GPIO matrix / IO_MUX**: not modelled electrically. SPI2 always reaches the panel; pull settings
  are ignored (the GPIO model carries per-pin idle levels: buttons, MOSI, RST, CS idle HIGH).
- **Stock firmware** (`xteink_app` 7.2.4, IDF 6.0.1): runs to Home with the device's NVS as dumped; its
  WiFi start (14 s) ends in `wifi:force witi stop` at +7.5 s and a deinit at +17.5 s without stalling
  the UI (`tools/nvsedit.py IMAGE set-u8 user_config net_en 0` skips it). It fades the frontlight out 60 s
  after the last input and back on the next touch (LEDC hardware fade, walked in guest time; `state.ledc`
  shows `fading`/`target_permille`). Its light panel is a pull-down from the portrait top edge
  (`x4emu swipe 5 240 300 240 --ms 600`; brightness and colour-temperature steps, Light button; `x4emu
  light` follows). Emulator-only console line:
  `phy: error: pll_cal exceeds 2ms!!!` (x6, RF PLL lock flag unanswered). RTC IO pads, RMT, I2S,
  APB_SARADC DMA mode, USB OTG stay unmodelled; SAR oneshots and the temperature sensor answer fixed values.

## Layout

```
qemu/          Espressif QEMU clone (esp-develop @ febae182), branch x4pro (gitignored; make setup)
qemu-patches/  our QEMU changes as a patch series applied by `make setup` (the source of truth)
models/        pure-C panel cores (SSD1677/UC8179/UC8279) + host tests + device fixtures
x4emu/         the CLI as a package (pipx install . → `x4emu`; docs/x4emu.md); tools/x4emu is its in-repo shim
tools/         x4emu shim, x4emu_mcp.py, mkflash.py, mksd.py, mkefuse.py, nvsedit.py, device.py, epdtrace.py, appdis.py, tcbwalk.py
tests/         pytest end-to-end (boot, home, touch, reader, battery, sleep, panel variants)
firmware/      crosspoint-reader submodule (with freeink-sdk); firmware-patches/ = EpdBus trace
docs/          hardware.md (facts + sources), audit.md (brief audit), log.md, device/ (captures)
images/        gitignored: flash dumps, SD images, build outputs
```

Paths must not contain spaces (QEMU's configure and the ESP-IDF toolchain refuse them). The repo
lives at `/Users/mini/x4pro-emu`; `/Users/mini/xteink x4/x4pro-emu` is a symlink to it.

## Build

```
make setup      # venv (.venv), clone qemu + apply qemu-patches/, clone firmware
make build      # qemu (xtensa-softmmu), models, CrossPoint x4pro image (pio run -e x4pro)
make test       # models unit tests + fixture replay + pytest end-to-end (no device needed)
```

Homebrew/apt packages: git build-essential ninja meson pkg-config glib pixman libgcrypt libslirp
libpng mtools dosfstools python3 platformio (`pio`). On macOS also `coreutils` (gtimeout) is handy.
CI: `.github/workflows/ci.yml` runs exactly `make setup && make build && make test` on Ubuntu 24.04
(cached QEMU tree and PlatformIO packages; screenshots and `.x4emu/*/` logs uploaded as artifacts).
The stock tests skip there (the device dump never leaves `images/`); `make test PYTEST_ARGS=…` passes
extra pytest flags.
Everything Python runs from `.venv/bin/python` (`-m esptool`, `-m espefuse`, `-m pytest`).
QEMU: `cd qemu/build && ../configure --target-list=xtensa-softmmu --enable-gcrypt --enable-slirp
--enable-png --disable-user --disable-capstone --disable-vnc --disable-gtk --disable-sdl
--disable-docs && ninja qemu-system-xtensa` (that target also runs macOS code signing).

## Boot a firmware image and take a screenshot (five commands)

```
.venv/bin/python tools/mkflash.py images/flash.bin --build firmware/.pio/build/x4pro
.venv/bin/python tools/mksd.py images/sd.img --size 256M --src path/to/books    # MBR + FAT32
.venv/bin/python tools/x4emu run --flash images/flash.bin --sd images/sd.img --fast-epd
.venv/bin/python tools/x4emu wait-text "Entering activity: Home" --timeout 60
.venv/bin/python tools/x4emu screenshot home.png
```

The efuse file is generated automatically from `docs/device/efuse-dump.txt` (real MAC, rev v0.2,
8 MB PSRAM). Drop `--fast-epd` for real refresh timing. A raw 16 MB device dump runs as-is
(`--flash images/device/flash-….bin`).

Stock firmware: `tools/mkflash.py images/stock.bin --raw images/device/flash-2026-09-06-a.bin` (optionally
`tools/nvsedit.py images/stock.bin set-u8 user_config net_en 0` to skip its 18 s WiFi start), a card built
from the device's files (`tools/mksd.py images/sd-full.img --src images/device/sd-files`: the stock's external
font and cache plans live there; `images/sd-device.img` lacks them), then `x4emu --name stock run --flash
images/stock.bin --sd images/sd-full.img --boot-hold-power`. Its boot-preflight rejects a cold boot unless
the power button is held (`--boot-hold-power [MS]`, default 3000: pressed over QMP before the first
instruction); without the flag it deep-sleeps and `x4emu --name stock press power` wakes it. Home (refresh 2)
follows in ~5 s. A cold boot with WiFi on (`net_en=1`) blocks in the PHY's full calibration (NEXT_PHASE §3.2.6):
boot that case through the wake. Refresh 3 is the status-bar
clock, completed at the next minute: the stock pre-sends the old plane after every refresh, so a trace ending
in DTM1 is idle, and "wait for three refreshes" takes 0..60 s (`tests/test_stock.py` boots scratch copies).

## MCP server (preferred for agents)

`.mcp.json` registers `tools/x4emu_mcp.py` (start Claude Code inside this directory, or
`claude mcp add x4emu -- .venv/bin/python tools/x4emu_mcp.py`). Tools mirror the CLI: `emu_run`,
`emu_wait_text`, `emu_wait_quiet`, `emu_tap`/`emu_press`/`emu_hold`/`emu_chord`/`emu_home`,
`emu_screenshot` (image inline, optional diff), `emu_state`, `emu_console`, `emu_trace_tail`,
`emu_battery`, `emu_light`, `emu_qmp`, `emu_console_send`, `emu_flash_app`; `build_firmware`, `build_flash_image`, `build_sd_image`;
`device_status`, `device_console`, `device_fetch_screenshots`, and the guarded
`device_flash_crosspoint` / `device_restore_stock` (plan only unless `confirm=true`).

## CLI reference (`tools/x4emu [--json] --name NAME …`, default name dev0)

`--json` before the subcommand makes every command print one JSON object (errors as `{"error": …}` with
the same exit code); the shapes and an install guide are in `docs/x4emu.md`.

| Command | Effect |
|---|---|
| `run --flash F [--sd IMG] [--efuse F] [--panel ssd1677\|uc8179\|uc8279] [--fast-epd] [--gdb] [--trace-epd F] [--trace-i2c F] [--icount N] [--no-usb-host] [--boot-hold-power [MS]]` | start QEMU detached; `.x4emu/NAME/` holds qmp.sock, console.log (USB Serial/JTAG), uart0.log, qemu.log, pid, run.json |
| `stop` / `reset` / `status` | quit; system_reset; pid + run state (+ "deep sleep" when paused by the sleep model) |
| `state` | JSON: uptime, panel, refresh_count, last_mode, busy, gpio levels, usj/spi2 (`busy`, `transfer_ms`)/i2c0 counters, buttons, touch, gt911 (`frames`, `clears`), battery, rtc, ledc channels (`fading`, `target_permille`), sleep, ana_i2c (analog-master transactions), saradc (oneshots, tsens reads), rf (radio-stub counters, `hot` polls), iolog_hot (unmodelled registers polled past the log cap) |
| `log [--follow] [--since N] [--file uart0.log]` / `wait-text TEXT [--timeout S]` | console access; never blocks past the timeout |
| `screenshot OUT.png [--diff OTHER.png]` | QMP screendump of the panel console (`device=epd`); `--diff` prints % pixels differing and exits 1 when they differ; a 480x800 device BMP is un-rotated automatically |
| `wait-refresh [--count N] [--total N] [--timeout S]` / `wait-quiet [--seconds S]` | wait for N more refreshes / until refresh_count ≥ N / until the panel has been idle for S s |
| `press left\|right\|power [--ms 120] [--wait S] [--quiet S]` / `hold BTN --ms 3000` | active-low buttons; `--wait` reports the refresh that follows, `--quiet` first waits for an idle panel; a power press while sleeping is extended to 1.5 s |
| `chord power right [--ms 300]` | several buttons at once; Power + Down is CrossPoint's screenshot chord (writes `/screenshots/*.bmp` to the card) |
| `tap X Y [--ms] [--wait] [--quiet]` / `swipe X1 Y1 X2 Y2 [--ms] [--wait] [--quiet]` / `home [--ms 250] [--wait] [--quiet]` | landscape panel pixels → GT911 portrait frame (inverse of swapXY/flipY); Home = the capacitive pad (the stock ignores it). `tap`/`home` stay down for at least `--ms` **and until the firmware has read the frame** (`state.gt911.clears`; up to 5 s), so a tap into CrossPoint's multi-second rendering pass is not lost; the output says when it was read. `swipe` holds every point until read (the GT911 model keeps only the latest point) |
| `battery --soc N --mv N --charging on\|off` / `light [-v]` | CW2017 values and the charger STAT line; LEDC duty (permille) of the cool/warm channels |
| `console-send TEXT [--no-newline]` | write into the guest's USB Serial/JTAG console (RX path) |
| `flash-app --app APP.bin [--build DIR]` | swap the app (and bootloader/table with `--build`) inside the live flash image, keep NVS/SD, relaunch |
| `mem read ADDR LEN` / `gdb` / `qmp JSON` | human-monitor `xp`; launches `xtensa-esp-elf-gdb` on :1234 (run with `--gdb`; the pioarduino gdb builds cannot talk to this QEMU, see log); raw QMP, e.g. `{"execute":"trace-event-set-state","arguments":{"name":"m25p80_*","enable":true}}` turns QEMU trace events into `qemu.log` lines at runtime |
| `tools/nvsedit.py IMAGE list` / `set-u8 NS KEY VALUE` / `set-str NS KEY VALUE` / `erase NS KEY` / `redact` | read or edit the NVS partition of a flash image (0x9000/0x5000 by default); `redact` erases `user_config/sta_ssid`, `sta_pwd`, `wifi_creds` (slots blanked) and sets `net_en` 0, so a stock image can leave `images/` (`tests/test_nvsedit.py`) |

ROM symbols for the stock app's PCs: `make rom-symbols` → `images/rom/esp32s3_rev0_rom.nm`; the app
itself is disassembled with `tools/appdis.py images/device/stock-app0-7.2.4.bin 0xPC` (segments parsed
from the image header). `tools/tcbwalk.py DRAM.bin --rom … --app …` walks every FreeRTOS task of a
`pmemsave` dump (QMP `pmemsave`, val 0x3FC88000 size 0x78000): what each waits on, its backtrace, and
`--frame PC,A0,SP` backtraces a live CPU from `info registers -a`. When touch, I2C and the 3-second gauge
poll stop together, check the tick there first (docs/log.md 2026-09-06, session 4).

Tips: CrossPoint ignores input while it is painting, so use `--quiet 2` or `wait-quiet` before an
input in scripts. Power: < 400 ms is a click (double-click toggles the frontlight), ≥ 400 ms held
sleeps. Panel coordinates are the landscape 800x480 screenshot; CrossPoint draws its portrait UI
rotated on it. `tools/epdtrace.py device.log emu.jsonl --from-cmd 0x00` compares panel command
streams; `models/build/replay uc8279 fixture.log` replays a recording through the core. Unmodelled-register polls:
the `x4pro/<block>` loggers write 32 lines per address to qemu.log and count the rest into `state.iolog_hot`
(`state.rf.hot` for the radio stub); `x4emu run … -global driver=esp32s3.rfstub,property=sticky,value=0xADDR:0xMASK`
makes a radio status bit read as set without a rebuild. Instance names (`--name`): short and without `=`
(they become UNIX socket paths).

## The real device (rules, in priority order)

A physical X4 Pro is attached over pogo pins as `/dev/cu.usbmodem*` (USB Serial/JTAG, 0x303A:0x1001).
In deep sleep the port disappears; only its power button brings it back. The stock app switches to
a mass-storage personality ("XTEink X4 Pro", 0x303A:0x4002) in USB mode.

1. Nothing is written to the device without a verified double backup on disk
   (`tools/device.py backup`: two 16 MB reads, SHA-256 equal). Current backup:
   `images/device/flash-2026-09-06-{a,b}.bin`, `docs/device/flash-backup-sha256.txt`.
2. Never `erase-flash`, never `espefuse burn-*`, never write 0x0..0x10000 (bootloader, partition
   table, otadata) except to restore the verified backup byte for byte. Writing an OTA app slot
   (0x10000 or 0x7F0000 under the stock table) is allowed after the backup. Current state: app0 =
   CrossPoint 1.6.0 (EpdBus-traced build), app1 = the stock 7.2.4 image, otadata untouched (boots app0).
   `tools/device.py restore-stock --yes` puts stock back into app0.
3. Do not touch the device during a transfer; the magnetic pogo adapter detaches easily. If a
   transfer breaks, check the device state before retrying.
4. GPIO19/20 are USB D-/D+: never run any pin or bus probe over them.
5. If the device stops responding, stop, write down what was done, and follow
   `firmware/docs/fix-bricked-xteink.md` and the CrossPoint web tools.
6. The flash dump contains the owner's WiFi credentials (NVS). It never leaves `images/`.

`tools/device.py` subcommands: `inventory`, `efuse-summary`, `backup`, `console [--reset]`,
`image-sd`, `flash-crosspoint [--firmware F] [--preserve-stock] --yes`, `restore-stock --yes`.
Every write-capable command prints its plan and re-reads the device before writing.

## Known facts about the desk unit

ESP32-S3 rev v0.2, 8 MB octal PSRAM, MAC 98:c3:77:be:ea:30. Panel **UC8279** (probe
`VER=00 0F 68 00 00 FLG=13`, LUT_VER 0x68), which is the emulator's default. SD card 15.7 GB FAT32
(files copied to `images/device/sd-files/`, including CrossPoint's `.crosspoint/` state). Oracles from
the device: its home-screen screenshot (`tests/golden/device-home-screenshot-3824.bmp`, reproduced with
0 pixels different), its panel command stream (`models/fixtures/`), its boot and wake logs
(`docs/device/`). The stock's home screen golden (`tests/golden/stock-home.png`) is emulator-made; the
device shows the same empty bookshelf. See `docs/hardware.md` and `docs/device/`.
