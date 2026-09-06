# NEXT PHASE — handoff for the next agent

Goal of the next phase, in the owner's words: run the **stock Xteink firmware** in the emulator
(it has features CrossPoint lacks, looks better, and drives the frontlight), and make the emulator
**world class**. This document is the whole context you need; the previous agent's context is gone.
Read `CLAUDE.md` first (build, CLI, device rules), then this file, then `docs/log.md` for history.

## 1. Where things stand (2026-09-06)

- Repo: `/Users/mini/x4pro-emu` (symlink at `/Users/mini/xteink x4/x4pro-emu`; paths with spaces
  break QEMU/ESP-IDF). 12 commits. `make test` = core unit tests + fixture replay + 12 pytest
  end-to-end cases, all green. `qemu/` is a plain clone of espressif/qemu `esp-develop` @ febae182
  with branch `x4pro`; **the source of truth for our QEMU changes is `qemu-patches/`** (export with
  `cd qemu && git format-patch -o ../qemu-patches febae182..x4pro` after every QEMU commit).
- Machine `xteink-x4pro` = Espressif's `esp32s3` machine + overlays (higher MemoryRegion priority,
  no edits to Espressif files beyond meson lines): USB Serial/JTAG console, full GPIO, SPI2, I2C0
  (GT911 0x5D, BM8563 0x51, CW2017 0x63), LEDC, RTC_CNTL deep-sleep overlay, D-cache occupy/lock
  DONE shim, named I/O access loggers for everything else (`x4pro/<block>` lines in qemu.log).
- Panel: pure-C core `models/epd_core.c` (SSD1677 / UC8179 / UC8279) behind `hw/display/x4pro_epd.c`
  (SSI peripheral + bit-banged probe pins + BUSY timer + QemuConsole, qdev id `epd`).
- CrossPoint 1.6.0: boots to Home, touch, reader, battery, sleep/wake, all verified against the real
  device: probe answer, 38-command panel stream, BUSY timings, wake log, and the home screenshot
  (0 pixels different). Oracles live in `docs/device/`, `models/fixtures/`, `tests/golden/`.
- Stock `xteink_app` 7.2.4 (ESP-IDF **6.0.1**): boots to `app_main`, passes its boot-preflight after a
  power-button wake, initialises tasks/SD host, reads GT911 ID+config, RTC, polls CW2017, probes the
  panel, then idles forever. See §3.
- Device: ESP32-S3 rev v0.2, 8 MB octal PSRAM, **UC8279** panel (LUT_VER 0x68), 15.7 GB card.
  app0 = CrossPoint (EpdBus-trace build), app1 = stock 7.2.4 copy, bootloader/table/otadata untouched,
  verified double backup in `images/device/flash-2026-09-06-{a,b}.bin`. The owner is at the desk and
  can press buttons, enter File Transfer (card mounts on the Mac as "NO NAME"), and re-seat the pogo
  adapter. In deep sleep the USB port vanishes; the stock app switches to a mass-storage
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

### 3.1 What the stock app does in the emulator today

`x4emu --name stock run --flash images/stock.bin --sd images/sd-device.img --trace-i2c f --trace-epd g`
(`images/stock.bin` = the raw 16 MB dump; regenerate with
`tools/mkflash.py images/stock.bin --raw images/device/flash-2026-09-06-a.bin`):

1. ROM → IDF 6.0.1 bootloader → octal PSRAM → `cpu_start: Multicore app` → (cache-occupy shim) →
   `app_main`.
2. `boot-preflight: source=1 … hold=0ms decision=2 reason=3` → **rejects a plain power-on** and deep
   sleeps (`wake_on=next_press`). The stock wants the power button held ~600 ms to boot.
   `x4emu press power` (auto-extended 1.5 s hold) wakes it: `rst:0x5 (DSLEEP)`,
   `boot-preflight: source=2 … hold=602ms decision=0 reason=11`, and it proceeds. (Real device on a
   USB reset: `source=6 … decision=0 reason=9 auth=3`.)
3. Memory init, tasks (`PowerMonitor`, `xteink_critical`, `xteink_nvs`), `SD_HOST` init, GT911
   0x8140/0x8144/0x8047… reads, RTC time read, CW2017 VCELL/SOC polled every few seconds, panel RST
   pulsed three times, VER read through SPI2 half-duplex (answered `00 0F 68 00 00`), then
   `main_task: Returned from app_main()` and **both CPUs idle**. No panel init, no WiFi (device
   starts WiFi at 13 s). Registers it touched that nothing models (from qemu.log):

   | Address | Register | Count |
   |---|---|---|
   | 0x60008904 | `SENS_SAR_PERI_CLK_GATE_CONF_REG` | 36 r/w |
   | 0x6000880C | `SENS_SAR_MEAS1_CTRL2_REG` (bit 17 MEAS1_START_SAR, bit 16 MEAS1_DONE_SAR, bits 0..15 MEAS1_DATA_SAR) | 8 r/w |
   | 0x60008810 | `SENS_SAR_MEAS1_MUX_REG` | 4 r/w |
   | 0x60040000 / 04 | `APB_SARADC_CTRL_REG` / `CTRL2_REG` | 14 / 8 |
   | 0x60040018 / 28 | `APB_SARADC_SAR1_PATT_TAB1_REG` / `SAR2_PATT_TAB1_REG` | 6 / 6 |
   | 0x60040070 | `APB_SARADC_APB_ADC_CLKM_CONF_REG` | 16 |
   | 0x60008490 / BC / C4 | `RTC_IO_TOUCH_PAD3_REG` (GPIO3) / `TOUCH_PAD14_REG` (GPIO14) / `XTAL_32N_PAD_REG` | 8 / 5 / 4 |

   Headers: `docs/device/idf-headers/{sens_reg.h,apb_saradc_reg.h,rtc_io_reg.h}` (fetch the
   `release/v6.0` versions of anything else you need; the stock is IDF 6.0.1, CrossPoint is 5.5.5).

