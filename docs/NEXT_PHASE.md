# NEXT PHASE — handoff for the next agent

Goal of the next phase, in the owner's words: run the **stock Xteink firmware** in the emulator
(it has features CrossPoint lacks, looks better, and drives the frontlight), and make the emulator
**world class**. This document is the whole context you need; the previous agent's context is gone.
Read `CLAUDE.md` first (build, CLI, device rules), then this file, then `docs/log.md` for history.

## 0. Resume here (end of session 3, 2026-09-06; its two commits are the top of `git log` on main)

1. `cd /Users/mini/x4pro-emu && make test` — 14 cases (12 CrossPoint + 2 stock), about 3 minutes.
   Needs the built QEMU (`qemu/build/qemu-system-xtensa`), the CrossPoint build
   (`firmware/.pio/build/x4pro`) and the gitignored `images/` (device dump, `sd-device.img`, ROM ELF);
   all three are present on this Mac. If `qemu/` were ever missing: `make setup && make build`
   (`qemu/.x4pro-patched` marks an applied series; the tree here carries patches 0001–0010 as commits).
2. See the stock give WiFi up: `x4emu --name stock run --flash images/stock.bin --sd images/sd-device.img`,
   wait for "deep sleep" in `x4emu --name stock status`, `press power`, `wait-text "wifi:force witi stop"
   --timeout 90`, `screenshot`. `state` shows `ana_i2c`, `saradc`, `rf.hot`, `iolog_hot`. (`images/stock.bin`
   is the dump with the device's NVS, `net_en=1`; the stock writes to it and to the SD image — regenerate
   with `tools/mkflash.py images/stock.bin --raw images/device/flash-2026-09-06-a.bin` when in doubt.)
3. Continue at §3.2 step 4 (stock screens beyond Home). The device was not touched in session 3; §1's
   device state still holds. Session 3's findings: `docs/log.md`, last entry.

## 1. Where things stand (2026-09-06)

- Repo: `/Users/mini/x4pro-emu` (symlink at `/Users/mini/xteink x4/x4pro-emu`; paths with spaces
  break QEMU/ESP-IDF). `make test` = core unit tests + fixture replay + pytest end-to-end cases
  (12 CrossPoint + 2 stock), all green. `qemu/` is a plain clone of espressif/qemu `esp-develop` @ febae182
  with branch `x4pro`; **the source of truth for our QEMU changes is `qemu-patches/`** (export with
  `cd qemu && git format-patch -o ../qemu-patches febae182..x4pro` after every QEMU commit).
- Machine `xteink-x4pro` = Espressif's `esp32s3` machine + overlays (higher MemoryRegion priority):
  USB Serial/JTAG console, full GPIO, SPI2 (CPU FIFO and GDMA paths), I2C0 (GT911 0x5D, BM8563 0x51,
  CW2017 0x63), LEDC, RTC_CNTL deep-sleep overlay, D-cache occupy/lock DONE shim, analog-master I2C
  block (0x6000E000), SENS (SAR oneshot, temperature sensor), radio register stub (FE2/FE/RX/BB/MAC with
  sticky status bits), named I/O access loggers for everything else (`x4pro/<block>` lines in qemu.log,
  32 per address, then counted into `state.iolog_hot`). Espressif files are edited only by
  the separate, upstreamable patches 0003 (USJ SOF), 0006 (interrupt matrix), 0008 (GDMA), 0009 (SPI1
  dummy cycles); everything else is overlays and new files.
- Panel: pure-C core `models/epd_core.c` (SSD1677 / UC8179 / UC8279) behind `hw/display/x4pro_epd.c`
  (SSI peripheral + bit-banged probe pins + BUSY timer + QemuConsole, qdev id `epd`).
- CrossPoint 1.6.0: boots to Home, touch, reader, battery, sleep/wake, all verified against the real
  device: probe answer, 38-command panel stream, BUSY timings, wake log, and the home screenshot
  (0 pixels different). Oracles live in `docs/device/`, `models/fixtures/`, `tests/golden/`.
