# x4pro-emu — Xteink X4 Pro emulator (QEMU, ESP32-S3)

Linux/macOS-hosted emulator for unmodified Xteink X4 Pro firmware images, driven from a shell:
buttons, touch, console, panel screenshots, JSON state. CrossPoint 1.6.0 boots to its home screen,
opens books, pages, sleeps and wakes in it. Progress and dead ends: `docs/log.md`.
**Next agent: read `docs/NEXT_PHASE.md`** — the stock-firmware plan (SAR ADC first) and the world-class checklist.

## What is NOT emulated (how it shows up for a firmware developer)

- **USB mass storage / TinyUSB**: the "File Transfer" screen does nothing; the host never sees a disk.
- **WiFi / BLE**: radio calls fail or time out; nothing enumerates on the network side.
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
- **Stock firmware** (`xteink_app` 7.2.4, IDF 6.0.1) stops after `cpu_start: Multicore app`; see
  `docs/log.md` M6.

## Layout

```
qemu/          Espressif QEMU clone (esp-develop @ febae182), branch x4pro (gitignored; make setup)
qemu-patches/  our QEMU changes as a patch series applied by `make setup` (the source of truth)
models/        pure-C panel cores (SSD1677/UC8179/UC8279) + host tests + device fixtures
tools/         x4emu CLI, mkflash.py, mksd.py, mkefuse.py, device.py, epdtrace.py
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

## MCP server (preferred for agents)

`.mcp.json` registers `tools/x4emu_mcp.py` (start Claude Code inside this directory, or
`claude mcp add x4emu -- .venv/bin/python tools/x4emu_mcp.py`). Tools mirror the CLI: `emu_run`,
`emu_wait_text`, `emu_wait_quiet`, `emu_tap`/`emu_press`/`emu_hold`/`emu_chord`/`emu_home`,
`emu_screenshot` (image inline, optional diff), `emu_state`, `emu_console`, `emu_trace_tail`,
`emu_battery`, `emu_light`, `emu_qmp`, `emu_console_send`, `emu_flash_app`; `build_firmware`, `build_flash_image`, `build_sd_image`;
`device_status`, `device_console`, `device_fetch_screenshots`, and the guarded
`device_flash_crosspoint` / `device_restore_stock` (plan only unless `confirm=true`).

## CLI reference (`tools/x4emu --name NAME …`, default name dev0)

| Command | Effect |
|---|---|
| `run --flash F [--sd IMG] [--efuse F] [--panel ssd1677\|uc8179\|uc8279] [--fast-epd] [--gdb] [--trace-epd F] [--trace-i2c F] [--icount N] [--no-usb-host]` | start QEMU detached; `.x4emu/NAME/` holds qmp.sock, console.log (USB Serial/JTAG), uart0.log, qemu.log, pid, run.json |
| `stop` / `reset` / `status` | quit; system_reset; pid + run state (+ "deep sleep" when paused by the sleep model) |
| `state` | JSON: uptime, panel, refresh_count, last_mode, busy, gpio levels, usj/spi2/i2c0 counters, buttons, touch, gt911, battery, rtc, ledc channels, sleep |
| `log [--follow] [--since N] [--file uart0.log]` / `wait-text TEXT [--timeout S]` | console access; never blocks past the timeout |
| `screenshot OUT.png [--diff OTHER.png]` | QMP screendump of the panel console (`device=epd`); `--diff` prints % pixels differing and exits 1 when they differ; a 480x800 device BMP is un-rotated automatically |
| `wait-refresh [--count N] [--total N] [--timeout S]` / `wait-quiet [--seconds S]` | wait for N more refreshes / until refresh_count ≥ N / until the panel has been idle for S s |
| `press left\|right\|power [--ms 120] [--wait S] [--quiet S]` / `hold BTN --ms 3000` | active-low buttons; `--wait` reports the refresh that follows, `--quiet` first waits for an idle panel; a power press while sleeping is extended to 1.5 s |
| `chord power right [--ms 300]` | several buttons at once; Power + Down is CrossPoint's screenshot chord (writes `/screenshots/*.bmp` to the card) |
| `tap X Y [--ms] [--wait] [--quiet]` / `swipe X1 Y1 X2 Y2 [--ms]` / `home [--ms]` | landscape panel pixels → GT911 portrait frame (inverse of swapXY/flipY); Home = the capacitive pad |
| `battery --soc N --mv N --charging on\|off` / `light [-v]` | CW2017 values and the charger STAT line; LEDC duty (permille) of the cool/warm channels |
| `console-send TEXT [--no-newline]` | write into the guest's USB Serial/JTAG console (RX path) |
| `flash-app --app APP.bin [--build DIR]` | swap the app (and bootloader/table with `--build`) inside the live flash image, keep NVS/SD, relaunch |
| `mem read ADDR LEN` / `gdb` / `qmp JSON` | human-monitor `xp`; launches `xtensa-esp-elf-gdb` on :1234 (run with `--gdb`); raw QMP |

Tips: CrossPoint ignores input while it is painting, so use `--quiet 2` or `wait-quiet` before an
input in scripts. Power: < 400 ms is a click (double-click toggles the frontlight), ≥ 400 ms held
sleeps. Panel coordinates are the landscape 800x480 screenshot; CrossPoint draws its portrait UI
rotated on it. `tools/epdtrace.py device.log emu.jsonl --from-cmd 0x00` compares panel command
streams; `models/build/replay uc8279 fixture.log` replays a recording through the core.

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
(`docs/device/`). See `docs/hardware.md` and `docs/device/`.