### 3.2 Attack plan (in order)

1. **SAR ADC model** (`hw/misc/esp32s3_saradc.c`, overlay at SENS 0x60008800/0x400 and
   APB_SARADC 0x60040000/0x1000 — remove them from the iolog list in `xteink_x4pro.c`). Minimum:
   oneshot via SENS: when `MEAS1_START_SAR` is written, set `MEAS1_DONE_SAR` and put a value in
   `MEAS1_DATA_SAR` (12-bit; expose a QOM property `adc-mv` or per-channel values on the board
   object); ADC2 (`MEAS2_*`) the same; `APB_SARADC_INT_RAW.ADC1_DONE` if the continuous path is
   used. The X4 Pro profile says `batteryAdc` is unassigned, so this is probably the stock's
   vestigial ladder or a VBUS sense; return mid-scale and see what changes. Re-run §3.1; if the app
   still idles, go to step 2.
2. **Find the blocked task with gdb**: `x4emu --name stock run … --gdb` then `x4emu --name stock gdb`
   (xtensa-esp-elf-gdb 17.1 from `~/.platformio/packages/tool-xtensa-esp-elf-gdb/bin/`). No ELF for
   the stock app exists, but: ROM symbols come from Espressif's `esp-rom-elfs` release
   (`esp32s3_rev0_rom.elf`, `xtensa-esp-elf-nm -n`; the `.rom.ld` files only cover exported APIs),
   the support doc lists stock IROM addresses (`writeCommand 0x4201a2d4`, `init 0x4201a568`, …,
   `mountSD`, `Cw2017PowerHal`), and the FreeRTOS task list is walkable from `pxCurrentTCB`/
   `pxReadyTasksLists`. Alternative without gdb: log every peripheral read that returns 0 in a loop
   (the `iolog` overlays already do this) and look at the last ~200 qemu.log lines when it goes idle.
3. **RTC IO pads**: stock writes `RTC_IO_TOUCH_PAD3/14` and `XTAL_32N` (hold/pull config for deep
   sleep). A stored-value overlay (like the cache shim) removes them from the unknowns.
4. **Light sleep / esp_pm**: IDF 6 apps often enable automatic light sleep; the sleep overlay returns
   at once from light sleep (`x4pro_sleep.c`, `light_sleeps` counter in `state`). If the stock spends
   its time there, wall-clock stops advancing for it; make light sleep advance the virtual clock by the
   requested timer wake (`RTC_CNTL_SLP_TIMER0/1`, `WAKEUP_ENA` timer bit) instead of returning immediately.
5. **Stock panel driver**: once it initialises the panel, compare its command stream
   (`--trace-epd`) with the support doc's recovered sequences (`INIT 0x4201a568`, FULL 0xF7, FAST
   0xFC/0xC7, the UC8279 `xtfAa` / `prebw_mid` LUT sets, `UC8279_gray_aa`, `UC8279_gray_full`,
   `UC8279_aa_prebw_mid`). Add any unknown opcode to `models/epd_core.c` (the core counts
   `epd_unknown_cmds` and remembers the last one). First-pixel acceptance: a stock screen in
   `tests/golden/stock-*.png` reproduced from a device screenshot when the stock has one, else
   compared against a photo the owner takes.
6. **Stock input**: its `GT911Driver` may rely on the INT line (GPIO10) rather than polling; the
   model drives INT low while a frame is pending and re-frames every 10 ms while a finger is down
   (`x4pro_i2c_devs.c`). Verify with `x4emu tap` on stock screens; check `state.gt911.frames/clears`.
7. **Frontlight ("backlight")**: stock drives LEDC channels 4 (GPIO8 cool) and 5 (GPIO9 warm) at
   25 kHz, 10-bit (support doc), NVS `user_config/lightBri=100`, `lightCT=100`. `x4emu light -v`
   already maps channels to pins through the GPIO matrix. World-class: render the light as a tint
   overlay in `screenshot --light` and expose it in `state`.
