# NEXT PHASE — handoff for the next agent

Goal of the next phase, in the owner's words: run the **stock Xteink firmware** in the emulator
(it has features CrossPoint lacks, looks better, and drives the frontlight), and make the emulator
**world class**. This document is the whole context you need; the previous agent's context is gone.
Read `CLAUDE.md` first (build, CLI, device rules), then this file, then `docs/log.md` for history.

## 0. Resume here (end of session 6, 2026-09-07)

**Work is organised as squads: read §10 before starting anything.** Sessions 5 and 6 ran the protocol with
four to five agents in parallel; session 6 balanced cost as the owner asked (one Fable for the model that
had to be inferred, Opus for the C work, Sonnet for the Python polish against a precise spec) — see the
session-6 entry of `docs/log.md` for what each tier delivered.

1. `cd /Users/mini/x4pro-emu && make test` — 44 cases (13 CrossPoint, 4 interface, 8 stock, 19 fast), 6 min 53 s, green at the end of
   session 6 (one stock tap per full-suite run is read by the firmware and ignored — see the log; the stock
   tests retry such a tap once). Needs the built QEMU
   (`qemu/build/qemu-system-xtensa`, patches 0001–0017), the CrossPoint build and the gitignored `images/`;
   all present on this Mac. If `qemu/` were ever missing: `make setup && make build`. After exporting a
   patch, `touch qemu/.x4pro-patched`. `qemu/build-s1/` and `qemu/build-s2/` are agents' complete build
   directories (`X4EMU_QEMU=/Users/mini/x4pro-emu/qemu/build-sN/qemu-system-xtensa`; `tests/conftest.py`
   honours the variable): the pattern for QEMU work beside other agents — rebuild before use, or delete.
2. **Session 6 in one line:** a waveform-level grey model (UC8279 LUT interpreter + per-pixel reflectance,
   `docs/grayscale.md`, off by default behind `waveform-gray`) that finds CrossPoint's anti-aliased text is
   three-level on this panel because its AA bank has BW == WB; the SDMMC log noise gone (patch 0016, an
   Espressif file: 484,963 → 1,788 lines per stock boot); 28 window/addressing tests that found and then
   fixed three SSD1677 addressing gaps; `x4emu shell`, `x4emu watch`, the MCP server in-process with
   record/replay. Everything committed on `main`; the owner pushes.
3. **Open and the owner's to answer:** which of the two grey renderings matches the glass (the side-by-side
   crops in the session-6 log; a photo of a book page on the device would settle `gray-k` too). If the
   waveform model wins: set `waveform_gray` default true in `epd_core_init`, regenerate
   `tests/golden/reader-page*.png`, tick the §8 item. If the old table wins: keep the default, record why.
4. Carried over: **the stock ignores a tap now and then** — `x4emu tap` reports "read by the firmware after
   0.03s" and no repaint follows (twice in three full-suite runs, always the first or second tap after Home,
   never when the test runs alone; the tests retry). Unknown whether the device does the same: a question for
   the parity harness (S6) — if the device never drops one, the emulator's timing around the pre-sent old
   plane is suspect. The 4-level grey `.xic` (never observed); the QMP `pmemsave` zero pages (pause first); the
   device oracle for a pixel-exact stock capture (optional, §0.3 of session 5 — needs the patched stock on
   the device); D1–D4 (§9); the `esp32s3.gpspi +0x38` noise and an IO_MUX overlay (§10.3 S5 row).
5. Next squads, in order (§10.3): S6 custom firmware (D1–D4) if the owner plans to write firmware, else S7
   upstream; either way the grey default flip first once the owner answers.

## 1. Where things stand (2026-09-06)

- Repo: `/Users/mini/x4pro-emu` (symlink at `/Users/mini/xteink x4/x4pro-emu`; paths with spaces
  break QEMU/ESP-IDF). `make test` = core unit tests + fixture replay + the pytest suite (CrossPoint, stock,
  record/replay, `.xic`, `stockdev`, `nvsedit`), all green. `qemu/` is a plain clone of espressif/qemu `esp-develop` @ febae182
  with branch `x4pro`; **the source of truth for our QEMU changes is `qemu-patches/`** (export with
  `cd qemu && git format-patch -o ../qemu-patches febae182..x4pro` after every QEMU commit).
