# NEXT PHASE — handoff for the next agent

Goal of this phase, in the owner's words: run the **stock Xteink firmware** in the emulator
(it has features CrossPoint lacks, looks better, and drives the frontlight), and make the emulator
**world class**. This document is the whole context you need; the previous agent's context is gone.
Read `CLAUDE.md` first (build, device rules, where things are), then this file, then the last entries of
`docs/log.md`.

## 0. Resume here (2026-10-29, after the 26102901 update)

**First: the device now runs XTOS Licorice 26102901** (the owner's OTA of 2026-10-29; app0 boots, 261013 stays
in app1; the first Licorice, 261013, came with the OTA of 2026-10-13, and 7.6.8 is now only in the backups).
26102901 ran with no model change and every Licorice test runs on both builds; `nvsedit redact` had to learn its
new secrets (`docs/stock-licorice.md`, "26102901"). Licorice is the stock's rewrite, not a 7.x (`docs/stock-licorice.md`). **The owner's direction, the
same day: make this a fully functional emulator; the old plan (items b–d below: the keyboard, native patches,
the lock screen, all against 7.6.8) matters less now — do not start them unless the owner brings them back.**
Licorice in the emulator: reader, files, light panel and frontlight, Dark mode, Wi-Fi and Bluetooth toggles,
sleep and wake, auto power-off, battery, USB mode (the OTG core, `docs/usb-otg.md`), and installing a signed
package `tools/xtapp.py --format licorice` builds and running it from Mini Apps (`docs/licorice-apps.md`); the
owner judged twelve of its screens 1:1 with the device by eye, and closed the same check for 26102901. **On the
device (2026-10-29): a package xtapp.py builds installs and runs under 26102901** — the first mini app from
here on the hardware. 26102901's new features (startup passcode, collections, StarDict dictionaries, phone
pairing) work in the emulator (`docs/stock-licorice.md`); the efuses re-read unchanged after the update. **Next: USB mass storage past the device
descriptor** (the built-in host's SET_ADDRESS, configuration and bulk-only transport, `docs/usb-otg.md`), then
the Licorice app sections beyond code (assets / data / fonts). WiFi and BLE traffic is the largest gap left
and a project of its own: take it on deliberately.

**Where things stand.** Every firmware runs unpatched: CrossPoint 1.6.0, the stock `xteink_app` 7.2.4, 7.5.4
and 7.6.8, and Licorice 261013 and 26102901; the device runs **Licorice 26102901 in app0** (261013 in app1). `make test` is 272 tests in
about two minutes on eight workers, with no input retries anywhere. On 7.5.4–7.6.8 Lua apps reach the device as a
card copy and keep `ctx.state` in the emulator as on the reader; the gamebook was played on the device under 7.6.8.
The owner's items:

1. ~~The first device-parity session~~ — **passed (2026-10-13)**: 7 captured screens, 0 unexplained
   pixels, the device back on pristine 7.6.8 (`docs/parity.md`, "The first session"). It also showed that
   the stock draws no capture toast over a Lua app, on the device as in the emulator.
2. ~~Sign-off for the Espressif upstream series~~ — **the owner declined, for now (2026-10-13)**. The branches
   `upstream-s3` and `x4pro-on-upstream` stay local (and in `images/upstream-qemu-2026-10-13.bundle`); do not
   propose submitting again unless the owner raises it (`docs/upstream-qemu.md`).
3. **The deployment decision for native patches** (item c below).

**Work is organised as squads: read §10 before starting anything.**

**Next, in order:**

a. ~~The device-parity session~~ — done (2026-10-13, above). The old ignored-tap question is closed as
   well: the emulator's dropped taps were its own GT911 model losing an unread release (fixed; no test
   retries an input any more), and on the reader every tap of the walk reached its screen, including the
   first one after Home.
b. **The keyboard (the owner's second wish), against 7.6.8.** Map the stock's on-screen keyboard with
   `tools/ghidra_stock.py --image images/device/stock-app0-7.6.8.bin --version 7.6.8`: the
   `KeyboardLayer{View,Presenter}` pair (the class list in `docs/stock-firmware.md` §2), where it is invoked
   (WiFi password, Startup Password, search), the key layout table, hit-testing, the key-commit path and the
   text buffer. Then design the hook: new native code compiled with the pioarduino Xtensa toolchain
   (`~/.platformio/packages/toolchain-xtensa-esp-elf`) into the app slot's free space, as an extra IROM
   segment or by extending segment 2 (`docs/stock-firmware.md` §1 weighs the two), a trampoline at the key-commit site, a dictionary on the card, a
   suggestion strip drawn through the app's own text routine; near-miss correction = a key-adjacency-weighted
   edit distance over the dictionary. The segment work needs code (`stockdev.refresh_image` handles the
   checksum and hash, not the segment table); the dictionary and the correction algorithm are host-tested in
   Python first, then C.
c. **Device deployment path for native patches — blocked on one decision by the owner.** A patched stock
   reaches the device two ways: write the **booting** slot (app0 today: `tools/device.py flash-crosspoint
   --firmware APP.bin --slot app0`, allowed after the backup, no rollback if the image is bad — recovery is
   `restore-stock --slot app0` over the pogo pins), or write the spare slot and move the boot pointer
   (otadata), which lives below 0x10000 and is forbidden by CLAUDE.md rule 2 without the owner's explicit
   go-ahead. Ask in plain words; do not build an otadata writer before the answer. Lua apps and data-only
   changes on the card need neither.
d. **Lock screen as a Lua app**: the standby path reads `lockscrMode` 2 but constructs no presenter
   (`docs/stock-firmware.md`, "Probed in session 7"; `docs/lua-apps.md` §5); a Ghidra job on the standby
   coordinator, now in 7.6.8.
e. **Reading typography — a Kindle-like font panel (§11).** Plan only, as the owner asked. §11 was written
   against 7.2.4; 7.6.8's label pack gained font-picker groups ("All", "No TTF fonts", "No XTF fonts", "No
   BIN fonts", "Size") and a font-download set (`docs/stock-7.6.8.md` §1), so the picker has probably
   changed: re-survey it on 7.6.8 before relying on §11.1's "no size control at all".
f. **Smaller open items**: the Lua script time budget (a 2 s `on_draw` is cut short and leaves the app inert
   until Home; the budget and the device's behaviour are unmeasured); DMA-SHA through the GDMA path (no stock
   path seen using it); why 7.2.4's font restore job runs twice (`docs/heap.md`); the firmware's own reader of
   the u32 string packs (header byte 3) and the `.xtapp` install/decrypt/uninstall strings with their 64-app
   limit (`docs/stock-7.6.8.md` §4); 7.5.4's toast pack (u32, 874 groups) stays unloadable (the 7.6.8
   `offset_width` change would likely describe it; not tried);
   `tests/golden/stock-lua-hello.png` is 7.2.4-made, so a 7.6.8 Lua test needs its own goldens; the 4-level
   grey `.xic`; QMP `pmemsave` zero pages; the IO_MUX logger noise (525 + 525 lines per stock boot; the GP-SPI
   +0x38 noise is gone since the 2026-10-13 review); the CrossPoint pin stays at 1.6.0 until new device captures
   exist (1.6.5 builds and boots, `docs/upstream.md`).

