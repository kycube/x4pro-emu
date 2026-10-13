# x4pro-emu — Xteink X4 Pro emulator (QEMU, ESP32-S3)

Linux/macOS-hosted emulator for unmodified Xteink X4 Pro firmware images, driven from a shell:
buttons, touch, console, panel screenshots, JSON state. CrossPoint 1.6.0 boots to its home screen,
opens books, pages, sleeps and wakes in it; the stock `xteink_app` (7.2.4, 7.5.4, 7.6.8) boots to its home
screen, paints, lights the frontlight, takes touch, runs its Lua apps, and its WiFi start comes up and
stops itself (no radio). The stock's new generation, **XTOS Licorice 261013** (the device's since
2026-10-13), reads, lights, sleeps and wakes; USB mode and app installs not yet (`docs/stock-licorice.md`). `README.md` is the public overview, `docs/README.md` the index of every document.
Progress and dead ends: `docs/log.md`. **Next agent: read `docs/NEXT_PHASE.md`**, §0 (resume) and §10 (the
work runs as squads: the coordinator forms agents per milestone, and since 2026-10-12 **Opus 5.5 does all of
it** — coordination, well-specified tasks, symbol-less firmware reading and new models alike; Fable 5.1 only
when the owner asks for it).

**The repository is public: keep it presentable and the README current.** `README.md` is what visitors see
on GitHub. In the same commit as anything user-visible — a tool or CLI command, an app, a supported firmware
version, a newly modelled peripheral, a removed limitation — update `README.md` (its feature list, "What is not
emulated", the docs table) and list any new document in `docs/README.md`. When a golden the README shows
changes, rerun `tools/readme_images.py`. No stray files at the root or in `docs/`: scratch work goes to the
session scratchpad or the gitignored `images/`; nothing from `images/` (device dumps hold the owner's WiFi
credentials) or `fonts/` (proprietary copies) is ever committed. Write README claims you can point to a test or a
log entry for.

## Build and test

```
make setup      # venv (.venv), clone qemu + apply qemu-patches/, check out the firmware submodule
make build      # qemu (xtensa-softmmu), models, CrossPoint x4pro image (PlatformIO in .venv-pio)
make test       # models unit tests + fixture replay + pytest on up to 8 xdist workers, ~2 min (no device needed)
```

Packages (Homebrew/apt): git build-essential ninja meson pkg-config glib pixman libgcrypt libslirp libpng
mtools dosfstools python3, plus a Python 3.10–3.14 for PlatformIO (Homebrew's `platformio` runs on 3.15,
which the pinned platform refuses; `make firmware PIO=pio` uses one on PATH). On macOS `coreutils` is handy.
Everything Python runs from `.venv/bin/python` (`-m esptool`, `-m espefuse`, `-m pytest`). The QEMU configure
line is the Makefile's (`make qemu`; on macOS `ninja qemu-system-xtensa` also code-signs); after a Homebrew
upgrade a build directory can point at removed kegs (`gcrypt.h not found`): re-run the configure.
`make test-serial` runs one test at a time, `make test-durations` re-measures the longest-first order in
`tests/durations.json`, `make test PYTEST_ARGS=…` passes pytest flags. Tests that wait on firmware timers run
with `x4emu run --icount shift=2,sleep=off`; never `shift=auto` (under load the stock's boot-preflight
rejected the held button on 12 of 12 boots; `docs/x4emu.md`).
CI (`.github/workflows/ci.yml`) runs exactly `make setup && make build && make test` on Ubuntu 24.04; the stock
tests skip there (the device dump never leaves `images/`). A weekly upstream watch checks CrossPoint,
Espressif QEMU, PlatformIO and Homebrew drift (`docs/upstream.md`).

Paths must not contain spaces (QEMU's configure and the ESP-IDF toolchain refuse them). The repo
lives at `/Users/mini/x4pro-emu`; `/Users/mini/xteink x4/x4pro-emu` is a symlink to it.

## Boot a firmware image and take a screenshot

```
.venv/bin/python tools/mkflash.py images/flash.bin --build firmware/.pio/build/x4pro
.venv/bin/python tools/mksd.py images/sd.img --size 256M --src path/to/books    # MBR + FAT32
.venv/bin/python tools/x4emu run --flash images/flash.bin --sd images/sd.img --fast-epd
.venv/bin/python tools/x4emu wait-text "Entering activity: Home" --timeout 60
.venv/bin/python tools/x4emu screenshot home.png
```

The efuse file is generated from `docs/device/efuse-dump.txt` (real MAC, rev v0.2, 8 MB PSRAM). Drop
`--fast-epd` for real refresh timing. A raw 16 MB device dump runs as-is.

The stock firmware, from a scratch copy of a dump (`flash-2026-09-06-a.bin` = 7.2.4, what `tests/test_stock.py`
uses; `flash-2026-10-12-a.bin` = 7.6.8; `flash-2026-10-13-v261013-a.bin` = Licorice, the device's, which logs
nothing on the console and paints Home in refresh 1):

```
.venv/bin/python tools/mkflash.py images/stock.bin --raw images/device/flash-2026-09-06-a.bin
.venv/bin/python tools/nvsedit.py images/stock.bin redact     # credentials blanked, net_en 0: skips the 18 s WiFi start
.venv/bin/python tools/mksd.py images/sd-full.img --src images/device/sd-files
.venv/bin/python tools/x4emu --name stock run --flash images/stock.bin --sd images/sd-full.img --boot-hold-power
.venv/bin/python tools/x4emu --name stock wait-refresh --total 2 --timeout 90           # 2 = Home, ~5 s
```

The card must come from the device's files: the stock's external font and cache plans live there
(`images/sd-device.img` lacks them). The stock's boot-preflight rejects a cold boot unless the power button is
held, which `--boot-hold-power` does over QMP; without it the stock deep-sleeps and `press power` wakes it
(`docs/x4emu.md`). Refresh 3 is the status-bar clock at the next minute, 0..60 s later; a panel trace ending in
DTM1 is idle, not stalled (`docs/hardware.md`).

## Where things are

| | |
|---|---|
| `x4emu/` (shim `tools/x4emu`) | the CLI: every command, its `--json` shape, the input-delivery and guest-time rules — `docs/x4emu.md`. Instance names (`--name`) become UNIX socket paths: short, no `=` |
| `.mcp.json` → `tools/x4emu_mcp.py` | MCP server, preferred for agents: `emu_*` mirror the CLI, `build_*`, `device_*` (the device writers only plan unless `confirm=true`) |
| `qemu-patches/` | our QEMU changes, **the source of truth**, applied by `make setup` onto Espressif's `esp-develop` @ febae182 in `qemu/` (gitignored, branch `x4pro`); re-export after every QEMU commit (`rm qemu-patches/*.patch; cd qemu && git format-patch -o ../qemu-patches febae182..x4pro`) |
| `models/` | pure-C panel cores (SSD1677/UC8179/UC8279), host tests, device fixtures |
| `tools/` | image builders, stock-firmware tools, `device.py`; each tool's docstring is its manual. The stock tools read per-version offsets from `tools/stockver.d/` |
| `tests/` | pytest end-to-end and fast tests; `tests/golden/` the screens |
| `apps/`, `stories/`, `parity/` | Lua apps (`docs/lua-apps.md`), gamebook sources (`docs/gamebook.md`), device-parity scripts (`docs/parity.md`) |
| `firmware/` | CrossPoint Reader submodule (with freeink-sdk); `firmware-patches/` = EpdBus trace |
| `docs/` | `hardware.md` (what is modelled, the full not-emulated list, `x4emu state` keys), the stock pages, `log.md`; index in `docs/README.md` |
| `images/` | gitignored: flash dumps, SD images, ROM symbols (`make rom-symbols`), Ghidra projects, build outputs |

Debugging a firmware in the emulator (hot-poll lists, TCB walks, register dumps, panel-stream diffs):
`docs/NEXT_PHASE.md` §7.

## What is NOT emulated (full list: `docs/hardware.md`)

USB mass storage / OTG (the host never sees a disk); WiFi and BLE traffic (the blocks a WiFi start touches are
answered, the driver finds no air and stops itself about 8 s after `wifi:mode : sta`; BLE untested); RMT, I2S,
APB_SARADC DMA mode; the GPIO matrix and IO_MUX pulls electrically. Deep sleep is a paused VM and a reset with
cause 5 (DSLEEP); light sleep returns at once; timing is the device's measured BUSY durations, not cycles; the
two cores keep TSO on any host (`docs/smp.md`).

## The stock firmware: facts that keep you from breaking things

- **Versions.** Every address in `docs/stock-firmware.md` is **7.2.4**'s; 7.5.4 and 7.6.8 are
  re-located in `docs/stock-7.5.4.md` / `docs/stock-7.6.8.md`. The stock tools pick `tools/stockver.d/<version>.json`
  by the image's `esp_app_desc`, check its `app_elf_sha256`, and refuse a version they do not know. **XTOS
  Licorice** (`dragon_example`, IDF 6.1, the device's) is a rewrite, not a 7.x: no developer menu, no
  string packs, apps installed from packages; every tool refuses it (`docs/stock-licorice.md`).
- **Patches go into scratch copies only**, never a dump or extracted app under `images/device/` in place and
  never the device: every tool recomputes the ESP checksum and the appended SHA-256 the bootloader verifies. The developer menu
  (Screen Capture) is compiled out of every version; `tools/stockdev.py` / `tools/stockpatch.py` patch a copy.
- **Lua apps** need no firmware change on 7.5.4 and later: the Mini Apps row appears once `/XTApps` on the card
  holds an app (7.2.4 needs the `lua-apps-row` patch). On the device an app is a card copy (`docs/lua-apps.md`).
- **Oracles.** The stock's screen goldens (`tests/golden/stock-*.png`) are emulator-made; the owner judged Home,
  menu, light panel, Settings, All Files and reader 1:1 with the device by eye (session 5). The device-made
  oracles are CrossPoint's: its home-screen screenshot (`tests/golden/device-home-screenshot-3824.bmp`, 0 pixels
  different), its panel command stream (`models/fixtures/`), its boot and wake logs (`docs/device/`).

## The real device (rules, in priority order)

A physical X4 Pro is attached over pogo pins as `/dev/cu.usbmodem*` (USB Serial/JTAG, 0x303A:0x1001).
In deep sleep the port disappears; only its power button brings it back. The stock app switches to
a mass-storage personality ("XTEink X4 Pro", 0x303A:0x4002) in USB mode.

1. Nothing is written to the device without a verified double backup on disk
   (`tools/device.py backup`: two 16 MB reads, SHA-256 equal). Current backups:
   `images/device/flash-2026-10-13-v261013-{a,b}.bin` (the newest: XTOS Licorice in app1, booting; pristine
   7.6.8 in app0), `flash-2026-10-13-pre-parity-{a,b}.bin` (7.6.8 in app0 booting, 7.5.4 in app1; taken
   before the parity session), `flash-2026-10-12-{a,b}.bin` (the same slots),
   `flash-2026-09-07-{a,b}.bin` (stock 7.5.4 in app1, booting) and `flash-2026-09-06-{a,b}.bin`
   (7.2.4 era), `docs/device/flash-backup-sha256.txt`.
2. Never `erase-flash`, never `espefuse burn-*`, never write 0x0..0x10000 (bootloader, partition
   table, otadata) except to restore the verified backup byte for byte. Writing an OTA app slot
   (0x10000 or 0x7F0000 under the stock table) is allowed after the backup. Current state (2026-10-13, after the
   owner's OTA to Licorice): app0 = pristine stock 7.6.8 (sha256 f68533a8…), **app1 = XTOS Licorice 261013**,
   otadata entry 1 seq 4 → **app1 boots** (`tools/device.py slots`); the efuses read as in
   `docs/device/efuse-dump.txt` after the update too (no flash encryption, secure boot or anti-rollback). `flash-crosspoint` / `restore-stock` read otadata and refuse to write a slot the bootloader
   does not start (a patched stock app goes to the booting slot with `flash-crosspoint --firmware APP.bin --slot
   app0`, `docs/parity.md`). Their default slot, app0, is **not** the booting one now, so they refuse it;
   `--slot app1` would overwrite Licorice. No tool here writes otadata.
3. Do not touch the device during a transfer; the magnetic pogo adapter detaches easily. If a
   transfer breaks, check the device state before retrying. After any esptool command (every
   `tools/device.py` read or write resets the chip) the reader keeps its last picture and ignores input
   until the owner unplugs it and holds the power button (the stock's boot-preflight); say so when handing back.
4. GPIO19/20 are USB D-/D+: never run any pin or bus probe over them.
5. If the device stops responding, stop, write down what was done, and follow
   `firmware/docs/fix-bricked-xteink.md` and the CrossPoint web tools.
6. The flash dump contains the owner's WiFi credentials (NVS). It never leaves `images/`
   (`tools/nvsedit.py IMAGE redact` makes a copy without them for scratch use: the NVS keys and, on
   Licorice, the LittleFS `/radio` store in the spiffs partition; the app in it is still Xteink's and is
   never committed).

`tools/device.py` (subcommands in its docstring) parses the partition table and otadata, never assumes them;
every write-capable command prints its plan and re-reads the device before writing, and
`_refuse_forbidden_write` is a structural guard on every esptool command it builds: no `erase-*`, and no
`write-flash` below 0x10000, so nothing here can move the boot pointer.

## Known facts about the desk unit

ESP32-S3 rev v0.2, 8 MB octal PSRAM, MAC 98:c3:77:be:ea:30. Panel **UC8279** (probe
`VER=00 0F 68 00 00 FLG=13`, LUT_VER 0x68), which is the emulator's default. SD card 15.7 GB FAT32
(files copied to `images/device/sd-files/`, including CrossPoint's `.crosspoint/` state). See
`docs/hardware.md` and `docs/device/`.