- Machine `xteink-x4pro` = Espressif's `esp32s3` machine + overlays (higher MemoryRegion priority):
  USB Serial/JTAG console, full GPIO, SPI2 (CPU FIFO and GDMA paths), I2C0 (GT911 0x5D, BM8563 0x51,
  CW2017 0x63), LEDC, RTC_CNTL deep-sleep overlay, D-cache occupy/lock DONE shim, analog-master I2C
  block (0x6000E000; the RF PLL lock flag and the PHY's DC-offset comparator read as done), SENS (SAR oneshot,
  temperature sensor), radio register stub (FE2/FE/RX/BB/MAC with sticky status bits), RTC IO stored-value
  overlay (0x60008400), named I/O access loggers for everything else (`x4pro/<block>` lines in qemu.log,
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
  the driver reaches `wifi:mode : sta` (after the full PHY calibration on a cold boot, or after a wake) and
  stops itself ~8 s later for lack of air; the UI never stalls
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

### 3.1 What the stock app does in the emulator today (2026-09-06, after patches 0006..0015)

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
   INT line (`state.gpio_irqs`, `gt911.frames`). Nav menu entries at landscape x 160/245/330/415/500,
   y ≈ 150 (Read, All Files, USB Mode, Cloud Sync, Settings); All Files first row (250, 320); reader
   page centre (400, 240) opens the reading menu, its back arrow is (80, 445); Right/Left page.
   60 s after the last input the frontlight fades out (`state.ledc` `fading` → warm 0), the next touch
   fades it back; the status-bar clock repaints every minute.
5. With `net_en=1` (the device's setting): at 14 s `wifi_init` logs appear exactly as on the device,
   PHY 711 calibrates (≈1050 analog-master reads, the temperature sensor, ≈8700 radio-block accesses),
   `wifi:mode : sta (98:c3:77:be:ea:30)` and `wifi:enable tsf` follow as on the device, then — no air —
   `W wifi:TX Q not empty: 500`, `force witi stop`, `flush txq` at +7.5 s and `Deinit lldesc rx mblock:6`
   at +17.5 s. Gauge polls, repaints and touch continue throughout. No emulator-only console line remains:
   the `pll_cal exceeds 2ms` lines went with the lock flag (0x62/0x07 bit 1, session 5) and a cold boot runs
   the full PHY calibration through the DC-offset comparator at 0x6000E04C. The Home pad is Back, one level
   (`x4emu home`). No blocker is known in the stock now.

`tests/test_stock.py` boots scratch copies of the dump with `tests/mkepub.py`'s book on the card:
`net_en=0` checks 1–4 against `tests/golden/stock-home.png` (status bar masked); `net_en=1` checks 5
(console sequence, no register polled past 200 k accesses, gauge alive, Home intact, menu tap repaints);
`test_stock_idle_dims_frontlight_and_keeps_ticking` (the 60 s fade, tick alive, tap after it);
`test_stock_screens_walk` (menu, All Files, page 1/2, reading menu, bookshelf with the book, Settings);
`test_stock_home_pad_acts_as_back`, `test_stock_rtc_pinned_by_base_epoch`,
`test_stock_light_controls_follow_the_sliders`; `tests/test_stock_capture.py` boots a developer-patched copy
(`tools/stockdev.py`) and requires its Screen Capture to match the panel outside the status bar.

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

4. ~~**Stock screens beyond Home**~~ **done** (session 4: menu / All Files / reader / reading menu /
   Settings in `test_stock_screens_walk`; the light pull-down with brightness, colour temperature and
   the Light button in `test_stock_light_controls_follow_the_sliders`, `x4emu light` and NVS follow). Menu (`x4emu tap 88 38` repaints it; find its items by
   tapping and diffing screenshots), All Files (the device card holds no books: build an SD image with
   an EPUB, `tests/mkepub.py` + `tools/mksd.py --src`), Settings (brightness/warmth sliders → `x4emu
   light` must follow; that is the open acceptance item), one reader page. One golden per screen under
   `tests/golden/stock-*.png` and one pytest walking them (extend `tests/test_stock.py`; mask the status
   bar as `masked_diff` does). Compare each full refresh's panel stream (`--trace-epd`,
   `tools/epdtrace.py`) with the UC8279 sequence in `docs/hardware.md`.
5. **Device oracle for a stock screen** — emulator half **done** (session 5: the `.xic` container is decoded
   and a capture matches the panel, `docs/xic.md`), device half open and the owner's call (§0.3): Screen
   Capture is compiled out of 7.2.4, so either the developer-patched stock goes into an app slot
   (`tools/stockdev.py`, a guarded `tools/device.py` write) and its capture is diffed with
   `tools/xic2png.py --diff`, or Home and one more screen are photographed and compared by eye. Either way
   also capture a boot log with `tools/device.py console --reset --seconds 40` and compare timing (device
   `wifi:mode : sta` at 13.6 s, emulator 13.3 s cold). Photos go to `docs/device/photos/`, the log next to
   `boot-stock-7.2.4.log` (redact SSID/BSSID/IP).
6. ~~**PHY on a cold boot**~~ **done** (session 5, patch 0013): the block was not the PLL but the PHY's
   DC-offset comparator at 0x6000E04C (bit 1 start, bit 24 done polled with no timeout, bits 31:30 comparator
   outputs; `cal-cmp` property), and the `pll_cal` lines came from the lock poll on analog 0x62 reg 0x07
   bit 1 (`ana_answered[]`), not reg 0x0c. `test_stock_wifi_fails_fast` boots cold, sees "Saving new
   calibration data" and asserts no `pll_cal` line.
7. ~~`x4emu run --boot-hold-power MS`~~ **done** (S1): every stock test boots cold; `boot_to_home(name,
   wake=True)` keeps the deep-sleep/wake path for whoever needs it.
8. ~~**NVS shareable image**~~ **done** (S1): `tools/nvsedit.py set-str / erase / redact`, slots blanked,
   `tests/test_nvsedit.py`; recipe in the module docstring.
9. ~~**RTC IO pads**~~ **done** (session 5, patch 0015): `x4pro.rtcio` stored-value overlay at 0x60008400,
   `state.rtcio`; no `x4pro/rtcio` line after a stock boot or a CrossPoint sleep/wake (asserted in both).
10. **Light sleep / esp_pm**: the stock never light-slept so far (`state.sleep.light_sleeps` = 0); keep
    the plan to advance the virtual clock by the timer wake if it ever does.
11. **APB_SARADC continuous mode** (0x60040000, DMA) only when a firmware uses it (the logger will show).

Acceptance for Goal A (updated, session 4): Home renders ✓; touch navigates it ✓ (menu, All Files,
reader, reading menu, Settings); WiFi fails fast instead of hanging ✓ (`test_stock_wifi_fails_fast`);
pytests boot the stock and walk its screens ✓; the frontlight duty follows its sliders ✓
(`test_stock_light_controls_follow_the_sliders`); the 60 s auto-dim no longer freezes the emulator ✓.
Open: a stock screen matches a *device* oracle (step 5 / squad S2-device; the emulator side is closed by
`tests/test_stock_capture.py`).

## 4. Goal B — fidelity ("world class" model quality)

- ~~**Waveform-level grayscale**~~ **built** (session 6, behind a flag): the UC8279 LUT interpreter and a
  per-pixel reflectance model live in `models/epd_core.c` (`docs/grayscale.md`); `waveform-gray` / `gray-k`
  properties on `x4pro.epd`, `state.gray_model`. Finding: CrossPoint's AA bank has BW == WB, so its text is
  three-level on this panel; the old table drew the two greys the wrong way round. **Open:** the owner's
  verdict (a photo of a book page, or the side-by-side crops) decides the default and `gray-k`; the 150 Hz
  frame period and ghosting in OTP DUs are not modelled; SSD1677's 105-byte LUT keeps the approximation.
- **Timing table**: per-mode BUSY durations from the device (`Wait complete` lines) for every
  waveform the stock uses; `full-ms/fast-ms/…` properties exist, extend per opcode value.
- **Partial-window fidelity**: ~~add core tests~~ done (session 6, `models/tests/test_windows.c`: 28 cases,
  115 checks). Open: the three SSD1677 gaps the tests record as XFAIL — X-decrement windows collapse to one
  column, the AM bit of 0x11 is ignored, the gate-scan mirror byte is ignored (unreachable by CrossPoint or
  the stock as configured; an Opus fix flips the XFAILs). The drivers send the RED/DTM1 resync themselves;
  the core does not copy planes at refresh.
- ~~**Determinism**~~ **done** (session 5, S4): `x4emu run --deterministic` (`-icount 3` + the RTC pinned by
  `base-epoch`), `x4emu record` / `replay` journals with guest timestamps, `tests/test_replay.py` (two
  replays of one journal, 0 px apart). Reproduced to the pixel, not to the microsecond: the replay's waits
  are host-paced polls of the guest clock; QEMU's own `rr=record` mode would be the bit-exact step if ever
  needed.
- ~~**SDMMC**~~ done (session 6, patch 0016): the ESP32-specific SDMMC_CLOCK and the other configuration
  registers are stored, CDETECT/WRTPRT come from the SD bus, DEBUG became `dwc_sdmmc_*` trace events; a
  stock boot's qemu.log went from 484,963 to 1,788 lines. Next noisiest: `esp32s3.gpspi unhandled read
  +0x38` (SPI_DMA_INT_CLR, 608 per stock boot) and the `x4pro/iomux` logger (525 reads + 525 writes).
- **USB Serial/JTAG input**: RX path exists (`OUT_RECV_PKT`); wire `x4emu console-send TEXT` so
  firmware CLIs can be driven (the stock's `boot-preflight … auth=` hints at a service console).
- **GPIO fidelity**: open-drain, per-pin pull registers from IO_MUX (today idle levels are a board
  table), RTC-domain GPIO reads for wake sources.
- **Upstream the generic models** (S3 GPIO, GP-SPI, I2C, LEDC, USB Serial/JTAG, cache DONE bits)
  to espressif/qemu; keep the board file here.

## 5. Goal C — developer experience

- ~~`x4emu` as a package (`pipx install .`), `--json` on every command~~ done (session 4: `x4emu/` package,
  `tools/x4emu` shim, `docs/x4emu.md`); `record/replay` done (session 5); `x4emu shell`, `x4emu watch` (HTTP live view) and the MCP server
  importing the package with `emu_record`/`emu_replay`/`emu_wait_guest_ms` done (session 6).
- Optional SDL window (`configure --enable-sdl`) and a documented `-display sdl` mode; keep
  headless as the default.
- ~~CI~~ **green** (session 5): the first Linux run of `.github/workflows/ci.yml` passed on b7f5918 in
  12 min (`make build` 9 min from scratch, `make test` 86 s, a 178 KB artifact of screenshots and logs).
  The owner pushes (GitHub Desktop; no push credential on the Mac). Watch the QEMU/PlatformIO caches on the
  next runs and the run time once the stock-free suite grows.
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
  CrossPoint also polls touch only between rendering passes that take seconds ("Time = 3494 ms from
  clearScreen to displayBuffer"), which `wait-quiet` cannot see: a 120 ms tap into such a pass was the
  session-4 flake in three different tests. `tap`/`home` now stay asserted until the firmware reads the
  GT911 frame (`state.gt911.clears`); firmware measures a tap from its own first read, so it stays short.
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
  Files, reader; session 5). A pixel-exact `.xic` device capture stays optional (needs the developer-patched
  stock on the device); the emulator side of that is proven (`tests/test_stock_capture.py`).
- [ ] CrossPoint: every activity reachable by script has a golden and a device oracle.
- [x] Deterministic replay of an input script yields identical screenshots run to run (`tests/test_replay.py`, session 5).
- [ ] Waveform-aware grayscale validated against the device (built in session 6 behind `waveform-gray`; the
  owner's photo or verdict on the crops decides the default).
- [x] `make setup && make build && make test` verified on Ubuntu 24.04 in CI (first run green, session 5) and on macOS.
- [x] `x4emu` installable, JSON everywhere, documented in one page (`docs/x4emu.md`, session 4).
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

**D3. Time control.** `x4emu run --speed N` (icount shift; `--deterministic` = shift 3 exists) and
~~`x4emu wait-guest-ms N`~~ (done, session 5: virtual-clock wait, not host sleep) for time-driven UI:
auto-sleep, toasts, battery polls.
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

## 10. How the work is run from here: squads

The owner's instruction (session 4): the session runs on Fable 5.1 as the **coordinator** and forms
**squads** of agents per milestone, delegating to lower models where the coordinator deems it
appropriate. Session 4 ran the first squad (CI, packaging, brightness) this way; the protocol below is
what worked, and the remaining plan is cut into squads in the order to run them.

### 10.1 Model tiers (who does what)

- **Coordinator, Fable 5.1 (this session).** Reads `CLAUDE.md`, §0 and the last `docs/log.md` entry,
  picks the next squad, writes each agent's brief, reviews every diff, runs the final `make test`,
  writes the log entry and updates this file and `CLAUDE.md`. Keeps for itself, or gives to a Fable
  subagent: anything that reads the stock app without symbols (`.xic`, the GT911 key byte, the PLL
  flag, any "spins forever"), any new peripheral model whose behaviour must be inferred (APB_SARADC
  DMA, RMT, I2S, USB OTG), Espressif-model bugs, waveform grayscale, the debug-console protocol and
  parity-harness design, and any task whose acceptance test does not yet exist.
- **Opus 5 agents.** Well-specified work with a mechanical acceptance check: CI iteration, packaging
  and CLI features (`record/replay`, `watch`, `shell`, JSON shapes), tests and goldens for behaviour the
  coordinator has already seen, UI hunts with a coordinate map, `nvsedit` features, stored-value
  register overlays (RTC IO), timing tables from device logs, docs pages, upstream patch preparation.
  Session 4 evidence: three Opus agents delivered CI, the package with `--json` (16/16 after the
  switch) and the brightness search in parallel, each within its brief.
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
   files. A tool another agent is rewriting (session 4: `tools/x4emu`) must stay working at every
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
| **S2-device** (optional) | owner + coordinator, only if a pixel-exact capture is wanted: the developer-patched stock into an app slot through a guarded `tools/device.py` path, one capture copied off the card, `xic2png.py --diff` against the emulator's screen (§0.3). The owner already judged the screens 1:1 by eye (session 5) | one stock screen diffed against a device capture with every difference explained |
| ~~**S3 ci-green**~~ | **done** (session 5): first Linux run green without iteration | met; keep an eye on cache hits and run time |
| ~~**S4 determinism**~~ | **done** (sessions 5–6): `--deterministic`, `record/replay`, `watch`, `shell`, the MCP server in-process | met (`tests/test_replay.py`, `tests/test_cli_polish.py`) |
| **S5 fidelity** | ~~Fable: waveform grey core · Opus: window tests · Opus: `dwc_sdmmc` trace patch~~ done (session 6); the BUSY timing table was skipped (the device logs hold only what the defaults already are); **open**: the grey validation against the glass (owner) and then the default flip + `gray-k`; a follow-up Opus item: the `esp32s3.gpspi +0x38` (SPI_DMA_INT_CLR) unhandled-read noise and an IO_MUX stored-value overlay (525 + 525 logger lines per stock boot) | `qemu.log` free of `dwc_sdmmc_` lines ✓; grayscale validated against the device ☐ |
| **S6 custom-firmware** (§9 D1–D4) | **Fable**: the `X4>` console protocol design in the custom firmware · Opus: `x4emu console-expect`, `tools/x4target.py`, the `--target emu\|device` pytest · **Fable**: the parity harness and its "explain every difference" assertions · Opus: `--speed` (D3; `wait-guest-ms` done in session 5) | one script runs on emulator and device and the diff is 0 or explained |
| **S7 upstream** | Opus: split the generic S3 models (GPIO, GP-SPI, I2C, LEDC, USJ, cache DONE, intmatrix/GDMA/SPI fixes) into espressif/qemu-style patches with tests and cover letters · **Fable**: review before submission | patches apply to `esp-develop` HEAD and pass `make test` here |

Run one squad per coordinator turn; two if their files are disjoint (S3 pairs with anything). After
each squad: `make test`, a dated `docs/log.md` entry, §0 of this file updated, and a commit left for
the owner to review unless the owner asked for commits.