**Done since session 8** (each with its `docs/log.md` entry):

- The 7.6.8 re-baseline: `tools/stockver.d/7.6.8.json`, `docs/stock-7.6.8.md`, u32 string packs in sixteen
  languages; the Lua route needs no patch on 7.5.4 or 7.6.8, and the menu presenter writes `presenter+176`
  from an `/sdcard/XTApps` enumeration (2026-10-12, "Upstream check after five weeks").
- CI green again; the session-8 socket-path fix confirmed (same entry).
- The owner picked status screen **B** (`apps/status-panels/`); its spacing was fixed with measured text
  advances (commit 5c17445, `docs/lua-apps.md` §4b, §7).
- The gamebook, played on the reader with state kept across visits (2026-10-12, "A gamebook on the stock").
- Lua exits no longer hang: the GDMA reaches PSRAM, two AES model fixes, `ctx.state` round-trips
  (qemu-patches 0019/0020; "A Lua app's exit no longer hangs").
- The `spinlock.h:141` assert was QEMU's memory order on arm64: TSO (0021/0022, `docs/smp.md`); lost inputs
  were the GT911 model wiping an unread release: frames latch and `x4emu` holds every input in guest time
  until seen (0023/0024, `docs/x4emu.md` "Input delivery"); the weekly upstream watch and PlatformIO in
  `.venv-pio` ("World-class pass, wave 1").
- The two-minute suite, the device-parity harness, the Espressif series prepared, the 7.2.4 heap corruption
  diagnosed as the firmware's own race (`docs/heap.md`; "World-class pass, wave 2").

**Facts a next agent should not re-derive.**

- 7.6.8 = app0 of `images/device/flash-2026-10-12-a.bin`, extracted as `images/device/stock-app0-7.6.8.bin`; a
  7.6.8 app offset X is at 0x10000+X in that flash image, and `stockdev.app_base` finds it. 7.5.4 = app1 of
  `flash-2026-09-07-a.bin` (flash 0x7F0000+X; `app_base` finds app0, not app1), extracted as
  `stock-app1-7.5.4.bin`. 7.2.4 = `stock-app0-7.2.4.bin`.
- From 7.5.4 on the extra nav row is **"Mini Apps"**, "USB Mode" is "USB" and "Cloud Sync" is "Cloud"; with
  all three patches applied the eighth row (Settings) falls off the panel.
- The Lua drawing API has **eight** methods and **no fill** (bars are framed or hatched); `g:text`'s y is the
  top of a ~24 px line box; `ctx.fonts` offers no larger face. A folder app cannot read its own files or
  `require`; a frame keeps its first 1024 drawing commands, a layer has no cap (`docs/lua-apps.md` §4c).
- The gitignored `images/` holds the device dumps, the card mirror (`images/device/sd-files/`), the ROM ELF
  and the Ghidra exports (`images/ghidra/stock-<version>/`, each rebuilt in a minute by
  `tools/ghidra_stock.py analyze [--image … --version …]`). Agents doing QEMU work beside others use their own
  build directory or worktree (`X4EMU_QEMU=…`) and `X4EMU_TEST_PREFIX` for their pytest instances.

## 1. The machine (what is modelled, and where)

- `qemu/` is a plain clone of espressif/qemu `esp-develop` @ febae182 with branch `x4pro`; **the source of
  truth for our QEMU changes is `qemu-patches/`** (35 patches; export with `cd qemu && git format-patch -o
  ../qemu-patches febae182..x4pro` after every QEMU commit, the old files removed first).
- Machine `xteink-x4pro` = Espressif's `esp32s3` machine + overlays (higher MemoryRegion priority):
  USB Serial/JTAG console, full GPIO, SPI2 (CPU FIFO and GDMA paths), I2C0 (GT911 0x5D, BM8563 0x51,
  CW2017 0x63), LEDC with hardware fades, RTC_CNTL deep-sleep overlay (the D-cache occupy/lock DONE shim was dropped on 2026-10-13: the cache model answers it),
  analog-master I2C block (0x6000E000; the RF PLL lock flag and the PHY's DC-offset comparator read as done),
  SENS (SAR oneshot, temperature sensor), radio register stub (FE2/FE/RX/BB/MAC with sticky status bits), RTC
  IO stored-value overlay (0x60008400), named I/O access loggers for everything else (`docs/hardware.md`).
  Espressif's own files are edited only by separate, upstreamable patches (`docs/upstream-qemu.md` §3 says
  which, patch by patch).
- Panel: pure-C core `models/epd_core.c` (SSD1677 / UC8179 / UC8279) behind `hw/display/x4pro_epd.c`
  (SSI peripheral + bit-banged probe pins + BUSY timer + QemuConsole, qdev id `epd`).
- CrossPoint 1.6.0 is verified against the real device: probe answer, 38-command panel stream, BUSY timings,
  wake log, the home screenshot (0 pixels different) and two reader pages (0 outside the footer clock).
  Oracles live in `docs/device/`, `models/fixtures/`, `tests/golden/`.