- Stock `xteink_app` 7.2.4 (ESP-IDF **6.0.1**): with `user_config/net_en=0` it boots to its **home
  screen** ("Bookshelf", clock, battery), paints through IDF's `spi_master` + GDMA, lights the warm
  frontlight channel, takes touch (INT-driven GT911) and keeps polling the gauge. With WiFi enabled
  (the device's NVS as dumped) the PHY calibrates against the analog-master, SENS and radio-stub models,
  the driver reaches `wifi:mode : sta` and stops itself ~8 s later for lack of air; the UI never stalls
  (`tests/test_stock.py`, two cases). See §3.
- Device: ESP32-S3 rev v0.2, 8 MB octal PSRAM, **UC8279** panel (LUT_VER 0x68), 15.7 GB card.
  app0 = CrossPoint (EpdBus-trace build), app1 = stock 7.2.4 copy, bootloader/table/otadata untouched,
  verified double backup in `images/device/flash-2026-09-06-{a,b}.bin`. The owner, when present, can
  press buttons, enter File Transfer (card mounts on the Mac as "NO NAME"), and re-seat the pogo
  adapter; ask in plain words, one step at a time. In deep sleep the USB port vanishes; the stock app switches to a mass-storage
  personality in USB mode.

## 2. Non-negotiables

1. Device rules in `CLAUDE.md` §"The real device": never write 0x0..0x10000, never erase, never
   burn efuses; app-slot writes only via `tools/device.py flash-crosspoint` / `restore-stock`
   (they re-read the device and refuse without the verified backup).
2. The flash dump holds the owner's WiFi credentials in NVS. `images/` is gitignored; if you build
   a stock image with the dump's NVS (§3.2), keep it under `images/`. Redact SSID/BSSID/IP in any
   log you commit (`docs/device/boot-stock-7.2.4.log` shows the convention).
3. Keep the "no edits to Espressif files" discipline: overlays, new files, meson/Kconfig hooks.
   If a fix belongs in an Espressif model, make it a separate, upstreamable patch.
4. Every model change ships with a test: a core unit test, a fixture replay, or a pytest that boots
   real firmware. Every finding that contradicts a doc goes into `docs/audit.md`/`hardware.md`
   with the evidence, and `docs/log.md` gets a dated entry.

## 3. Goal A — stock firmware, fully working

### 3.1 What the stock app does in the emulator today (2026-09-06, after patches 0006..0010)

`tools/mkflash.py images/stock.bin --raw images/device/flash-2026-09-06-a.bin` (optionally
`tools/nvsedit.py images/stock.bin set-u8 user_config net_en 0` to skip the 18 s WiFi start), then
`x4emu --name stock run --flash images/stock.bin --sd images/sd-device.img --trace-epd f --trace-i2c g`:

1. ROM → IDF 6.0.1 bootloader → octal PSRAM → `app_main`. `boot-preflight` rejects a plain power-on
   (`decision=2 reason=3`) and deep-sleeps; `x4emu press power` (1.5 s hold) wakes it
   (`source=2 hold=602ms decision=0 reason=11`). The real device on a USB reset: `source=6 … auth=3`.
2. NVS loads intact (0 page programs at boot), the card mounts (CMD0…ACMD41, CMD13+CMD17 reads),
   GT911 ID/config read, RTC read, CW2017 polled every 3 s.
3. Panel: three RST pulses, VER read (`00 0F 68 00 00`) through SPI2, then the UC8279 init and the
   home paint through `spi_master` with GDMA (sequence in `docs/hardware.md`). Home at ~5 s after the
   wake; `refresh_count` 3; warm frontlight on GPIO9 at ~25 %.
4. Touch: a tap on the menu icon (`x4emu tap 88 38`) repaints; the stock reads the GT911 on its
   INT line (`state.gpio_irqs`, `gt911.frames`).
5. With `net_en=1` (the device's setting): at 14 s `wifi_init` logs appear exactly as on the device,
   PHY 711 calibrates (≈1050 analog-master reads, the temperature sensor, ≈8700 radio-block accesses),
   `wifi:mode : sta (98:c3:77:be:ea:30)` and `wifi:enable tsf` follow as on the device, then — no air —
   `W wifi:TX Q not empty: 500`, `force witi stop`, `flush txq` at +7.5 s and `Deinit lldesc rx mblock:6`
   at +17.5 s. Gauge polls, repaints and touch continue throughout. Emulator-only console line:
   `phy: error: pll_cal exceeds 2ms!!!` x6 (the RF PLL lock flag in analog block 0x62 reg 0x0c is never
   set). No blocker is known in the stock now.

`tests/test_stock.py` boots scratch copies of the dump: `net_en=0` checks 1–4 against
`tests/golden/stock-home.png` (status bar masked); `net_en=1` checks 5 (console sequence, no register
polled past 200 k accesses, gauge alive, Home intact, menu tap repaints).

### 3.2 Attack plan (in order)

1. ~~Analog-master I2C block model~~ **done** (`hw/misc/x4pro_ana_i2c.c`; protocol and evidence in
   `docs/hardware.md`, "Analog master…"; `state.ana_i2c`; `sar2-code` property).
2. ~~WiFi MAC/BB/RF loggers~~ **done as a stub**: `hw/misc/esp32s3_rfstub.c` (FE2/FE/RX/BB/MAC storage
   + sticky status bits FE +0x174.16 and MAC +0xD14.0; `sticky` property for run-time experiments);
   loggers remain for the map's holes (`state.iolog_hot`). The driver stops itself at +7.5 s. Open and
   cosmetic: answer the RF PLL lock flag (analog block 0x62 reg 0x0c, read after each write of the cap
   value to 0x62 reg 0x01) so the six `pll_cal exceeds 2ms` lines disappear.
3. ~~SAR ADC model~~ **done for SENS** (`hw/misc/esp32s3_saradc.c`: MEAS1/2 oneshot → DONE + DATA,
   TSENS always ready, storage; `sar1-data`/`sar2-data`/`tsens-out`). APB_SARADC (the DMA controller at
   0x60040000) stays a logger until a firmware uses continuous mode.
Next, in the order that serves the owner's goal (a usable stock in the emulator) best:

4. **Stock screens beyond Home** (start here). Menu (`x4emu tap 88 38` repaints it; find its items by
   tapping and diffing screenshots), All Files (the device card holds no books: build an SD image with
   an EPUB, `tests/mkepub.py` + `tools/mksd.py --src`), Settings (brightness/warmth sliders → `x4emu
   light` must follow; that is the open acceptance item), one reader page. One golden per screen under
   `tests/golden/stock-*.png` and one pytest walking them (extend `tests/test_stock.py`; mask the status
   bar as `masked_diff` does). Compare each full refresh's panel stream (`--trace-epd`,
   `tools/epdtrace.py`) with the UC8279 sequence in `docs/hardware.md`.
5. **Device oracle for a stock screen.** The stock has no screenshot function. Ask the owner to put the
   stock back on the device (`tools/device.py restore-stock --yes`: app0 ← backup; CrossPoint returns
   with `flash-crosspoint --yes`), photograph Home and one more screen, and capture a boot log with
   `tools/device.py console --reset --seconds 40`. Compare by eye (layout, glyph shapes, the status bar)
   and by timing (device `wifi:mode : sta` at 13.6 s, emulator 14.5 s). Photos go to
   `docs/device/photos/`, the log next to `boot-stock-7.2.4.log` (redact SSID/BSSID/IP).
6. **Cosmetic PHY artefact**: `phy: error: pll_cal exceeds 2ms!!!` x6. The PHY writes the RF PLL cap to
   analog block 0x62 reg 0x01 (0..0x0a) and reads reg 0x0c for a lock flag; find the expected bit
   (`tools/appdis.py` at the PC while it loops, `x4pro.ana-i2c:` lines in qemu.log) and answer it in
   `x4pro_ana_i2c.c` (a per-(block, reg) read hook). The device log has no such line.
7. `x4emu run --boot-hold-power MS`: drive GPIO3 low for the first N ms so the stock's preflight
   accepts a cold boot without the deep-sleep/wake detour (saves ~2 s and a `press power` in every
   script; `tests/test_stock.py::boot_to_home` is where it pays off).
8. **NVS shareable image**: give `tools/nvsedit.py` `set-str`/`erase` so `sta_ssid/sta_pwd/wifi_creds`
   can be blanked and a stock image without the owner's credentials can leave `images/`.
9. **RTC IO pads**: `RTC_IO_TOUCH_PAD3/14`, `XTAL_32N`, `TOUCH_PAD0..` hold/pull writes (sleep
   isolation); a stored-value overlay like SENS removes the last `x4pro/rtcio` lines.
10. **Light sleep / esp_pm**: the stock never light-slept so far (`state.sleep.light_sleeps` = 0); keep
    the plan to advance the virtual clock by the timer wake if it ever does.
11. **APB_SARADC continuous mode** (0x60040000, DMA) only when a firmware uses it (the logger will show).

Acceptance for Goal A (updated): Home renders ✓; touch navigates it ✓ (menu); WiFi fails fast instead
of hanging ✓ (`force witi stop` +7.5 s, deinit +17.5 s, UI alive; `test_stock_wifi_fails_fast`); a
pytest boots the stock and walks one screen ✓ (`tests/test_stock.py`). Open: the frontlight duty
follows its slider (step 4, Settings → `x4emu light`); a stock screen matches a device oracle (step 5).

## 4. Goal B — fidelity ("world class" model quality)

- **Waveform-level grayscale**: today a custom-LUT refresh composes 4 levels from two planes with a
  fixed table (`state.gray_approx`). Proper: interpret the uploaded LUT tables (SSD1677 0x32 105-byte
  LUT; UC81xx 0x20..0x24 × 49 bytes, `xtfAa` sets, `prebw_mid` 42-byte settle tables) as frame
  sequences per (old,new) pixel state and integrate per-pixel voltage-time into a gray value with a
  simple electrophoretic model. Validate against device photos of AA text. Keep it optional and
  slow-path.
- **Timing table**: per-mode BUSY durations from the device (`Wait complete` lines) for every
  waveform the stock uses; `full-ms/fast-ms/…` properties exist, extend per opcode value.
- **Partial-window fidelity**: PTL windows, SSD1677 windowed writes with `mirrorX/Y`, and the
  post-refresh RED/DTM1 resync are modelled; add core tests for windows at the edges and for
  data-entry modes 0..7.
- **Determinism**: `-icount 3` works; add `x4emu run --deterministic` (icount + fixed RTC seed +
  no host time) and a replay of input scripts (`x4emu script FILE` with timestamps) so a screen
  sequence is reproducible bit for bit.
- **SDMMC**: `hw/sd/dwc_sdmmc.c` prints DEBUG lines on every access (Espressif's DEBUG macro is on);
  a patch that makes it a trace event would clean qemu.log; also model card-detect and 4-bit width
  if any firmware asks.
- **USB Serial/JTAG input**: RX path exists (`OUT_RECV_PKT`); wire `x4emu console-send TEXT` so
  firmware CLIs can be driven (the stock's `boot-preflight … auth=` hints at a service console).
- **GPIO fidelity**: open-drain, per-pin pull registers from IO_MUX (today idle levels are a board
  table), RTC-domain GPIO reads for wake sources.
- **Upstream the generic models** (S3 GPIO, GP-SPI, I2C, LEDC, USB Serial/JTAG, cache DONE bits)
  to espressif/qemu; keep the board file here.

## 5. Goal C — developer experience

- `x4emu` as a package (`pipx install .`), `--json` on every command, `x4emu shell` REPL,
  `x4emu record/replay`, `x4emu watch` (live PNG refresh on each panel update).
- Optional SDL window (`configure --enable-sdl`) and a documented `-display sdl` mode; keep
  headless as the default.
- CI: GitHub Actions on Ubuntu 24.04 that builds QEMU (cached), builds CrossPoint with pioarduino
  (cached `~/.platformio`), runs `make test`, and uploads screenshots as artifacts. The clean-checkout
  path (`make setup && make build && make test`) has only been exercised on macOS; verify on Linux
  (the `Makefile` already uses `sysctl`/`nproc` fallbacks; `gtimeout` is macOS-only sugar).
- Docs site or a `docs/README.md` index; keep `CLAUDE.md` under two screens.
- Golden-image tests for every screen we can reach, each paired with a device oracle where one
  exists (the chord in CrossPoint; a photo for stock until it has a screenshot function).

## 6. Device workflow (what the owner can do for you)

- Console: `tools/device.py console --reset --seconds 40` (always `--reset`; without it the port
  may sit silent). The device auto-sleeps after idle; then the port is gone until a power press.
- Screenshots (CrossPoint): Power + lower side button writes `/screenshots/screenshot-N.bmp`;
  File Transfer mounts the card at `/Volumes/NO NAME`; `x4emu screenshot out.png --diff that.bmp`
  un-rotates the 480x800 BMP. Copy the card's files read-only with `rsync` into
  `images/device/sd-files/` and rebuild `images/sd-device.img` with `tools/mksd.py`.
- To run the stock on the device again: `tools/device.py restore-stock --yes` (app0 ← backup).
  To put CrossPoint back: `flash-crosspoint --yes` (app1 already holds the stock copy).
- Recorded oracles so far: `docs/device/boot-stock-7.2.4.log`, `boot-crosspoint-1.6.0*.log`,
  `sleep-wake-crosspoint-1.6.0.log`, `screenshots/screenshot-3824.bmp`, `efuse-*.txt`,
  `models/fixtures/uc8279-device-boot-home.log`, `images/device/sd-files/`.

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
- SdFat mounts MBR partition 1 only; `mksd.py` writes an MBR. QEMU needs power-of-two images.
- The GT911 only answers while GPIO2 is driven LOW (with GPIO1 HIGH); the model NACKs otherwise.
- The panel probe is bit-banged by CrossPoint but read through SPI2 half-duplex by the stock;
  both paths are modelled. A UC part must also answer 0xA2 (RMTP).
- The pioarduino `uv` installer was blocked by the owner's firewall once; pip pre-install works.
- zsh: quote globs that may not match (`ls x* 2>/dev/null` errors with "no matches found"), and
  variables holding commands are not word-split (use arrays or Python).
- macOS builds `qemu-system-xtensa-unsigned`; `ninja qemu-system-xtensa` runs the signing step.
- Espressif's models log only what they do not implement: `dwc_sdmmc_*` lines are `LOG_UNIMP`
  defaults, the SPI/I2C/GPIO traffic of implemented registers is silent. Turn on QEMU trace events at
  runtime through QMP (`trace-event-set-state`, e.g. `m25p80_*`, `sdcard_*`) before rebuilding anything.
- `-d exec` (`log exec` via QMP) plus a debug stop shows the translation block that touched a bad
  address; `get_pc()` in the memory path reports the block start, not the instruction.
- FreeRTOS TCB walk (docs/log.md 2026-09-06) tells you what every task waits on without symbols;
  ROM symbols come from `images/rom/esp32s3_rev0_rom.nm`; the app is disassembled with objdump on the
  segments parsed from the ESP image header (esptool's "File offs" is the segment header, data is +8).
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
- `x4emu --name` becomes a directory under `.x4emu/` and part of two UNIX socket paths: no `=` in it
  (QEMU's `-qmp unix:PATH` option parser splits on it) and keep it short (macOS caps socket paths at
  104 bytes; pytest names come from the test function plus the parametrize id).

## 8. Definition of "world class" (checklist)

- [x] Stock firmware: boots, renders, navigates, frontlight observable (WiFi off in NVS).
- [x] Stock firmware: WiFi start fails fast (analog-master I2C block, SENS, radio stub; `tests/test_stock.py`).
- [ ] Stock firmware: one screen matched to a device photo.
- [ ] CrossPoint: every activity reachable by script has a golden and a device oracle.
- [ ] Deterministic replay of an input script yields identical screenshots run to run.
- [ ] Waveform-aware grayscale validated against device photos.
- [ ] `make setup && make build && make test` verified on Ubuntu 24.04 in CI and on macOS.
- [ ] `x4emu` installable, JSON everywhere, documented in one page.
- [ ] Generic S3 models proposed upstream; board file and panel cores stay here.
- [ ] `docs/audit.md`/`hardware.md` still the single source of truth, every claim with evidence.

## 9. Goal D — agent ergonomics, as milestones (the agent iterates on firmware through these)

Done in this phase: the MCP server (28+ tools, screenshots inline), `make run` (build → image →
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

**D3. Time control.** `x4emu run --speed N` (icount shift) and `x4emu wait-guest-ms N`
(virtual-clock wait, not host sleep) for time-driven UI: auto-sleep, toasts, battery polls.
Acceptance: a test triggers CrossPoint's inactivity auto-sleep in a few host seconds.

**D4. Parity harness.** The same input script runs on emulator and device (D1), screenshots are
diffed pairwise, and every difference is either 0 or explained in the test's assertion message
(battery %, clock). Acceptance: the M3/M5 device comparisons become an automated job the owner
starts by plugging in the device.

**D5. Fidelity gaps that block custom firmware first.** Analog-master I2C block, WiFi "fail fast" and
the SENS/SAR oneshot are done (§3.2 steps 1–3); next RMT/I2S if the firmware uses them, APB_SARADC
continuous mode if a firmware samples with DMA, then USB OTG device mode (large, optional).

What the emulator guarantees a custom firmware today (design against this list): USB Serial/JTAG
console both ways; GPIO 0..48 with edge/level interrupts; SPI2 to the panel, CPU FIFO or GDMA
(ESP-IDF `spi_master`, interrupt-driven, re-routing in the interrupt matrix works); UC8279/UC8179/
SSD1677 with the real probe answers and device timings; I2C0 with GT911, BM8563, CW2017 (BATINFO
resident); LEDC frontlight; SDMMC 1-bit card (MBR/FAT32 image); NVS reads and writes through the
QIO flash path; deep sleep with EXT1 wake on GPIO3; efuse/MAC of the desk unit; a WiFi start that
initialises (PHY calibration against the analog-master, SENS and radio-stub models) and then fails the
way ESP-IDF fails without air, so a firmware's WiFi path runs and errors instead of hanging; SAR ADC
oneshots and the temperature sensor with fixed values. Not there: a radio, BLE (untested), USB OTG,
APB_SARADC DMA mode, RMT, I2S, touch sensor pads, ULP.