8. **NVS**: build the stock image on top of the dump so `hw_calib/screenType=2`, `user_config` and
   the phy calibration blobs are present (`mkflash.py --base images/device/flash-…-a.bin --raw …`
   or just the raw dump), but for a shareable image write a tool that blanks `sta_ssid/sta_pwd/
   wifi_creds` (NVS page/entry format is decoded in `docs/device/partitions.md`; an NVS
   editor lives in ESP-IDF `components/nvs_flash/nvs_partition_tool`). Consider setting
   `user_config/net_en=0` so the stock does not start WiFi in the emulator.
9. **WiFi/BLE**: no radio model exists and none is planned; make sure the stock fails fast rather
   than hanging (watch for accesses at the WiFi MAC/BB/RF blocks 0x60033000/0x60035000/0x6001C000
   in qemu.log; add `iolog` overlays there first).
10. **USB mass storage (File Transfer)**: the ESP32-S3 USB OTG is a Synopsys DWC2 core at 0x60080000.
    QEMU has `hw/usb/hcd-dwc2.c` (host mode, Raspberry Pi) but no DWC2 *device* mode. Research
    item, large: implement DWC2 device mode enough for TinyUSB CDC+MSC and back it with a QEMU
    usb-storage/host-side viewer, or accept "not emulated" and document. The stock and CrossPoint
    both use it only from their File Transfer screens.
11. Add `x4emu run --stock` (or `--boot-hold-power MS`): drive GPIO3 low for the first N ms so the
    stock's preflight accepts a cold boot without the sleep/wake detour (input device property,
    applied before the first reset).

Acceptance for Goal A: the stock home screen renders; touch navigates it; the frontlight duty
follows its brightness slider; a stock screenshot equals a device oracle (photo or BMP); a pytest
boots the stock image and walks one screen; `docs/log.md` explains the blocker that was found.

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

## 8. Definition of "world class" (checklist)

- [ ] Stock firmware: boots, renders, navigates, frontlight observable, one screen matched to the device.
- [ ] CrossPoint: every activity reachable by script has a golden and a device oracle.
- [ ] Deterministic replay of an input script yields identical screenshots run to run.
- [ ] Waveform-aware grayscale validated against device photos.
- [ ] `make setup && make build && make test` verified on Ubuntu 24.04 in CI and on macOS.
- [ ] `x4emu` installable, JSON everywhere, documented in one page.
- [ ] Generic S3 models proposed upstream; board file and panel cores stay here.
- [ ] `docs/audit.md`/`hardware.md` still the single source of truth, every claim with evidence.

## 9. Goal D — agent ergonomics (iterate faster on custom firmware)

- **MCP server** (`tools/x4emu_mcp.py`, registered by `.mcp.json` at the repo root; start Claude
  Code in `/Users/mini/x4pro-emu` so it loads): the whole CLI as typed tools, screenshots returned
  inline (`emu_screenshot`), state as JSON (`emu_state`), device tools (`device_status`,
  `device_console`, `device_fetch_screenshots`, guarded `device_flash_crosspoint` /
  `device_restore_stock` that only act with `confirm=True`), and build steps (`build_firmware`,
  `build_flash_image`, `build_sd_image`). It shells out to the CLI, so the CLI stays the single
  implementation and pytest keeps using it. Extend it rather than adding Bash recipes.
- **Design custom firmware emulator-first**, and give it a debug console from day one: a serial
  command set over USB Serial/JTAG (`screenshot` → base64 BMP, `state`, `tap x y`, `key`,
  `light`, `sleep`) makes the *device* scriptable exactly like the emulator, so one pytest can run
  against both (`--target emu|device`) and diff screenshots without the chord + File Transfer
  detour. `x4emu` already has the RX path for sending console input (wire `console-send`).
- **Hot reflash without restarting QEMU**: Espressif's QEMU accepts `esptool` over
  `socket://localhost:5555` (see the README in `docs/device/ref/`); add `x4emu flash-app app.bin`
  that writes 0x10000 through it and resets, and a `make run` target (build → mkflash → run →
  wait → screenshot) so an edit-to-pixels loop is one command.
- **Build speed**: CrossPoint's x4pro env compiles wolfSSL etc. (~9 min cold, ~50 s warm); a custom
  firmware should keep a lean pioarduino env, enable `build_cache_dir`, and skip WiFi/TLS libs it
  does not use. Keep firmware ELFs next to images so `x4emu gdb` has symbols.
- **Fast time**: `--fast-epd` (2 ms refreshes) for logic tests, real timings for UX checks; add
  `--speed N` (icount shift) and a `wait-guest-ms` helper for time-based UI (auto-sleep, toasts).
- **What the emulator guarantees a custom firmware today** (design against this list):
  USB Serial/JTAG console both ways; GPIO 0..48 with edge/level interrupts; SPI2 to the panel;
  UC8279/UC8179/SSD1677 with the real probe answers and device timings; I2C0 with GT911, BM8563,
  CW2017 (BATINFO resident); LEDC frontlight; SDMMC 1-bit card (MBR/FAT32 image); deep sleep with
  EXT1 wake on GPIO3; efuse/MAC of the desk unit. Not there: WiFi/BLE, USB OTG, SAR ADC (§3.2),
  RMT, I2S, touch sensor pads, ULP.