## 2. Non-negotiables

1. Device rules in `CLAUDE.md` §"The real device": never write 0x0..0x10000, never erase, never
   burn efuses; app-slot writes only via `tools/device.py flash-crosspoint` / `restore-stock`
   (they re-read the device and refuse without the verified backup).
2. The flash dump holds the owner's WiFi credentials in NVS. `images/` is gitignored; if you build
   a stock image with the dump's NVS, keep it under `images/` (or `tools/nvsedit.py redact` it). Redact
   SSID/BSSID/IP in any log you commit (`docs/device/boot-stock-7.2.4.log` shows the convention).
3. Keep the "no edits to Espressif files" discipline: overlays, new files, meson/Kconfig hooks.
   If a fix belongs in an Espressif model, make it a separate, upstreamable patch.
4. Every model change ships with a test: a core unit test, a fixture replay, or a pytest that boots
   real firmware. Every finding that contradicts a doc goes into `docs/audit.md`/`hardware.md`
   with the evidence, and `docs/log.md` gets a dated entry.

## 3. Goal A — stock firmware, fully working

### 3.1 What the stock app does in the emulator (7.2.4, sessions 4–5; 7.5.4 and 7.6.8 the same unless noted)

`tools/mkflash.py images/stock.bin --raw images/device/flash-2026-09-06-a.bin`, `tools/nvsedit.py
images/stock.bin redact` (skips the 18 s WiFi start), then `x4emu --name stock run --flash images/stock.bin
--sd CARD --boot-hold-power [--trace-epd f --trace-i2c g]`:

1. ROM → IDF 6.0.1 bootloader → octal PSRAM → `app_main`. `boot-preflight` rejects a plain power-on
   (`decision=2 reason=3`) and deep-sleeps; `--boot-hold-power` holds the button from reset so the cold
   boot is accepted (`hold=600ms decision=0 reason=11`); without it `x4emu press power` (1.5 s hold) wakes
   it. The real device on a USB reset: `source=6 … auth=3`.
2. NVS loads intact (0 page programs at boot), the card mounts (CMD0…ACMD41, CMD13+CMD17 reads),
   GT911 ID/config read, RTC read, CW2017 polled every 3 s.
3. Panel: three RST pulses, VER read (`00 0F 68 00 00`) through SPI2, then the UC8279 init and the
   home paint through `spi_master` with GDMA (sequence in `docs/hardware.md`). Home at ~5 s;
   warm frontlight on GPIO9 at ~25 %.
4. Touch: a tap on the menu icon (`x4emu tap 88 38`) repaints; the stock reads the GT911 on its
   INT line (`state.gpio_irqs`, `gt911.frames`). Nav menu entries at landscape x 160/245/330/415/500,
   y ≈ 150 (7.2.4: Read, All Files, USB Mode, Cloud Sync, Settings); All Files first row (250, 320); reader
   page centre (400, 240) opens the reading menu, its back arrow is (80, 445); Right/Left page.
   60 s after the last input the frontlight fades out (`state.ledc` `fading` → warm 0), the next touch
   fades it back; the status-bar clock repaints every minute. The Home pad is Back, one level (`x4emu home`).
5. With `net_en=1` (the device's setting): at 14 s `wifi_init` logs appear exactly as on the device,
   PHY 711 calibrates (≈1050 analog-master reads, the temperature sensor, ≈8700 radio-block accesses),
   `wifi:mode : sta (98:c3:77:be:ea:30)` and `wifi:enable tsf` follow as on the device, then — no air —
   `W wifi:TX Q not empty: 500`, `force witi stop`, `flush txq` at +7.5 s and `Deinit lldesc rx mblock:6`
   at +17.5 s. Gauge polls, repaints and touch continue throughout. A cold boot runs the full PHY
   calibration ("Saving new calibration data") through the DC-offset comparator at 0x6000E04C; no
   emulator-only console line remains.

`tests/test_stock.py` boots scratch copies of the dump with `tests/mkepub.py`'s book on the card:
`net_en=0` checks 1–4 against `tests/golden/stock-home.png` (status bar masked); `net_en=1` checks 5
(console sequence, no register polled past 200 k accesses, gauge alive, Home intact, menu tap repaints);
`test_stock_idle_dims_frontlight_and_keeps_ticking` (the 60 s fade, tick alive, tap after it);
`test_stock_screens_walk` (menu, All Files, page 1/2, reading menu, bookshelf with the book, Settings);
`test_stock_home_pad_acts_as_back`, `test_stock_rtc_pinned_by_base_epoch`,
`test_stock_light_controls_follow_the_sliders`; `tests/test_stock_capture.py` boots a developer-patched copy
(`tools/stockdev.py`) and requires its Screen Capture to match the panel outside the status bar.

### 3.2 Status

Done (sessions 4–6, `docs/log.md`): the analog-master I2C block, the radio stub, SENS (SAR oneshot and
temperature sensor), the stock's screens beyond Home with goldens and a walk test, the light pull-down
(`x4emu light` and NVS follow it), the PHY's DC-offset comparator and the RF PLL lock flag (patch 0013, no
`pll_cal` line on either boot path), `--boot-hold-power`, `nvsedit set-str / erase / redact`, the RTC IO
overlay (patch 0015), and the emulator half of a device oracle (the `.xic` decoded, a capture equals the
panel; `docs/xic.md`).

Open:

- ~~A stock screen against a device oracle~~: done (2026-10-13, `docs/parity.md`, 7 screens, 0 unexplained
  pixels). Still open: compare a boot log too (`tools/device.py console --reset --seconds 40`; device
  `wifi:mode : sta` at 13.6 s, emulator 13.3 s cold).
- **Light sleep / esp_pm**: the stock never light-slept so far (`state.sleep.light_sleeps` = 0); keep the plan
  to advance the virtual clock by the timer wake if it ever does.
- **APB_SARADC continuous mode** (0x60040000, DMA) only when a firmware uses it (the logger will show).

## 4. Goal B — fidelity ("world class" model quality)

Done: the waveform-level grey model, validated against a photo of the glass and the default since
2026-09-07 (`gray-k` 38, `docs/grayscale.md`); the partial-window tests and the three SSD1677 addressing
gaps they recorded, fixed (`models/tests/test_windows.c`, 115 checks, no known gap; commit 5f26ba3);
determinism (`--deterministic`, `record`/`replay`, `tests/test_replay.py`: reproduced to the pixel, not to
the microsecond — QEMU's `rr=record` would be the bit-exact step if ever needed); SDMMC configuration
registers and trace events (patch 0016: a stock boot's qemu.log from 484,963 to 1,788 lines); the USB
Serial/JTAG RX path (`x4emu console-send`); the two cores in TSO (`docs/smp.md`); the generic S3 models split
out for Espressif (`docs/upstream-qemu.md`, awaiting the owner). The per-mode BUSY timing table was skipped:
the device logs hold only what the defaults already are.

Open:

- The 150 Hz frame period and ghosting in OTP DUs are not modelled; SSD1677's 105-byte LUT keeps the
  fixed table.
- The noisiest log lines left: the `x4pro/iomux` logger (525 reads + 525 writes per stock boot), which an
  IO_MUX stored-value overlay would end (the `esp32s3.gpspi +0x38` lines are gone since the 2026-10-13 review).
- **GPIO fidelity**: open-drain, per-pin pull registers from IO_MUX (today idle levels are a board
  table), RTC-domain GPIO reads for wake sources.

## 5. Goal C — developer experience

Done: `x4emu` as a package with `--json` everywhere (`docs/x4emu.md`), `record`/`replay`, `shell`, `watch`
(HTTP live view), the MCP server running the package in-process, CI on Ubuntu (green; ~86 s of `make test` on
4 workers), the weekly upstream watch (`docs/upstream.md`), the docs index (`docs/README.md`).
The owner pushes (GitHub Desktop); `gh` is installed and logged in as kycube (§7 has its quirks).

Open:

- Optional SDL window (`configure --enable-sdl`) and a documented `-display sdl` mode; keep headless as the
  default.
- Golden-image tests for every screen we can reach, each paired with a device oracle where one exists (the
  chord in CrossPoint; the stock's Screen Capture through the parity harness).

## 6. Device workflow (what the owner can do for you)

- Console: `tools/device.py console --reset --seconds 40` (always `--reset`; without it the port
  may sit silent). The device auto-sleeps after idle; then the port is gone until a power press.
- Screenshots (CrossPoint): Power + lower side button writes `/screenshots/screenshot-N.bmp`;
  File Transfer mounts the card at `/Volumes/NO NAME`; `x4emu screenshot out.png --diff that.bmp`
  un-rotates the 480x800 BMP. Copy the card's files read-only with `rsync` into
  `images/device/sd-files/` and rebuild `images/sd-device.img` with `tools/mksd.py`.
- Before any write: `tools/device.py slots` (today app0 = 7.6.8 boots, app1 = 7.5.4). Both writers default
  to app0, the booting slot, so `restore-stock --yes` rewrites 7.6.8 from the newest verified backup and
  `flash-crosspoint --yes` would **replace** 7.6.8 (CrossPoint on the device is the owner's call; there is
  no stock copy in a spare slot to fall back to, only the backup). The parity session's write and revert are
  spelled out in `docs/parity.md`, "A device session".
- Recorded oracles so far: `docs/device/boot-stock-7.2.4.log`, `boot-crosspoint-1.6.0*.log`,
  `sleep-wake-crosspoint-1.6.0.log`, `screenshots/screenshot-3824.bmp`, `efuse-*.txt`,
  `photos/reader-aa-text-2026-09-07.jpg`, `models/fixtures/uc8279-device-boot-home.log`,
  `images/device/sd-files/` (the card as of 2026-09-06), the reader-page screenshots behind
  `tests/test_device_screenshot.py`.

## 7. Lessons that cost time (read before touching the models)

- `-d unimp` is blind on this SoC: `esp32s3.iomem` swallows 0x60000000..0x600d1000 silently and
  Espressif's unimp stubs sit *below* it. Use the `x4pro/<block>` overlays; add one per block you
  care about, remove it when a real model lands.
- Espressif's `v10.1.x` tags are upstream QEMU without ESP machines. Base on `esp-develop`.
- Overlays win by MemoryRegion priority (we use 1 for loggers, 2 for models). The strap register
  must keep answering 0x4 or the ROM enters download mode.
- The USB console only flows while ESP-IDF's SOF monitor sees SOF (we set it on every INT_RAW read)
  and HWCDC's `connected` is true (IN_EMPTY after each flush); CrossPoint's 1 ms TX timeout drops
  whole lines otherwise.
- Firmware ignores input while painting; the CLI has `--quiet` / `wait-quiet`, and
  `wait-refresh --total`/`press --wait` avoid the baseline race that looked like 4-second latency.
  Both firmwares poll input only between rendering passes that can take seconds ("Time = 3494 ms from
  clearScreen to displayBuffer"). Since 2026-10-13 every input is held in guest time until the firmware has
  consumed it and its release, and the GT911 model latches frames until the clear (`docs/x4emu.md`, "Input
  delivery"); a test that still loses an input has found a bug, not a flake — do not add a retry.
- SdFat mounts MBR partition 1 only; `mksd.py` writes an MBR. QEMU needs power-of-two images.
- The GT911 only answers while GPIO2 is driven LOW (with GPIO1 HIGH); the model NACKs otherwise.
- The panel probe is bit-banged by CrossPoint but read through SPI2 half-duplex by the stock;
  both paths are modelled. A UC part must also answer 0xA2 (RMTP).
- The pioarduino `uv` installer was blocked by the owner's firewall once; pip pre-install works.
- zsh: quote globs that may not match (`ls x* 2>/dev/null` errors with "no matches found"), and
  variables holding commands are not word-split (use arrays or Python).
- macOS builds `qemu-system-xtensa-unsigned`; `ninja qemu-system-xtensa` runs the signing step.
- `gh` is installed and logged in as kycube; it needs the Little Snitch allowance and works only outside the
  command sandbox (run it with the sandbox disabled). `gh run list -R kycube/x4pro-emu` and `gh run view <id>
  --log-failed` diagnose CI; the unauthenticated Actions API gives steps and durations but never logs or
  artifacts. Docker Hub pulls hang on this Mac, so a containerised Linux reproduction is not available.
- Espressif's models log only what they do not implement: `dwc_sdmmc_*` lines are `LOG_UNIMP`
  defaults, the SPI/I2C/GPIO traffic of implemented registers is silent. Turn on QEMU trace events at
  runtime through QMP (`trace-event-set-state`, e.g. `m25p80_*`, `sdcard_*`) before rebuilding anything.
- `-d exec` (`log exec` via QMP) plus a debug stop shows the translation block that touched a bad
  address; `get_pc()` in the memory path reports the block start, not the instruction.
- FreeRTOS TCB walk (docs/log.md 2026-09-06) tells you what every task waits on without symbols;
  ROM symbols come from `images/rom/esp32s3_rev0_rom.nm` (`make rom-symbols`); the app is disassembled
  with `tools/appdis.py IMAGE 0xPC` on the segments parsed from the ESP image header (esptool's "File
  offs" is the segment header, data is +8); `tools/ghidra_stock.py` answers xrefs, callers and decompiles.
- Panel command streams: `tools/epdtrace.py device.log emu.jsonl --from-cmd 0x00` compares a device
  recording with `x4emu run --trace-epd`; `models/build/replay uc8279 FIXTURE.log` replays a recording
  through the core alone.
- The pioarduino gdb builds do not work against this QEMU (python libs missing / no XML target
  description); QMP `info registers -a`, `pmemsave` and `xp` do the job.
- Three Espressif-model bugs bit the stock (patches 0006, 0008, 0009): interrupt re-routing, GDMA
  descriptor look-ahead, QIO dummy cycles. When IDF code "spins forever" on this SoC, suspect the
  model before the firmware.
- The PHY's status polls have no timeouts. Find them from `x4emu state` (`rf.hot`, `iolog_hot`: the
  addresses polled past the 32-line log cap, with counts), take the PC with `info registers -a` over
  QMP (`x4emu qmp '{"execute":"human-monitor-command","arguments":{"command-line":"info registers -a"}}'`),
  disassemble the app there (`tools/appdis.py images/device/stock-app0-7.2.4.bin 0xPC`), and answer
  the bit (a sticky entry in `esp32s3_rfstub.c`, or a model). Before the cap, one such poll wrote
  1.9 GB of qemu.log in 30 s. Loop counters kept with `l16ui/s16i` wrap at 65536 and look like
  countdowns in register samples.
- When touch, I2C and the 3-second gauge poll stop together, the tick is dead: `pmemsave` over QMP
  (`{"execute":"pmemsave","arguments":{"val":1070170112,"size":491520,"filename":"/abs/dram.bin"}}`;
  the HMP form parses a `/` in the path as a division) + `tools/tcbwalk.py` show every task's wait in
  one screen; `info registers -a` gives PS.INTLEVEL / INTERRUPT / INTENABLE, `x4emu mem read 0x600C2000
  396` the matrix map (source → CPU interrupt), and the level-1 dispatcher loop (app 0x4037c58e..c5e6)
  re-dispatches a level source its handler cannot clear forever. A model must never complete
  "instantly" what a driver reads progress back from (LEDC fades, session 4).
- `x4emu --name` becomes a directory under `.x4emu/` and part of two UNIX socket paths: no `=` in it
  (QEMU's `-qmp unix:PATH` option parser splits on it) and keep it short (macOS caps socket paths at
  104 bytes; pytest names come from the test function plus the parametrize id).

## 8. Definition of "world class" (checklist)

- [x] Stock firmware: boots, renders, navigates, frontlight observable (WiFi off in NVS).
- [x] Stock firmware: WiFi start fails fast (analog-master I2C block, SENS, radio stub; `tests/test_stock.py`).
- [x] Stock firmware: the screens reachable by touch have goldens and a walk test (session 4).
- [x] Stock firmware: screens matched to the device — by the owner's eye (Home, menu, light panel, Settings, All
  Files, reader; session 5), and pixel for pixel through the stock's own `.xic` captures (Home, menu, Settings,
  All Files, reader, Mini Apps, a Lua app; 0 unexplained pixels, the parity session of 2026-10-13, `docs/parity.md`).
- [ ] CrossPoint: every activity reachable by script has a golden and a device oracle — Home ✓ (session 3) and
  the reader's pages 1–2 ✓ (session 7: the device's own screenshots of the test book match the emulator's
  base frame in 0 pixels outside the footer clock, `tests/test_device_screenshot.py`); FileBrowser, sleep
  screen and the settings pages still lack a device shot.
- [x] Deterministic replay of an input script yields identical screenshots run to run (`tests/test_replay.py`, session 5).
- [x] Waveform-aware grayscale validated against the device (session 7: the owner's photo of the test book on
  the glass shows one uniform grey rim per glyph — the model, not the two-tone table; the rim measures ~0.45
  reflectance, so `gray-k` = 38; default on since 2026-09-07, `docs/device/photos/reader-aa-text-2026-09-07.jpg`).
- [x] `make setup && make build && make test` verified on Ubuntu 24.04 in CI (first run green, session 5) and on macOS.
- [x] `x4emu` installable, JSON everywhere, documented in one page (`docs/x4emu.md`, session 4).
- [ ] Generic S3 models proposed upstream; board file and panel cores stay here. (Prepared and
  checkpatch-clean, `docs/upstream-qemu.md`; submission waits for the owner.)
- [ ] `docs/audit.md`/`hardware.md` still the single source of truth, every claim with evidence.

## 9. Goal D — agent ergonomics, as milestones (the agent iterates on firmware through these)

Done in this phase: the MCP server (screenshots inline), `make run` (build → image →
boot → wait → screenshot), `x4emu flash-app` (swap the app in the live image, keep NVS/SD,
relaunch), `x4emu console-send` (bidirectional USB Serial/JTAG: the chardev is now a unix socket
with a logfile), hot-poll lists in `x4emu state` (`iolog_hot`, `rf.hot`: which unmodelled register a
firmware spins on, without reading qemu.log), `tools/appdis.py` (app disassembly at a PC). Next:

**D1. Firmware debug console (in the custom firmware, from day one).** A line protocol over the
USB Serial/JTAG so the *device* is scriptable exactly like the emulator. Proposed grammar, one
command per line, replies prefixed so they never collide with logs:
```
X4> screenshot            -> X4< SHOT 800 480 1 <base64 of the 1-bpp panel frame, rows top-down>
X4> state                 -> X4< STATE {"activity":"Home","battery":63,"charging":false,...}
X4> tap 345 350           -> X4< OK        (synthetic touch in landscape panel pixels)
X4> key right 120         -> X4< OK        (synthetic button press, ms)
X4> home 120              -> X4< OK
X4> light 60 50           -> X4< OK        (brightness %, warmth %)
X4> sleep                 -> X4< OK        (then the port drops)
X4> version               -> X4< VER <name> <git> <build time>
```
Emulator side: `x4emu console-send` and `emu_console_send` already inject the request; add
`x4emu console-expect PREFIX --timeout S` to collect the reply from console.log and a
`tools/x4target.py` with the same API for the device port. Acceptance: one pytest,
parametrised `--target emu|device`, drives Home → Browse Files → back and diffs `screenshot`
replies against `tests/golden/`; on the device it runs without touching the card or the chord.

**D2. `make run` on the custom firmware** with a lean pioarduino env (no wolfSSL/WiFi unless
used; `build_cache_dir`), ELF kept next to the image for `x4emu gdb`, warm build under 30 s.
Acceptance: edit a string in the firmware → `make run` → new pixels in under 60 s.

**D3. Time control.** `x4emu run --speed N` (icount shift; `--deterministic` = shift 3 and
`--icount shift=2,sleep=off`, which the timer-bound tests use, exist) and
~~`x4emu wait-guest-ms N`~~ (done, session 5: virtual-clock wait, not host sleep) for time-driven UI:
auto-sleep, toasts, battery polls.
Acceptance: a test triggers CrossPoint's inactivity auto-sleep in a few host seconds.

**D4. Parity harness.** ~~Built for the stock~~ (2026-10-13, `tools/parity.py`, `docs/parity.md`): one step
script walked by `x4emu` and, from a printed runbook, by the owner; the stock's own Screen Captures are
paired and every differing pixel is masked, explained or reported. Open: the first device session (§0),
and a D1-driven variant for a custom firmware that needs no hands.

**D5. Fidelity gaps that block custom firmware first.** Analog-master I2C block, WiFi "fail fast" and
the SENS/SAR oneshot are done (§3.2); next RMT/I2S if the firmware uses them, APB_SARADC
continuous mode if a firmware samples with DMA, then USB OTG device mode (large, optional).

What the emulator guarantees a custom firmware today (design against this list): USB Serial/JTAG
console both ways; two cores with the chip's store order (TSO) on any host; GPIO 0..48 with edge/level
interrupts; SPI2 to the panel, CPU FIFO or GDMA (ESP-IDF `spi_master`, interrupt-driven, re-routing in the
interrupt matrix works); GDMA to PSRAM and Espressif's AES model (DMA-AES); UC8279/UC8179/
SSD1677 with the real probe answers and device timings; I2C0 with GT911, BM8563, CW2017 (BATINFO
resident); LEDC frontlight; SDMMC 1-bit card (MBR/FAT32 image); NVS reads and writes through the
QIO flash path; deep sleep with EXT1 wake on GPIO3; efuse/MAC of the desk unit; a WiFi start that
initialises (PHY calibration against the analog-master, SENS and radio-stub models) and then fails the
way ESP-IDF fails without air, so a firmware's WiFi path runs and errors instead of hanging; SAR ADC
oneshots and the temperature sensor with fixed values. Not there: a radio, BLE (untested), USB OTG,
APB_SARADC DMA mode, RMT, I2S, touch sensor pads, ULP.

## 10. How the work is run from here: squads

The session's **coordinator** forms **squads** of agents per milestone. Session 4 ran the first squad
(CI, packaging, brightness) this way; the protocol below is what worked, and the remaining plan is cut
into squads in the order to run them.

### 10.1 Model tiers (who does what)

**The owner's decision, 2026-10-12: Opus 5.5 does all of it.** Sessions 4–8 ran Fable 5.1 as coordinator
and for the symbol-less reading and new models, with Opus 5 for well-specified work; that split was
made before Opus 5.5 existed. Opus 5.5 is the stronger agentic model of the two at a much lower price,
so it now takes every role below. Where an older plan line in this file says "Fable" for a task, read
it as "the Opus 5.5 agent that does the firmware reading". Use Fable 5.1 only when the owner asks for it.

- **Coordinator (Opus 5.5).** Reads `CLAUDE.md`, §0 and the last `docs/log.md` entry, picks the next
  squad, writes each agent's brief, reviews every diff, runs the final `make test`, writes the log entry
  and updates this file and `CLAUDE.md`.
- **Agents (Opus 5.5)**, one brief per disjoint piece of work. The briefs differ by kind, not by model:
  *reading and modelling* work (the stock app without symbols — `.xic`, the GT911 key byte, the PLL
  flag, any "spins forever"; new peripheral models whose behaviour must be inferred — APB_SARADC DMA,
  RMT, I2S, USB OTG; Espressif-model bugs; waveform grayscale; protocol and harness design; any task
  whose acceptance test does not yet exist) gets a time box and "a finding is not a failure";
  *well-specified* work (CI, packaging and CLI features, tests and goldens for behaviour already seen,
  UI hunts with a coordinate map, `nvsedit` features, register overlays, timing tables, docs pages,
  upstream patch preparation) gets a mechanical acceptance check. Session 4 evidence for the second
  kind: three Opus agents delivered CI, the package with `--json` and the brightness search in
  parallel, each within its brief.
- **Sonnet / Haiku.** Not for model or firmware work in this repository; at most doc reformatting.

### 10.2 Protocol (what made the first squad work)

1. **Disjoint file ownership.** Each brief names the files the agent may create or edit; two agents
   never share a file. Shared docs (`CLAUDE.md`, `docs/log.md`, `docs/NEXT_PHASE.md`, `hardware.md`,
   `audit.md`) belong to the coordinator: agents put findings in their final report or in a new page
   of their own (`docs/x4emu.md` was one), and the coordinator merges.
2. **Shared tree, not worktrees.** `qemu/`, `images/`, `.venv/` and the firmware build are gitignored
   build products; a git worktree would not have them. Agents run side by side in the checkout with
   unique instance-name prefixes (`x4emu --name <prefix>…`) and their own scratch subdirectory.
3. **Never:** `git commit`/`push`, `tools/device.py`, writes under `images/`, edits to another agent's
   files, and nothing that rewrites the shared working tree as a whole (`git stash`, `git checkout .`,
   `git reset`, `git clean`): every other agent's uncommitted work lives there (2026-10-13: a reviewer's
   half-second stash went unnoticed only by luck). Old versions come from `git show HEAD:path` into scratch.
   A tool another agent is rewriting (session 4: `tools/x4emu`) must stay working at every
   moment; the rewriter develops beside it and switches only after its own verification.
4. **Every brief carries:** the files to read first; the facts the coordinator already knows
   (coordinates, register semantics, what was tried); deliverables; the verification the agent must run
   itself (a boot, a test run twice, `make test` if it touched shared code); a time box; the report
   format (under 350 words: what was done, what was verified, what could not be, what was left out).
5. **The coordinator closes the squad:** reviews diffs, spot-checks a claim or two (session 4: the JSON
   error path, the CI marker hazard), runs `make test` with everything together, writes the log entry.

Brief template (copy, fill the brackets):
```
You are working in /Users/mini/x4pro-emu (paths must not contain spaces; Python is .venv/bin/python).
Read CLAUDE.md, then [files]. Facts you can rely on: [coordinates, register semantics, prior attempts].
TASK: [one paragraph]. Deliverables: [numbered, with file names]. Verification (required): [commands,
"run the test twice", "make test at the end"]. Constraints: edit only [files]; instance names start
with "[prefix]"; scratch under [dir]; no commits, no tools/device.py, nothing under images/, do not
edit CLAUDE.md or docs/*.md except [own page]. Time box: [N] minutes. Final report under 350 words:
[what to include].
```

### 10.3 The remaining plan as squads (run in this order)

| Squad | Agents (model) | Acceptance |
|---|---|---|
| ~~**S1 stock-close**~~ | **done** (sessions 4–5): `--boot-hold-power`, `nvsedit` set-str/erase/redact, RTC IO overlay, the DC-offset comparator + PLL lock flag, the GT911 key byte (Home pad = Back) | met: `make test` green, no `rtcio` in `iolog_hot`, no `pll_cal` line, a redacted image boots to Home |
| ~~**S2 oracle**~~ (emulator half) | **done** (session 5): `.xic` decoded and verified, `tools/xic2png.py`, `tools/stockdev.py`, `tests/test_stock_capture.py`, `docs/xic.md` | met in the emulator: a capture equals the panel in 0 px |
| ~~**S2-device**~~ | **done** (2026-10-13): the harness (`tools/parity.py`, `docs/parity.md`) and the first device session, 7 screens, 0 unexplained pixels, the device reverted to pristine 7.6.8 | one stock screen diffed against a device capture with every difference explained |
| ~~**S3 ci-green**~~ | **done** (session 5): first Linux run green without iteration | met; keep an eye on cache hits and run time |
| ~~**S4 determinism**~~ | **done** (sessions 5–6): `--deterministic`, `record/replay`, `watch`, `shell`, the MCP server in-process | met (`tests/test_replay.py`, `tests/test_cli_polish.py`) |
| **S5 fidelity** | ~~waveform grey core · window tests · `dwc_sdmmc` trace patch~~ done (session 6); ~~the grey validation against the glass, the default flip + `gray-k`~~ done (session 7); the BUSY timing table was skipped (the device logs hold only what the defaults already are); ~~the `esp32s3.gpspi +0x38` noise~~ done (2026-10-13 review); **open**: an IO_MUX stored-value overlay (525 + 525 logger lines per stock boot) | `qemu.log` free of `dwc_sdmmc_` lines ✓; grayscale validated against the device ✓ |
| **S6 custom-firmware** (§9 D1–D4) | the `X4>` console protocol design in the custom firmware (a reading-and-design brief) · `x4emu console-expect`, `tools/x4target.py`, the `--target emu\|device` pytest · ~~the parity harness and its "explain every difference" assertions~~ done for the stock (2026-10-13, `docs/parity.md`) · `--speed` (D3; `wait-guest-ms` done in session 5) | one script runs on emulator and device and the diff is 0 or explained |
| **S8 typography** (§11) | research first, on 7.6.8 (§0.e): where the reading-font selection persists and the `.xtf` line-pitch field (firmware reading) · the two Lua capability probes (binary file reads, `g:image`) · then `tools/readingfonts.py` and a verified set | a book's Font card lists a tidy face/style/size set, every entry renders, and the choice survives a reboot |
| **S7 upstream** | ~~split the generic S3 models into espressif/qemu-style patches with cover letters~~ **prepared** (2026-10-13): `upstream-s3` (15 commits, checkpatch-clean, builds, the AES qtests pass on `-M esp32s3`) and `x4pro-on-upstream` (3 board commits, tree = x4pro up to formatting; smoke 37 passed), `docs/upstream-qemu.md`. **Open**: the owner's sign-off and approval for each step of its §8; PRs #185/#177/#181 overlap; register-level qtests per model | patches apply to `esp-develop` HEAD and pass `make test` here |

Run one squad per coordinator turn; two if their files are disjoint (S3 pairs with anything). After
each squad: `make test`, a dated `docs/log.md` entry, §0 of this file updated, and a commit left for
the owner to review unless the owner asked for commits.

## 11. Goal E — reading typography: a Kindle-like font panel (the owner's ask, 2026-09-07)

The owner's words: *"build off of how the kindle handles this. We should have a built in preview in
the settings menu where the user can adjust font face/style/size for reading."* They asked for the
plan, not the build — **do not start implementing without saying so.**

What Kindle's `Aa` sheet gives, as the target to measure against: a panel over the page; a list of
faces; a size control; a weight/style control; spacing and margins; and the page behind it
re-rendering as you change things.

### 11.1 What the device already gives us (all verified, `docs/reading-fonts.md`)

* The reader's font list is **open**: the picker walks `/sdcard/fonts` and `/sdcard/Pushed Fonts`
  for `*.xtf` and `*.bin`, no manifest, no signature, no install step, ≤ 64 entries, and the row
  label is the **file name**, truncated after about 14 characters.
* Selecting a font **re-renders the page immediately, with no restart** — and the picker is an
  overlay over the page. A crude live preview therefore already exists; it is the real page.
* There is **no font-size control anywhere**: the `.xtf` cell *is* the size. Cells 24 and 32 both
  work. So "size" is another file, not a setting — which is the single most important fact for this
  design, and also the reason the stock's book text is stuck at one cramped size.
* Reading glyphs are **1-bpp bitmaps**, one file per face × style × size. No scaling, no hinting at
  runtime, quality fixed at conversion time.
* A Lua app can own a whole screen (`docs/lua-apps.md`) but has **no reader API and no file write**.

### 11.2 Close these before designing anything

1. **Where the reading-font selection is persisted** (`docs/reading-fonts.md` §8: after a reboot the
   picker's highlight and the rendered page disagreed). A chooser that does not stick is worthless,
   and *writing* that setting is what would let anything outside the stock's own panel apply a
   choice. Ghidra on the picker's commit path, then confirm by diffing NVS / the card before and
   after a selection. **Blocking for 11.4 and 11.5.**
2. **Which `.xtf` header field drives line pitch**, and the largest cell the reader accepts. Kindle's
   panel has a spacing control; ours can only have one if leading is ours to set. Also: are the two
   header CRC-32s checked on the reading path?
3. **Two Lua capability probes** (`apps/probe/` is the vehicle): can Lua read a *binary* file
   (`ctx.data.open_text` / `read_text` across NUL bytes), and what does `g:image` actually take?
   Together they decide whether 11.4 is possible at all. Cheap: one app, one boot.
4. **7.6.8**: the device ran 7.6.8 when this was written (it runs XTOS Licorice now, where none of §11 has
   been looked at); 7.6.8's label pack has new font-picker groups (§0.e); the reading path was booted on
   neither 7.5.4 nor 7.6.8, so nothing ships until this is re-verified there.

### 11.3 Stage 1 — the set (no code, no risk, most of the value)

The picker is already a face chooser; what it lacks is *organisation*. We control both the contents
of the list and every label, so a generated, well-named set turns the stock's own panel into a
face × style × size chooser with no firmware change at all.

* `tools/readingfonts.py`: `build FACE.ttf --sizes 22,26,30 --styles regular,bold,italic`
  → one `.xtf` per combination, and `install SD.img` / a directory for the device's card.
  It wraps `xtfont.build_xtf` and needs a `CELL_PROFILE` entry per cell (the session-8 guess for 32
  was accepted by the firmware; the other cells must be derived and each one **verified by booting**,
  not assumed).
* Naming is the UI: ≤ 14 useful characters, sorted the way it should read. `Bookerly 26`,
  `Bookerly B 26`, `Charis 26`, `Charis 30`. Decide the convention with the owner *before*
  generating, because renaming later means re-copying the card.
* Budget: ≤ 64 entries total, so e.g. 4 faces × 2 styles × 4 sizes = 32 leaves room. Watch card
  space — Latin-only `.xtf` is small, a full `.bin` is 8 MiB.
* Acceptance: a book, the Font card, a tidy list; every entry renders; the owner picks by eye from
  screenshots of the same page in each.

### 11.4 Stage 2 — a real preview screen (Lua), only if 11.2.3 says yes

A **Fonts** app on the Mini Apps page that draws *the same paragraph* in every font on the card, so
faces and sizes are compared side by side instead of by flipping through the picker. This is the
"preview" half of the owner's ask, and it needs no native code.

It stands or falls on 11.2.3: the app must read the font file itself (both formats are trivially
parseable — `.bin` is a flat codepoint-indexed grid, `.xtf` is documented in `tools/xtfont.py`) and
blit glyphs. If `g:image` takes a bitmap this is easy; if the only route is a `g:line` per glyph row
it is likely too slow for a paragraph and the app should preview a **word or a line**, not a page.
Measure before designing. It cannot *apply* a choice unless 11.2.1 finds a writable setting.

### 11.5 Stage 3 — the stock's own panel, patched (the real Kindle experience)

The panel the owner actually described: face, style and size as three controls in the reader's Font
card, with the page behind it updating. The insight that makes it tractable is that **the font engine
needs no change**: size and style are already separate files, so the panel is a *grouping* problem.
Parse the labels the list builder produces (`FUN_42145bd0`, 7.2.4), group `Face [Style] NN` by face,
and present face as the list, style and size as steppers — selecting simply picks the file that the
three axes name.

* Mechanism: new native code compiled with the pioarduino Xtensa toolchain into the app slot's free
  space (an extra or extended IROM segment), plus a trampoline at the list builder — **the same machinery the
  keyboard needs (§0.b)**. Do the keyboard first or in the same squad; the segment/trampoline and
  image-header work is the expensive part and it is shared.
* Anything drawn goes through the app's own text routine; a preview strip can reuse the reading
  font's own glyphs, which is exactly what Kindle shows.
* Acceptance: from a book, one panel changes face, style and size; the page re-renders; the choice
  survives a reboot (11.2.1); the emulator test suite is the gate before the device.

### 11.6 Order, and what it costs

11.2 (research, one squad — the firmware reading and the Lua probes as two briefs) → 11.3 (one
agent, a tool plus a verified set) → 11.4 only if the probes allow → 11.5 with, or after, the
keyboard. Stages 1 and 2 touch nothing but files on the card and are reversible by deleting them;
stage 3 is a patched app image and follows the device rules in `CLAUDE.md` and §0.c.
