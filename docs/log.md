# Engineering log

## 2026-09-06 — M0 start

- Host is macOS 26 (Darwin 25.6.0) on Apple Silicon (14 cores, 48 GB), not Ubuntu 24.04.
  The brief says "Ubuntu 24.04 or equivalent"; adapting: Homebrew provides glib, pixman,
  libgcrypt, libslirp, libpng, ninja, meson, mtools, dosfstools. Everything the brief asks
  for exists on this platform except `lsusb`/`dmesg` (replaced by `ioreg`/`system_profiler`)
  and Linux `/dev/ttyACM*` (here `/dev/cu.usbmodem31101`).
- Project path contains a space (`/Users/mini/xteink x4`). The venv's shebang scripts
  (`.venv/bin/esptool.py`) fail with "bad interpreter"; every tool is invoked as
  `.venv/bin/python -m esptool` / `-m espefuse` instead. Tooling in `tools/` does the same.
- esptool installed is 5.4.0. It spells commands with hyphens (`chip-id`, `read-flash`,
  `merge-bin`, `image-info`) and warns on the underscore forms. Brief claim "newer esptool
  spells it merge-bin" confirmed.
- No hands on the device: I cannot press physical buttons, so every bench check that needs
  a button press (Left-held boot strap, power-button-cuts-power-or-sleeps, on-device
  screenshot chord) is recorded as "not testable this session" rather than guessed.
- Device first contact (D0):
  - USB: VID 0x303A PID 0x1001 "USB JTAG_serial debug unit", serial 98:C3:77:BE:EA:30
    (= base MAC). Personality is USB Serial/JTAG, so whatever firmware is on the device
    is not enumerating a TinyUSB composite at idle.
  - Console silent for 5 s at idle (no periodic output).
  - `esptool --before default_reset chip_id`: connects on the first try. ESP32-S3 (QFN56)
    rev v0.2, 40 MHz crystal, "Embedded PSRAM 8MB (AP_3v3)", USB mode USB-Serial/JTAG.
    Hard reset via RTS works, so `--after hard-reset` is a usable reset trigger for boot
    log captures.

### Corrections to the brief found during the audit (details in docs/audit.md)

- **QEMU tag**: `v10.1.2` in espressif/qemu is the upstream QEMU release, not the fork. It has no
  Espressif machines at all (`-machine help` lists only kc705/lx60/sim/virt). Re-cloned from
  `esp-develop` @ febae182 (the head the brief quoted) and branched `x4pro` from there. Time lost: one
  full QEMU build (~4 min on this Mac).
- **Stock boot slot**: this unit boots **app0** (otadata seq 1) and **app1 is erased**. The brief and the
  support doc describe a unit that booted app1. So "the stock image stays in the other slot" needs us to
  copy it there first (a write into an empty OTA slot, allowed by the rules).
- **Panel variants**: there are three (SSD1677 / UC8179 / UC8279), and the factory NVS on this unit says
  `screenType=2` = **UC8279**. The brief only knew about the first two. The emulator's default panel
  should end up UC8279 once a CrossPoint boot on the device confirms it; SSD1677 stays the reference
  model because it is the simplest and the one the brief documents best.
- **Deep sleep in QEMU**: `esp_deep_sleep_start()` does not "fall through into its abort"; it spins on
  `RTC_CNTL_INT_RAW` waiting for SLP_WAKEUP/SLP_REJECT, which the model never sets.
- **Probe**: besides 0x70/0x71 the probe reads 0xA2 (RMTP, 49 bytes) whenever FLG looks driven, and can
  confirm a UC part from that alone. UC models must answer it.
- **esptool 5.4**: no `partition-table` subcommand; `image-info` has no `--version 2`; hyphenated names.
- Support doc internal inconsistencies noted: the I²C-map paragraph still says GT911 INT=4/RST=10
  (struct: INT=10, RST=4); the GT911 reset dance is quoted with two different timings (code: 10/10/50/50 ms).

### Device findings

- Stock `xteink_app` 7.2.4, ESP-IDF v6.0.1 (built 2026-08-14). Stock's console is IDF-level only: no panel,
  touch, or probe lines. After `app_main` returns it brings up WiFi (~13 s) and joins the owner's network
  using credentials stored in NVS. The dump therefore contains WiFi credentials; it stays in `images/`.
  The committed boot log has SSID/BSSID/IP redacted.
- The stock app touches SDMMC at boot (`SD_HOST: input line delay not supported`), so an SD card is in.
  Imaging it needs the file-transfer screen (touch), so it waits for CrossPoint on the device.
- eFuse replay works without the socket burn: `tools/mkefuse.py --dump` writes the `espefuse dump` words
  into the layout `hw/nvram/esp_efuse.c` reads; QEMU then reports the real MAC.

### Stock dump in QEMU (M1's "boot the raw device dump once")

`qemu-system-xtensa -machine esp32s3 -m 8M` + octal PSRAM + efuse replay + the raw dump as MTD: ROM,
IDF 6.0.1 bootloader, octal PSRAM detection ("Found 8MB PSRAM device", timing tuning falls back to
index 5) all run, then the app prints `cpu_start: Multicore app` and stops: the next line on the real
device is `GPIO 44 and 43 are used as console UART I/O pins` / `Pro cpu start user code`. So the app CPU
start (or something right after it) under IDF 6.0.1 is where the stock image stalls. `-d guest_errors`
shows only two "Invalid read at 0x10200C region 'er'" and the M25P80 flash model rejecting SFDP-style
commands 0x5A/0x77/0x7A and 0x90/0xAB. Parked for M6.

### Build environment notes

- pioarduino 55.03.311 unpacks: `framework-arduinoespressif32` (Arduino core 3.3.x),
  `framework-arduinoespressif32-libs` (prebuilt IDF 5.5 libs + headers: the register-map truth),
  `toolchain-xtensa-esp-elf`, `tool-xtensa-esp-elf-gdb`. Versions recorded below once the first build
  finished.
- The first `pio run` failed in pioarduino's penv setup: its `uv` installer could not reach PyPI
  (the owner's Little Snitch firewall blocked it; approved later). Worked around by pre-installing the
  penv dependency list with pip.

### M0 closed

- Toolchain versions (pioarduino 55.03.311): Arduino core 3.3.11, ESP-IDF libs 5.5.5, xtensa-esp-elf gcc 14.2.0,
  xtensa-esp-elf-gdb 17.1 (binary `xtensa-esp-elf-gdb-3.9`), esptool 5.3.0 (pio) / 5.4.0 (venv).
- CrossPoint 1.6.0-x4pro builds: `firmware/.pio/build/x4pro/{bootloader,partitions,firmware}.bin`
  (+ `firmware.factory.bin`, `firmware.elf`).
- QEMU esp-develop @ febae182 builds and boots the ROM; the stock dump gets to `cpu_start: Multicore app`.
- Design decision: the QEMU fork cannot be pushed (no `gh`/GitHub auth here). `qemu/` is a plain clone
  with branch `x4pro`; every commit on that branch is exported to `qemu-patches/` with
  `git format-patch` and `make setup` re-applies them onto febae182 in a fresh clone.
- Design decision for the machine: `xteink-x4pro` is a QOM subclass of the `esp32s3` machine that calls
  the parent `mc->init` and then adds board devices. Stub peripherals (USB Serial/JTAG, GPIO) are
  shadowed by mapping the real models at the same base with a higher memory-region priority, so
  `esp32s3.c` needs no edits. The SoC's children are reached by QOM path (`/machine/soc/intmatrix`, …).

## 2026-09-06 — M1: it boots

- **The big assumption holds.** The unmodified CrossPoint 1.6.0-x4pro image (Arduino 3.3.11 on
  IDF 5.5.5, octal PSRAM) boots on Espressif's stock `esp32s3` machine with `-m 8M` and
  `ssi_psram.is_octal=true`. No SoC-level fault; UART0 shows the bootloader and the app's
  error-level lines within ~1.6 s of guest time.
- New machine `xteink-x4pro` (`qemu/hw/xtensa/xteink_x4pro.c`): a QOM subclass of the esp32s3
  machine that calls the parent init and then adds board devices. The stub USB Serial/JTAG at
  0x60038000 is shadowed by `hw/misc/esp32s3_usj.c` mapped at priority 1. No edits to Espressif
  files beyond two meson.build lines. Patch series exported to `qemu-patches/`.
- USB Serial/JTAG model: EP1 packet buffer, `wr_done` → push to chardev + `SERIAL_IN_EMPTY`,
  SOF raw bit every 1 ms of guest time, never `BUS_RESET`. With `-chardev file,id=usbcon` and
  `-global driver=esp32s3.usj,property=chardev,value=usbcon`, the whole CrossPoint log appears:
  IMU/RTC probe failures, `Device: xteink_x4_pro`, four SDMMC `send_op_cond` retries,
  `SD card initialization failed`, `[XTDET]` NVS and bus-probe lines, SSD1677 init waits,
  `Entering activity: FullScreenMessage`, then `[MEM]` every 10 s. 70 s of guest time on one
  boot, no crash, no spin (`docs/emu/boot-crosspoint-1.6.0-m1-nosd.log`).
- Dead end 1: the `v10.1.2`-based build had no ESP machines (see M0). Dead end 2: my first
  overlay for GPIO shadowed the strap register and the ROM went to download mode ("waiting for
  download", 10k reads of USB OTG); the overlay now answers +0x38 with the SoC gpio's `strap_mode`.
- `-d unimp` is blind on this SoC: `esp32s3.iomem` swallows 0x60000000..0x600d1000 silently and
  the SoC's unimp stubs sit at priority -1000 underneath it. The x4pro machine overlays named
  logging regions (priority 1) so `x4pro/<block>: read/write ADDR` lines show what the firmware
  touches. First boot without SD, top blocks: GPIO IN (0x3C) 520k reads (BUSY polling and
  buttons), SPI2 W0..W15 3001 full 64-byte chunks (= BW+RED writes of the HALF paint plus the
  post-refresh resync: 4 × 48,000 bytes), I2C0 CTR/FIFO_CONF/CLK_CONF/TO/SCL_* (the RTC probe),
  IO_MUX pad config, RTC_IO +0xB0.
- Observations for M2: with GPIO IN reading 0, every active-low button looks pressed, so
  CrossPoint fires its screenshot chord at boot (`[SCR] Failed to save screenshot`); the GPIO
  model's pull-up idle levels fix that. BUSY reads 0 so every panel wait completes in 0 ms.
- Corrections: Arduino 3.3.11 `Wire` uses the **new** `driver/i2c_master.h` path
  (`esp32-hal-i2c-ng.c`, `i2c_master_transmit_receive`), not the legacy `driver/i2c.h` the brief
  assumed; the I2C model must satisfy the new driver's LL sequence. The S3 timer group type is
  `timer.esp32s3.timg`, not `timer.esp32c3.timg`.
- Console drops: one run lost the `[XTDET] bus probe` line while the next run had it. HWCDC
  drops a line after 1 ms if the ring buffer is full, and the ISR-driven drain in the model
  happens one 64-byte packet per IN_EMPTY; under a burst the guest can outrun it. Same as real
  hardware in principle, but worth revisiting if tests see missing lines (a larger drain per
  flush or a shorter IN_EMPTY latency in the model would help).
- Not yet done for M1's acceptance: comparing against the real device's CrossPoint boot log, since
  the device runs stock. Done as soon as CrossPoint is on the device.
- Stock dump on the new machine: same as on the stock machine (stops after `Multicore app`).

## 2026-09-06 — Device: CrossPoint on the X4 Pro, ground truth for the panel

- The stock app switched itself to a TinyUSB mass-storage personality ("XTEink X4 Pro",
  0x303A:0x4002) while idle; the owner confirmed they had put it in USB mode. macOS mounted the
  SD card (15.7 GB FAT32 "NO NAME", 8 KB clusters). Only 16 MB used: `XTCache/`, `XTData/`
  (stock system fonts, home-startup archive), no books. Copied read-only to
  `images/device/sd-files/` (`rsync`, excluding macOS metadata). A raw block image over the
  ESP32-S3's full-speed USB would take hours; not done.
- After the owner exited USB mode the USB Serial/JTAG came back. `tools/device.py
  flash-crosspoint --preserve-stock --yes`: sanity-read app0 == backup, app1 blank, wrote the
  stock 7.2.4 image (`images/device/stock-app0-7.2.4.bin`) into app1 @0x7F0000, wrote CrossPoint
  1.6.0-x4pro (5,387,264 bytes) into app0 @0x10000, read-backs OK, otadata untouched.
  `tools/device.py restore-stock --yes` reverses it.
- First CrossPoint boot on the device (`docs/device/boot-crosspoint-1.6.0.log`): SD mounts,
  RTC found, IMU absent, frontlight attached (gpio 8 / warm 9), and the panel probe:
  `[XTDET] NVS hw_calib/screenType=2 (UltraChip)`,
  `[XTDET] bus probe VER=00 0F 68 00 00 FLG=13 -> UltraChip`, MTP dump
  `A5 A5 1F 20 25 25 3C 00 97 02 02 03 20 02 58 00 … 0F 00 00 68 … 05 0A 0F 14 50 5F 64 7F`,
  `promoted SSD1677 -> UC8279 800x480 (LUT_VER=68)`. **The desk unit is a UC8279 (LUT_VER
  0x68).** Not `00 00 01 FF FF` as the doc's UC8179 example; the brief's SSD1677-first plan is
  kept only as the reference model, the emulator defaults to `uc8279`.
- Measured BUSY intervals from CrossPoint's own `Wait complete` lines: `8279x4_PON` 40 ms,
  `8279x4_DRF` 1326 ms for the boot screen (GC full), 482-483 ms for every DU/fast page. These
  are now the core's defaults for the UC variants.
- CrossPoint idles by dropping the CPU frequency (`[PWR] Going to low-power mode` /
  `Restoring normal CPU frequency`), no light sleep.
- The first capture after flashing recorded nothing: the port was opened after the boot and
  HWCDC only emits once the host has read an IN packet, while the periodic `[MEM]` line comes
  every 10 s... it did not show either; a capture with an RTS reset worked normally. Unclear
  whether CrossPoint had already dropped into its low-power idle with the console detached.
  Always capture with `--reset`.

## 2026-09-06 — M2: first pixel (UC8279), probe answered, stream matches the device

- Models: `hw/gpio/esp32s3_gpio_full.c` (OUT/ENABLE/IN/STATUS/PINn, per-pin idle levels,
  external drivers via "pin-in", edges on "pin-out", edge/level interrupts to ETS_GPIO_INTR_SOURCE),
  `hw/ssi/esp32s3_gpspi.c` (SPI2 CPU-driven transfers: W0..W15, MS_DLEN, CMD.USR, TRANS_DONE),
  `hw/display/x4pro_epd.c` (SSI peripheral + probe pins + BUSY timer + QemuConsole, qdev id `epd`),
  and the pure-C `models/epd_core.c` (SSD1677 / UC8179 / UC8279 command sets, RAM addressing,
  compose, bit-banged probe answers) with host tests `models/tests/test_epd.c` and a fixture
  replayer `models/tests/replay.c`.
- With the GPIO idle levels the boot no longer trips the screenshot chord, and the probe on an
  SSD1677 sees `VER=FF FF FF FF FF FLG=FF` (floating pull-up) exactly as XteinkDetect expects.
- Panel default = **uc8279** (the desk unit). The emulator's `[XTDET]` lines are byte-identical
  to the device's: `VER=00 0F 68 00 00 FLG=13`, the 48-byte MTP, `promoted … UC8279 (LUT_VER=68)`.
- First screenshot (`tests/golden/sd_error.png`): "SD card error" in CrossPoint's portrait UI
  orientation, rendered from the UC8279 NEW plane (gates 120..599 of the 600-gate scan).
- `--trace-epd` vs the device (CrossPoint built with `firmware-patches/freeink-sdk-epd-trace.patch`,
  `-DFREEINK_EPD_TRACE=1`, flashed to app0; `docs/device/boot-crosspoint-1.6.0-epdtrace.log`):
  `tools/epdtrace.py` shows the init + first full refresh identical opcode-for-opcode with the
  same data bytes (`00 61 65 03 30 E1 13 10 50 E0 E5 04 00 12`). The device then paints Home
  with DU pages (`10 13 50 E0 E5 03 E1 91 90 00 12 92`), which the emulator reaches only with an
  SD card. Emulator BUSY waits: PON 41 ms, DRF 1329 ms (device 40 / 1325).
- Fixture `models/fixtures/uc8279-device-boot-home.log` replays through the core: 38 commands,
  3 refreshes, 0 unknown commands (`make -C models test`).
- `-icount 3` works on the two-CPU machine (CrossPoint boots and idles normally under it).
- Dead ends: the trace lost commands pending across a RST pulse (fixed: flush on reset and on
  BUSY completion); `screendump` needs `device=epd` because the SoC's `esp_rgb` is console 0;
  symlinking `models/epd_core.c` into the QEMU tree needs the right relative depth.
- SD: with `-drive if=sd` the card initialises (no `send_op_cond` retries) but SdFat's volume
  mount failed on a superfloppy image; SdFat opens MBR partition 1 (the device's card is MBR +
  one FAT32 partition). `mksd.py` now writes an MBR. Continued in M3.

## 2026-09-06 — M3: home screen with the device's SD contents

- `tools/mksd.py` writes an MBR with one FAT32 partition at 1 MiB (SdFat mounts partition 1 and
  does not fall back to a superfloppy). With `images/sd-device.img` (the device card's files)
  attached via `-drive if=sd`, CrossPoint mounts the card, loads the theme, brings up the
  frontlight manager, paints the boot splash and goes Home, just like the device.
- The complete panel stream now matches the device's recording opcode-for-opcode and
  byte-for-byte for all 38 commands (`tools/epdtrace.py … --from-cmd 0x00`): init, GC full
  refresh, and the two DU Home paints with PTIN/PTL/PTOUT.
- `x4emu press right` moves the selection: 3.5 % of pixels change. Every press now triggers a
  refresh within a few ms of release, also in CrossPoint's low-power (reduced CPU clock) mode.
- Two false alarms on the way, both tool races: `wait-refresh` sampled its baseline after the
  refresh had already been counted (fixed: `--total N`, and `press/tap --wait` measure from a
  pre-input baseline), and a press issued while the firmware was still painting was ignored,
  as on hardware (fixed in the CLI with `--quiet S` / `wait-quiet`).
- Console line loss root cause: ESP-IDF's connection monitor clears the SOF raw bit from the
  FreeRTOS tick hook and declares the host gone after a few ticks without SOF; when QEMU
  delivers bunched ticks, HWCDC flips `connected=false` and replaces the oldest ring-buffer
  bytes (its not-connected FIFO policy) — whole lines vanish in bursts. The model now
  presents SOF on every INT_RAW read while a host is attached. No lines lost since.
- GPIO5 (SD power enable) is ignored by the SDMMC path and visible in `state.gpio.sd_pwr`.
- Device-vs-emulator home-screen diff: waiting for a device screenshot (CrossPoint's
  Power + lower-side-button chord writes a BMP to the card; the owner has to take it and
  expose the card over USB). The emulator's home screen shows "0%" battery because the CW2017
  is not modelled until M4; the clock is absent for the same reason (RTC not found).
- `tests/test_boot.py`: boots with a fresh 64 MiB MBR/FAT32 image, waits for Home and two
  refreshes, presses Right, asserts a pixel diff, and checks the probe verdict text.

## 2026-09-06 — M4: touch and the rest of the I2C bus

- `hw/i2c/esp32s3_i2c.c`: the S3 I2C controller in master FIFO mode as ESP-IDF 5.5's
  `i2c_master` driver (Arduino 3.3.11 `Wire` → `esp32-hal-i2c-ng.c`) programs it: TX FIFO
  through DATA, COMD0..7 command list (RSTART/WRITE/READ/STOP/END, byte_num, ack bits, done),
  CTR.TRANS_START executes the list synchronously against a QEMU I2CBus; NACK / END_DETECT /
  TRANS_COMPLETE interrupts into ETS_I2C_EXT0_INTR_SOURCE. `--trace-i2c` writes one JSON line
  per transaction.
- `hw/misc/x4pro_i2c_devs.c`: GT911 (16-bit pointer, 0x814E status / 0x8150 points / 0x8140
  ID / config area, INT line, re-frames every 10 ms while a finger is down, NACKs while GPIO2
  is not driven LOW), BM8563/PCF8563 (16 BCD registers, host wall clock seed + guest time,
  settable), CW2017 (VERSION 0x0D, VCELL/SOC from `battery-soc`/`battery-mv`, MODE dance,
  SOC_ALERT 0x80, the OEM 80-byte BATINFO resident so the firmware skips its upload).
- Console now matches the device's I2C findings: `SDK RTC found`, no IMU (the X4 Pro profile
  has `ImuType::None`, so nothing is probed), GT911 answers at 0x5D on the first try, the
  status bar shows the gauge's percentage (63 % default; `x4emu battery --soc 42` changes it
  after CrossPoint's 1.5 s poll and the next repaint).
- Touch mapping verified end to end: `x4emu tap 345 350` (landscape panel pixels → GT911 raw
  (129, 345)) opens "Browse Files"; `x4emu home` returns to Home. Both react within ~50 ms.
- First BATINFO table I typed was wrong (two rows lost to a grep filter), which made the
  firmware re-upload the profile; fixed from the source lines verbatim.
- Tests: `tests/test_touch_reader.py` boots with a generated EPUB (`tests/mkepub.py`), checks the
  I2C devices, taps into the file browser and the book, pages forward twice with pixel diffs,
  and checks the battery/charging properties.

## 2026-09-06 — M5: frontlight PWM and deep sleep

- `hw/misc/esp32s3_ledc.c`: S3 LEDC (LS channels 0..7, timers 0..3) with the S3 offsets; duty
  resolution from the timer, duty from LSCHn_DUTY, DUTY_CHNG_END raised at once on DUTY_START.
  `state.ledc` lists every channel with the GPIO it is routed to (GPIO matrix
  FUNC_OUT_SEL == LEDC_LS_SIG_OUT0_IDX + ch = 73 + ch). CrossPoint attaches the frontlight to
  channels 0 (GPIO8 cool) and 1 (GPIO9 warm) at 25 kHz; the stock app used channels 4/5 for the
  same pins (support doc). `x4emu light` prints the cool/warm duty in permille.
- `hw/misc/x4pro_sleep.c`: an overlay on RTC_CNTL that handles STATE0.SLEEP_EN, WAKEUP_STATE,
  INT_*, EXT_WAKEUP_CONF, SLP_REJECT_CONF, DIG_PWC, EXT_WAKEUP1(_STATUS), SLP_WAKEUP_CAUSE and
  forwards every other offset to Espressif's rtc_cntl with `memory_region_dispatch_*`.
  Deep sleep (DIG_PWC.DG_WRAP_PD_EN set) pauses the VM (`x4emu status` says so); the GPIO
  model reports pin levels into the overlay, and when a pin selected in EXT_WAKEUP1 reaches the
  EXT1 level it records EXT_WAKEUP1_STATUS + SLP_WAKEUP_CAUSE=EXT1, sets both CPUs' reset
  cause to 5, requests a system reset and resumes the VM. Light sleep returns immediately.
- Verified on CrossPoint: `x4emu hold power --ms 700` → "Power button held 408ms, sleeping" →
  Sleep activity (the "CrossPoint SLEEPING" logo stays on the panel) → "Entering deep sleep" →
  paused, `ext1_sel=0x8` (GPIO3), any-low. `x4emu press power` (auto-extended to a 1.5 s hold,
  because CrossPoint re-sleeps unless the button is still held when it verifies the wake ~1.3 s
  after the reset) → `rst:0x5 (DSLEEP)`, wake cause EXT1, status GPIO3, then the splash-less
  wake path straight to Home. Wall-clock: the guest's millis() keeps counting across the modelled
  reset (the systimer is not reset by QEMU's system reset), so post-wake timestamps continue
  from where they were; real hardware restarts at 0.
- Device comparison of the wake log: pending (the device had auto-slept; USB is off in deep
  sleep and only the owner can press its button).
- Short power presses (< 400 ms) are clicks (X4 Pro: double-click toggles the frontlight),
  not sleep: `SETTINGS.getPowerButtonDuration()` is 400 ms unless "short press = sleep" is set.

## 2026-09-06 — M6 (part 1): where the stock firmware stops, and why

- Stock `xteink_app` 7.2.4 (IDF 6.0.1) on the `xteink-x4pro` machine: ROM, bootloader, octal
  PSRAM detection and `cpu_start: Multicore app` print, then nothing. `x4emu qmp` +
  `info registers -a`: CPU0 PC=0x4004e89b, CPU1 PC=0x40041a76 (ROM). Resolved with the full ROM
  symbol table from Espressif's `esp-rom-elfs` release 20241011 (`esp32s3_rev0_rom.elf`,
  `xtensa-esp-elf-nm`; the public `esp32s3.rom*.ld` files only list exported API symbols and
  gave nonsense for these addresses): CPU0 is in `Cache_Occupy_Items+0x3b`, called from
  `Cache_Occupy_Addr+0x4c`, with A02=0x3C000000 (DROM window) and A08=0x600C4034 =
  `EXTMEM_DCACHE_OCCUPY_CTRL_REG`; CPU1 idles in `ets_delay_us` (its ROM wait loop).
  So IDF 6.0.1's startup asks the D-cache to "occupy" (lock) a region and polls
  `DCACHE_OCCUPY_DONE` (bit 1), which Espressif's `esp32s3_cache` model never sets (it has no
  occupy/preload/autoload handling). The fix is a register overlay that reports the operation
  done; see part 2.

## 2026-09-06 — M6 (part 2): stock firmware boots to its idle loop; panel variants

- Overlay for `EXTMEM_DCACHE_OCCUPY_CTRL` (0x600C4034) and `EXTMEM_DCACHE_LOCK_CTRL`
  (0x600C401C) reporting DONE: the stock app now runs `app_main`. Its `boot-preflight` rejects a
  plain power-on (`hold=0ms decision=2 reason=3`) and enters deep sleep with `wake_on=next_press`,
  which the sleep model pauses. `x4emu press power` (a 1.5 s hold) wakes it with DSLEEP and the
  preflight accepts (`source=2 hold=602ms decision=0 reason=11`), so the stock reader wants the
  power button held ~600 ms to boot, as the real device's log (`source=6 … decision=0`) came from a
  USB-triggered reset that it also accepts.
- After that the stock app initialises memory/tasks, `SD_HOST`, reads the GT911 ID/version/config
  (0x8140: "911", 0x8144: 60 10, 0x8047..), the RTC time, polls the CW2017 SoC/VCELL every few
  seconds, resets the panel three times and reads VER through SPI2 in half-duplex — the model now
  answers `00 0F 68 00 00` on that path too — and then both CPUs sit in the idle loop. No panel
  init, no WiFi (the device starts WiFi at 13 s). Not chased further (out of the brief's scope);
  the untouched peripherals it did poke are the SAR ADC (`sens` 0x60008904/0x6000880C,
  `apb_saradc` 0x60040000/04/18/28/70: ~250 accesses) and RTC IO (0x60008490/BC/C4). A stock
  task is most likely blocked on one of those (battery ADC conversion) or on an SD/FATFS mount
  under IDF 6's driver. `--trace-epd`/`--trace-i2c` capture everything it does up to that point.
- Panel variants: `tests/test_panels.py` boots CrossPoint with `--panel ssd1677|uc8179|uc8279`;
  the probe verdicts are `default controller` (line floats to 0xFF), `promoted … UC8179
  (LUT_VER=01)` and `promoted … UC8279 800x480 (LUT_VER=68)` and each variant paints Home. The
  UC8179 driver used two commands the core did not know (BTST 0x06, PWS 0xE3); added, along with
  TCON/VDCS/TSC/TSE/LPD/AUTO. Only the UC8279 answers are measured; the UC8179 answers are the
  SDK doc's example (`00 00 01 FF FF`, FLG 0x13) and the SSD1677 behaviour is the floating line.
- Panel core: BUSY is asserted while RST is low and for the power-up time after it rises, so
  firmware that waits for a BUSY edge after reset sees one.
- Patch series (`qemu-patches/`, 5 patches) applies cleanly on a fresh febae182 checkout with
  `git am`; the symlinked core sources resolve only when the clone sits at `<repo>/qemu/`.

## 2026-09-06 — Session status

| Milestone | State | Open items |
|---|---|---|
| M0 environment, audit, first contact | done | — |
| M1 it boots (USB Serial/JTAG console) | done | real-device boot log comparison done for CrossPoint (`docs/device/boot-crosspoint-1.6.0.log`) |
| M2 first pixel (GPIO, SPI2, panel) | done | UC8179 answers are from the SDK doc, not measured (no such unit) |
| M3 home screen with the device's SD contents | done | device screenshot reproduced with 0 pixels different (`tests/test_device_screenshot.py`) |
| M4 touch, I2C bus, battery, RTC | done | GT911 product ID / raw touch frame not read from the real chip (its ID registers are only reachable through a dev build) |
| M5 frontlight PWM, deep sleep | done | wake log compared with the device: same sequence and timings |
| M6 stock firmware, other panels | stock runs to its idle loop after a power wake; variants tested in the emulator | stock's blocker (SAR ADC / SD-FATFS / an interrupt wait) not identified; UC8179 unverified on hardware |

Device state at the end of the session: app0 = CrossPoint 1.6.0-x4pro with the EpdBus trace
(`images/crosspoint-epdtrace.bin`), app1 = stock 7.2.4, bootloader/partition table/otadata
untouched; `tools/device.py restore-stock --yes` returns app0 to stock. The device is in deep sleep
(auto-sleep) and its USB port is gone until the power button is pressed.

Note: the `firmware/` submodule working tree carries the uncommitted EpdBus trace patch and a
`platformio.local.ini` that sets `-DFREEINK_EPD_TRACE=1`; both are reproduced by
`firmware-patches/freeink-sdk-epd-trace.patch` (`cd firmware/freeink-sdk && git apply
../../firmware-patches/freeink-sdk-epd-trace.patch`). The submodule pointer itself is unchanged
(7db14a01). `images/crosspoint.bin` is the untraced build the emulator tests use.

## 2026-09-06 — M5 device comparison: wake-from-deep-sleep log

Device (`docs/device/sleep-wake-crosspoint-1.6.0.log`, owner held power to sleep and again to
wake) vs emulator (`docs/emu/sleep-wake-crosspoint-1.6.0.log`, `hold power --ms 700` then
`press power`):

| Step | Device | Emulator |
|---|---|---|
| ROM banner | lost (USB re-enumerates after the wake) | `rst:0x5 (DSLEEP)` |
| bring-up | RTC found, SD mounted, theme, frontlight | same (plus `IMU not found` first, lost on the device to the port reopen) |
| probe | `NVS hw_calib/screenType=2 (UltraChip)`, `VER=00 0F 68 00 00 FLG=13`, MTP, promoted UC8279 | `NVS … not set` (blank NVS in the built image), same VER/MTP/promotion |
| route | `Entering activity: Home` directly (splash-less wake) | same |
| paints | PON 40 ms, DRF 1327 ms, DRF 483 ms | PON 42 ms, DRF 1327 ms, DRF 484 ms |
| after | `Going to low-power mode` at +3.6 s | same at +3.4 s |

Also on the device: `[CPS] Settings loaded from file` (a settings file now exists on its card).
The device's sleep-entry lines were not captured: the USB Serial/JTAG drops the moment the chip
sleeps, and the last lines before it never reach the host. To see them use `-DENABLE_SERIAL_LOG`
on UART0 or read them in the emulator (`Power button held 405ms, sleeping` → Sleep activity →
`Entering deep sleep`).

## 2026-09-06 — Oracle check: CrossPoint's own screenshot vs the panel model

- `x4emu chord power right` fires CrossPoint's screenshot chord in the emulator; the firmware
  writes `/screenshots/screenshot-<ms>.bmp` (1-bit, 480x800, its frame rotated) to the SD image.
  Un-rotated (`PIL rotate(90)`; the source comment's "counter-clockwise" is the other way round
  in PIL's convention), it matches the QMP screenshot of the panel with **0 pixels different**:
  the UC8279 plane-to-glass mapping (gate offset 120, bit order, row order) shows exactly what the
  firmware drew. `tests/test_screenshot_chord.py` keeps it that way; `x4emu screenshot --diff`
  accepts a device BMP directly.

## 2026-09-06 — M3 closed: device screenshot vs emulator, explained line by line

The owner took CrossPoint's screenshot chord on the device's home screen and exposed the card
(`docs/device/screenshots/screenshot-3824.bmp`, 480x800 1-bit; landscape rendering and diff mask
next to it). Against the emulator's home screen (same firmware build, the card's files mirrored
into `images/sd-device.img`):

| Region | Device | Emulator as booted | Cause | After `x4emu battery --soc 100 --charging on` |
|---|---|---|---|---|
| status bar, digits | `100%` | `63%` | CW2017 default SoC in the model vs the real gauge | identical |
| status bar, icon | charging bolt | plain battery | GPIO21 charger STAT: high on the device (USB power), low in the model by default | identical |
| everything else (menu, highlight, icons, texts) | — | — | pixel-identical | identical |

Result: 151 pixels (0.039 %) as booted, **0 pixels** once the battery state matches
(`tests/test_device_screenshot.py`). An earlier golden showed CrossPoint's button legend
(Down / Up / Select) at the right edge; that golden predated the GT911 model, when
`gpio.hasTouch()` was false and `UITheme::getMetrics()` kept `buttonHintsHeight`. With the touch
controller present the emulator hides the legend exactly like the device. The device's
`.crosspoint/settings.json` and `state.json` are kept in `docs/device/` (reader preferences only).

## 2026-09-06 — Stock firmware: spi_master + GDMA, the interrupt matrix, quad flash reads, home screen

Where the previous session left it: the stock `xteink_app` 7.2.4 reached `Returned from app_main()`
and "idled"; the handoff blamed the SAR ADC. Findings, in the order they were made:

- **SAR ADC was not it.** The ~250 SENS/APB_SARADC accesses are two `bootloader_random_enable/disable`
  pairs (pattern tables written then cleared to `0xffffff`, `MEAS1_START_SAR` never set) plus
  `rtcio_ll_function_select` toggling `SENS_SAR_PERI_CLK_GATE_CONF.iomux_clk_en` per RTC pad.
  Nothing polled the ADC.
- **`dwc_sdmmc` does not log every access.** Its `dwc_sdmmc_read/write:` lines are the `default:`
  cases (`LOG_UNIMP`) for registers it does not implement (CLKENA, CTYPE, TMOUT, CDETECT, UHS, 0x800);
  CMD/CMDARG/RINTSTS accesses are silent. The stock's card init and 4,600 single-sector reads
  (`sdmmc_card_init` → CMD0/8/55/41/2/3/9/7/16/51/6…, then CMD13+CMD17 pairs, 6 writes) all
  succeeded; the reads only looked "slow" because the trace hid the commands.
- **Where the tasks really were** (FreeRTOS TCB walk over a `pmemsave` dump: name at TCB+52, list
  items at +4/+24 with `pvOwner == TCB`, `xCoreID` at +68, stack end at +72; frames from
  `pxTopOfStack`, PC at +4, windowed return addresses have bits 31:30 = call size): `xteink_ui`,
  `xteink_input`, `xteink_sdmon`, `xteink_nvs`, `pwr_monitor` in short `vTaskDelay` polls,
  `epd_flush` blocked forever in `spi_device_transmit()` → `spi_device_get_trans_result()` on the
  spi_master result queue (strings at the frame addresses), i.e. **the stock drives the panel with
  ESP-IDF's interrupt- and GDMA-driven `spi_master`**, not Arduino's CPU FIFO path.
- **Bug 1, Espressif `esp32s3_intc.c`:** the matrix stored a new map entry but never re-evaluated
  the CPU line. spi_master invokes its ISR by *re-routing* the SPI2 source (from 6 = disconnected to
  its CPU interrupt) while TRANS_DONE is left pending, and parks it on 6 to disable it. Live state
  said it all: `INT_RAW = INT_ENA = INT_ST = TRANS_DONE`, core-0 map for source 20 = 6. Patch 0006
  tracks every source's level and drives each CPU interrupt with the OR of the sources routed to it,
  on source changes and on map writes.
- **SPI2 through GDMA** (patch 0007): `DMA_CONF.DMA_TX/RX_ENA` route the data phase through the
  GDMA channel bound to peripheral SPI2 (`esp_gdma_get_channel_periph` + `read/write_channel`;
  link property "gdma" = `/machine/soc/gdma`), up to the full 32 KB `MS_DLEN`; the FIFO path is
  unchanged; `DMA_INT_SET` reads as 0; `state.spi2` gains `dma_tx_bytes/dma_rx_bytes/dma_errors`.
  First result: 47 panel commands, 3 refreshes, 424 KB over DMA, 0 errors. The stock's UC8279 init:
  `00 37 4D`, TRES `03 20 02 58` (800x600, gates 120..599 used), GSST 0, PFS 0x20, `E1 02`, DTM2/DTM1
  60,000-byte planes, CDI 0x97, CCSET 0x02, TSSET 0x1E, PON, PSR `17 4D`, DRF 1.3 s later; partial
  updates with PTIN/PTL/PTOUT, CDI 0xD7, TSSET 0x5A.
- **Bug 2, Espressif `esp_gdma.c`:** `read_channel` fetched the descriptor after the last node even
  when the transfer was complete; the last node's `next` is NULL so every SPI DMA transaction read a
  12-byte descriptor from guest address 0 (three `Invalid read` lines each). Found with the exec
  trace (`log exec` via QMP) around a debug stop; `get_pc()` in the unassigned-access logger is the
  TB start, not the faulting instruction. Patch 0008 guards the fetch.
- **The first screen was the first-boot "Select Region" page, and "Region save failed".** Region is
  NVS `hw_calib/region` (u8, 1 = CN, 2 = overseas; getter 0x42069160, setter re-reads with default
  238). The flash trace (`trace-event-set-state m25p80_*` at runtime, no rebuild) showed the entry
  programmed, marked written, read back, then marked erased and zeroed: NVS's CRC-mismatch erase.
  The read-back returned 30 of 32 bytes.
- **Bug 3, Espressif `esp32s3_spi.c`:** dummy cycles were converted to bytes as single-line and the
  field's "minus one" ignored. ESP-IDF's 0xEB read (24-bit address, `USR_DUMMY_CYCLELEN=5` = 6
  cycles, `FREAD_QIO`) sent 1 dummy byte where QEMU's ISSI `is25lp128` expects 3 (6 cycles x 4
  lines): every quad read shifted by two bytes. Consequences before the fix: NVS judged page 0
  corrupt at **every** boot, erased and re-provisioned it (the 21 boot-time page programs), which is
  why the stock showed its first-boot screens; and every NVS write "failed". Patch 0009 counts
  cycles at the read mode's width (QIO 4, DIO 2, else 1); octal-PSRAM transactions keep 3 bytes.
  After it: 0 page programs at boot, the stock boots straight to **Home ("Bookshelf", clock, 63 %)**,
  warm frontlight at 249 permille on GPIO9, the menu tap repaints, and WiFi starts at 14 s as on the
  device (13.4 s).
- **WiFi start hangs the UI:** the `wifi` task (prio 23, core 0) spins in ROM
  `rom_pkdet_vol_start+0x2e` (from `rom_get_sar2_vol`) polling `0x6000E050` bits 26:24 for 7 — the
  analog-master I2C block (RTCCNTL+0x6050 in the ROM's naming; SAR2 samples at 0x6000E080..9C), which
  the SoC's silent catch-all answers with 0. Everything on core 0 starves. `tools/nvsedit.py IMAGE
  set-u8 user_config net_en 0` keeps the stock off the radio; that is what `tests/test_stock.py`
  boots. Modelling the block is the next step (`docs/NEXT_PHASE.md`).
- Tooling: `x4emu qmp` accepts arguments named `name` (positional-only fix); `tools/nvsedit.py`
  lists/edits NVS; the ROM symbol table is at `images/rom/esp32s3_rev0_rom.nm` (esp-rom-elfs
  20241011). QEMU trace events work at runtime through QMP (`trace-event-set-state`), which made
  the flash and SD traces possible without rebuilding.
- Dead ends: gdb from the pioarduino package cannot talk to this QEMU (the python builds miss
  libpython, the no-python build has no XML target description: "'g' packet reply is too long");
  a temporary `qemu_system_debug_request()` in the unassigned-access path plus `info registers -a`
  and `pmemsave` replaced it. esptool's `image-info` "File offs" column is the segment *header*
  offset (data starts 8 bytes later); objdump labels were off by 8 until the image was parsed
  properly. In objdump output `l32r` literal values are true addresses but `call8` targets follow
  the labels. zsh does not word-split a variable holding a command (use a function).

Device state unchanged (app0 CrossPoint, app1 stock, backups intact). `images/stock.bin` is
regenerated from the dump; the stock writes NVS at first touch of settings, so tests boot copies.

## 2026-09-06 — Stock WiFi start no longer starves the UI: analog master, SENS, radio stub

Goal A's last blocker (`docs/NEXT_PHASE.md` §3.2 steps 1–3). With the device's NVS as dumped
(`user_config/net_en=1`) the stock now runs its WiFi start to the same console lines as the device and
then gives up on its own, while the UI keeps working. Four things were in the way, found one after the
other; each step was: run, read the hot-poll list, take the PC over QMP, disassemble the app there.

- **Analog-master I2C block** (0x6000E000, new `hw/misc/x4pro_ana_i2c.c`, overlay priority 2). Protocol
  from the ROM (`images/rom/esp32s3_rev0_rom.elf`): the masters at +0x00/+0x04 take
  `block | reg<<8 | data<<16`, bit 24 write, bit 25 busy, bit 26 start (`rom_chip_i2c_readReg_org`
  0x400354fc, `rom_chip_i2c_writeReg` 0x40035818, `rom_i2c_paral_read/write` 0x40035614/0x40035684); a
  read's answer comes back in bits 23:16 once busy clears; `rom_get_i2c_hostid` (0x400354bc) sends blocks
  0x62..0x64 to master 1. +0x40 ANA_CONF0 (BBPLL cal STOP_FORCE bits 2/3, CAL_DONE bit 24), +0x44/+0x48
  ANA_CONFIG/2 (`regi2c_ctrl_ll.h`). SAR2 power detector: +0x50 bit 1 start, bits 26:24 == 7 idle
  (`rom_pkdet_vol_start` 0x40036a18 polls it before and after the start), +0x5C/+0x60 configuration
  (`rom_pwdet_sar2_init` 0x40036470 writes 0x16a and bits 19/21/23; `rom_get_sar2_vol` 0x40036afc selects
  the input in bits 4:3), +0x80..+0x9C eight 13-bit samples (`rom_read_sar_dout` 0x40036aa4). The model
  stores every analog register per (block, reg), answers idle/done, and returns `sar2-code` (0x800) as the
  samples. A WiFi start makes 1049 reads / 535 writes: SAR ADC 0x69 (IDF's adc/tsens init, on the boot path
  with WiFi off too), DIG_REG 0x6D, BOD 0x61, BBPLL 0x66, and the radio blocks 0x62/0x63/0x64/0x67/0x6a/0x6b.
  `x4emu state` → `ana_i2c`; qemu.log gets one `x4pro.ana-i2c:` line per transaction.
- **SENS block** (0x60008800, new `hw/misc/esp32s3_saradc.c`). The next spin was app code (0x422aac19)
  polling `SENS_SAR_TSENS_CTRL` (+0x50) for `TSENS_READY` (bit 8): the PHY reads the chip temperature.
  Model: TSENS always ready with `tsens-out` (104 ≈ 25 °C by ESP-IDF's formula), MEAS1/MEAS2 oneshots
  (`START_SAR` → `DONE_SAR` + `sar1-data`/`sar2-data`, 2048), everything else read-back storage (the RTC IO
  clock gates and the bootloader RNG pattern tables that used to land in the `sens` logger). `state.saradc`.
- **Radio register stub** (FE2 0x60005000, FE 0x60006000, RX/NRX 0x6001C000, BB 0x6001D000, WiFi MAC
  0x60033000; new `hw/misc/esp32s3_rfstub.c`, priority 2). Storage plus status bits that read as set:
  FE +0x174 bit 16 (app 0x422e2cf9: a capture started with FE +0x144 bit 1 and RX +0x02C bit 23; the loop
  counts RX +0x08C[18:12] ≤ 69 while it waits, no timeout) and MAC +0xD14 bit 0 (app 0x4230bfa6: set bit 1,
  wait for bit 0; the first MAC access after `phy_init`, the device prints `wifi:mode : sta` right after).
  `-global driver=esp32s3.rfstub,property=sticky,value=0xADDR:0xMASK,…` adds entries without a rebuild.
  A WiFi start makes 3154 reads / 5563 writes to these blocks, none a spin afterwards; the busiest pair is
  the pbus FE +0xC8/+0xCC (RF register writes; 92 reads of the busy word, which reads 0 = idle).
- **Capped access logger** (`include/hw/misc/x4pro_hotlog.h`). The first SENS poll wrote 22 M `x4pro/sens`
  lines (1.9 GB) into qemu.log in 30 s. Every `x4pro/<block>` logger and the radio stub now log the first
  32 accesses per address and count the rest; `state.iolog_hot` / `state.rf.hot` list the addresses that
  went past the cap with their counts — the next spin is read from `x4emu state`, not from the log.

Result (`x4emu --name net run --flash <dump copy, net_en=1> --sd images/sd-device.img`): PHY 711 comes up,
`wifi:mode : sta (98:c3:77:be:ea:30)`, `wifi:enable tsf` (device: identical lines), then
`W wifi:TX Q not empty: 500, TXQ_BLOCK=0`, `force witi stop`, `flush txq`, `sw txq[0] state(1) is not
idle` at +7.5 s and `wifi:Deinit lldesc rx mblock:6` at +17.5 s after the start — ESP-IDF's own give-up path
without air. Core 0 is never starved: the CW2017 poll continues every 3 s, Home repaints (refresh 4 instead
of 3, a status-bar glyph), both CPUs sit in the idle loop, the menu tap still repaints, and Home matches
`tests/golden/stock-home.png` outside the status bar. Emulator-only artefact: `phy: error: pll_cal exceeds
2ms!!!` x6 — the PHY steps the RF PLL cap (writes 0x62/0x01 = 0..0x0a and reads 0x62/0x0c for a lock flag
the stored-value model never sets); the device prints nothing there. New test:
`tests/test_stock.py::test_stock_wifi_fails_fast` (the `net_en=0` case stays).
QEMU patch 0010 carries all of it; no Espressif file touched.

Lessons: the ROM's `RTCCNTL+0x6050` naming is the linker script's, the block is the analog master; the
PHY's status polls have no timeouts, so every bit it waits for needs an answer, and with the hot lists each
one is a two-minute step; loop counters kept with `l16ui/s16i` wrap at 65536 and look like countdowns in
register samples; `esp32s3.rfstub` stores what the driver writes, which the MAC address setup at
0x60033000+8n / +0x24+8n (app 0x4230be5e) relies on.

## 2026-09-06 — Session 4: the stock's frontlight auto-dim froze core 0 (LEDC fade model), tcbwalk, stock screens

The handoff said 14/14 green; `make test` at the start of the session gave 12/14: the stock's menu tap
never repainted, and CrossPoint's file-browser row tap once drew nothing. The stock failure reproduced by
hand and looked like a touch or I2C bug: after one good tap the GT911 model sat with a frame pending
and INT low, the firmware never read it again, and the CW2017 poll (every 3 s) had stopped too — the
I2C controller idle, its last transaction complete, interrupts disabled. Nothing waited on the bus.

- **The tick was dead.** `pmemsave` over QMP (the HMP form parses a `/` in the path as a division) and the
  new `tools/tcbwalk.py` (finds every TCB by its self-owned list items, prints core, priority, the
  queue/semaphore a task waits on, and a register-window backtrace with ROM/app symbols): every
  periodic task of the stock (`xteink_input`, `xteink_ui`, `pwr_monitor`, `xteink_sdmon`, …) sat in the
  delayed list waiting for ticks 93553..93917 while the dump was 345 s old. `xTickCount` = 93551, frozen.
- **Core 0 never left a level-1 interrupt.** `info registers -a`: core 0 at PS.INTLEVEL=1 on the interrupt
  stack, `INTERRUPT & INTENABLE` = CPU interrupts 2, 5 and 12; the PC in ESP-IDF's level-1 dispatcher
  loop (`rsr.interrupt` / pick the highest bit / `wsr.intclear` / call the handler / `j` back). The
  interrupt matrix (0x600C2000, source → CPU interrupt) said 12 = LEDC, 5 = SYSTIMER target 0 (the tick),
  2 = SYSTIMER target 2 (esp_timer). LEDC came first every time; the tick never ran again.
- **Why the LEDC handler could not clear it.** `LEDC_INT_RAW` = `DUTY_CHNG_END_LSCH1`, `INT_ENA` = channels
  0/1; channel 1 (warm, GPIO9) had `CONF1` = fade *down*, 255 steps of 1 duty unit every 24 PWM periods
  (245 ms at 25 kHz) from `DUTY` 4080 (= 255.0, the 249 ‰ the stock lights at boot). The model completed
  every fade "instantly" without moving the duty. ESP-IDF's `ledc_fade_isr` clears the flag, reads `DUTY_R`
  back, sees 255 instead of 0, programs the next segment and sets `DUTY_START` again — the model raises
  `DUTY_CHNG_END` in the same instruction, the ISR returns into a dispatcher that sees the source high
  again, forever. Trigger: the stock fades the frontlight out **60 s after the last input** (the power
  press that woke it counts) and fades it back on the next touch. The tests were flaky because boot to Home
  takes 11–50 s wall time here, so the tap sometimes landed on the dim.
- **Fix (QEMU patch 0011, `hw/misc/esp32s3_ledc.c`):** `DUTY_START` with `DUTY_NUM` steps walks the duty
  from `DUTY` to `DUTY ± NUM×SCALE` over `NUM×CYCLE` periods of the channel's timer in guest time (a QEMU
  timer per channel); `DUTY_R` and `state.ledc` follow the walk; `DUTY_CHNG_END` fires when the last step
  lands; `NUM = 0` applies the duty at once as before. `state.ledc.channels[]` gains `fading` and
  `target_permille`, `state.ledc.fades` counts fades. Verified: the warm channel goes 249 → 0 at 60 s, the
  gauge poll and the clock repaint continue, a tap fades it back and opens the menu.
  New test `tests/test_stock.py::test_stock_idle_dims_frontlight_and_keeps_ticking`.
- **CrossPoint's lost taps.** Three different CrossPoint tests each failed once on an input that drew
  nothing: the Home pad after Browse Files, the file-browser row tap, the Browse Files tap right after a
  battery change. Common cause: CrossPoint polls the GT911 in its main loop, and a rendering pass takes
  seconds ("Time = 3494 ms from clearScreen to displayBuffer") during which no poll happens; `wait-quiet`
  only sees panel refreshes, so a 120 ms tap into that window is invisible to the firmware. `x4emu tap`
  and `home` now stay asserted for at least `--ms` and until the firmware has consumed the frame
  (`state.gt911.clears`, up to 5 s) and report when it was read; the firmware still sees a short touch
  because it measures from its own first read. `home` also takes `--quiet` and defaults to 250 ms.
- **Stock screens (NEXT_PHASE §3.2 step 4).** Nav menu (Read, All Files, USB Mode, Cloud Sync, Settings;
  date on the right edge), All Files (microSD, `screenshots/`, `Test Book.epub`), the EPUB (opens at
  "Chapter 2", Right pages), the reading menu (back, bookmark, Contents/Progress/Font/More), the bookshelf
  with the book (38 %, "0h1m", Continue), Settings (Unbound, Upgrade, Network, Bluetooth, Language, Time,
  Startup Password, System Font, About Device). Goldens `tests/golden/stock-{menu,all-files,reader-page1,
  reader-page2,reading-menu,settings}.png` and `test_stock_screens_walk` (coordinates in its docstring).
  The card image for the stock tests now carries `tests/mkepub.py`'s book (mcopy into the scratch copy).
  Panel stream of every update (`--trace-epd`): PTIN, PTL full window (x 0..799, y 120..599), DTM1 48 000
  bytes, PTOUT, PTIN, PTL changed window, DTM2 window bytes, PTOUT, CDI D7, CCSET 02, TSSET 5A, PFS 20,
  E1 02, [PTL full again], PTIN, PSR 17 4D, DRF; the boot init is command for command what
  `docs/hardware.md` lists.
- **Not found yet: the brightness UI.** Settings shows no display/light entry; a swipe and the Right button
  do not scroll it; the Home pad does nothing in the stock (it reads the frames — maybe it wants the GT911
  key value byte at 0x8177, which the model does not serve). Candidates: the reading menu's "More", a
  status-bar pull-down, the power-button double click.
- **Stock has a screenshot function after all.** Strings: Settings → Developer → "Screen Capture",
  `/sdcard/screenshots/screenshot_%s.xic`, `Screenshot saved`, a developer log `/sdcard/logs/dev_%s.log`
  with `light_on=%u brightness_value=%u color_temp_value=%u wifi_enabled=%u …`, and a
  `DeveloperToolsService` that pushes screenshots to `/sdcard/devtools/screenshot_push_url.txt`. If the
  `.xic` format can be decoded, step 5 (a device oracle for a stock screen) needs no photo.

Lessons: when touch, I2C and every 3-second poll stop together, check the tick before any peripheral —
`tcbwalk.py` shows it in one screen; a level-1 source that its handler cannot clear parks the core in the
dispatcher loop with `PS.INTLEVEL=1` and only higher-level interrupts still run; `x4emu mem read` on the
matrix map (0x600C2000 + 4·source) names the culprit; "instant" completion in a model is wrong whenever
the driver reads progress back. QMP `pmemsave` takes absolute paths, the HMP wrapper does not.

### Session 4, delegated work (Opus 5 agents under review)

- **CI** (`.github/workflows/ci.yml`, `Makefile` `PYTEST_ARGS`): Ubuntu 24.04, checkout with submodules,
  the apt list from CLAUDE.md in noble package names, pioarduino's PlatformIO core fork v6.1.19 with the
  extra pip pins the CrossPoint repo's own CI uses, `git config` for `git am`, the QEMU *tree* cached
  (source + build, keyed on `QEMU_REF` and the hash of `qemu-patches/*.patch`, no `restore-keys`: a
  near-miss would restore a `.x4pro-patched` marker hiding a different series), `~/.platformio` cached,
  caches saved before the tests, `make test PYTEST_ARGS="--basetemp=…"` so screenshots and `.x4emu/*/`
  logs are uploaded even when red. Verified locally: YAML, `bash -n` on every step, dry runs, the collect
  script against a fake workspace, package names on packages.ubuntu.com. Not verifiable here: the Linux
  build itself, runner speed against the tests' 90 s boot timeouts, and goldens from a firmware built
  without the gitignored `platformio.local.ini` (no EpdBus trace, another version string). Enable
  Actions on the repository once. Local hazard found on the way: `qemu/.x4pro-patched` is an mtime rule
  against `qemu/.git`, so a commit in the clone (patch export) makes `make setup` want to re-run `git am`;
  `touch qemu/.x4pro-patched` after exporting a patch.
- **`x4emu` package + `--json`** (`x4emu/{cli,commands,qmp,paths,output}.py`, `pyproject.toml`,
  `docs/x4emu.md`, `tools/x4emu` reduced to a shim that imports the package from the checkout and pins
  `X4EMU_ROOT`): `pipx install .` gives the `x4emu` console script; the checkout is found from
  `$X4EMU_ROOT`, else by walking up from the cwd to a directory with `tools/x4emu` and `qemu-patches/`,
  else the checkout containing the package. `--json` (global, before the subcommand) prints one object
  per command, errors as `{"error": …}` with the old exit codes; the human output is byte-for-byte the
  old one (the MCP server and the tests parse it). Verified with a CrossPoint boot through 27 commands in
  both modes and `make test` 16/16 after the switch.
- **The stock's light controls** (Opus agent, third of the squad): a pull-down panel over any page, opened
  by a slow drag from the portrait top edge (`x4emu swipe 5 240 300 240 --ms 600`), closed by a tap on
  the dimmed page. Brightness in 10 % steps (each moves the lit channel by 10 % of duty), nine colour
  presets Cool 4 … Warm 4 that mix the cool channel in as warm drops (Balanced = 119/99 ‰), Boost / Full
  Refresh / Sleep Lock / Light buttons; every change is a hardware fade and lands in NVS `lightBri`,
  `lightCT`, `lightOn`. `test_stock_light_controls_follow_the_sliders` with goldens `stock-light.png`
  and `stock-light-adjusted.png`, stable over three runs. Also learnt: a short power press is sleep in
  the stock ("Get Started" wallpaper, then deep sleep), a double click opens Cloud Download; a tap can be
  dropped right after a registered one (the test retries a dropped tap). Goal A's acceptance is complete;
  the device oracle (squad S2) is what remains.
- **Squads.** The owner asked that the work be organised as coordinator + agent squads from here, with
  lower models where appropriate; the protocol and the remaining plan cut into squads S1–S7 are
  `docs/NEXT_PHASE.md` §10.

### Session 4, later: the stock's "stalls" were its clock schedule; SPI transfers take their time

After the squad's suite run showed two new stock failures (a Home screen with an "External font failed
to load. Using built-in font." toast, and a reading-menu back tap that drew nothing), the panel traces
of both ended in an update's first half: PTOUT, PTIN, PTL, DTM1 48,000 bytes, then nothing. A DRAM +
PSRAM dump (`tools/tcbwalk.py --region`, new) showed every task alive: the UI task in its 2-tick event
loop, `epd_flush` in `ulTaskNotifyTake` with no job, both cores idle, SPI2 in the driver's idle state.
Two wrong theories cost an hour each — first that the GP-SPI model's instant completion lost a handshake
(the model now charges the real transfer time, which is right anyway: docs/hardware.md), then that the
missing external font on the card image derailed a render (the tests now boot a card built from the full
device files, which is right anyway: `stock_card` fixture). The truth came from a timeline: the "stalled"
updates completed at 21:13:00 and 21:14:00 exactly. **The stock pre-sends the frame it just showed as the
next update's old plane right after every refresh, and sends the new plane + DRF when the repaint is due
— immediately for a UI event, at the next minute for the status-bar clock.** A trace ending in DTM1 is
its idle state. Consequences: (1) "boot to three refreshes" waited for the first minute tick, 0..60 s,
which was the whole boot-time variance and the reason taps in tests landed at odd moments;
`boot_to_home` and `test_stock_boots_to_home…` now take refresh 2 (Home) plus an idle panel; (2) my
stall hunter's detector (no DRF within 6 s of a DTM1) flagged normal behaviour — its captures are not
evidence of anything; (3) the reading-menu back tap that "drew nothing" within 20 s is still not
explained (the tap was read; `tap` did not yet hold until read at the time), and the font toast needs the
card without the font, so both are covered by the fixture and CLI changes rather than understood.
GP-SPI timing: `hw/ssi/esp32s3_gpspi.c` keeps USR set and CS asserted for `bits / f_spi` with f_spi from
the CLOCK register (80 MHz / ((PRE+1)(N+1)) or the system clock), raises TRANS_DONE from a timer;
`state.spi2` gains `busy` and `transfer_ms` (a stock boot to Home: 307 ms of SPI). QEMU patch 0012.
First version timed every transfer and broke `test_boot_to_home_and_press_right` deterministically:
CrossPoint's Arduino SPIClass writes a frame as 940 polled 64-byte chunks, each now waiting for a QEMU
timer with ~1 ms slack, so a 12 ms frame took 1.5 s and the test's button press fell into that busy
window (CrossPoint polls no input while writing the panel). Transfers under 1 ms complete at once again.
`tools/tcbwalk.py --region FILE:BASE` reads task stacks that live in PSRAM (`pmemsave 0x3C6C0000 0x200000`).

**State at the end of session 4:** `make test` 20/20 (12 CrossPoint + 5 stock + 3 nvsedit, 4 min 34 s) on
QEMU with patches 0001–0012, the stock tests booting cold (`--boot-hold-power`) on the full device card.
Squad S1's Opus half is merged and committed; its coordinator half (the PHY cold-boot block, RTC IO,
the Home-pad key byte) is the next session's first work (`docs/NEXT_PHASE.md` §0.5, §3.2.6).
- **`nvsedit.py set-str / erase / redact`** (Opus agent, squad S1): strings are type 0x21, chunk 0xff,
  span = 1 + ceil(size/32), data field `<u16 size><u16 0xffff><u32 crc32(data)>` with the bytes in the
  following span-1 entries (0xff-padded, size counts the NUL); every entry of a span shares one state.
  `erase` and the retirement half of the setters blank the retired 32-byte slots to 0xff, so a value
  really leaves the image. `redact` = erase `user_config/{sta_ssid,sta_pwd,wifi_creds}` + `net_en = 0`;
  the redacted dump boots to Home pixel-identical to the golden (`tests/test_nvsedit.py`, 3 cases,
  9 s). Recipe in the module docstring; a redacted image may leave `images/`.
- **`x4emu run --boot-hold-power [MS]`** (Opus agent, squad S1): QEMU starts with `-S`, the power button is
  set over QMP before the first instruction, `cont`, release after MS (default 3000: early boot runs at
  about half real time here, the preflight wants 600 ms held when it samples GPIO3 at ~530 ms; 1800 is the
  measured threshold). The stock's preflight then logs `source=1 … hold=600ms decision=0 reason=11` and
  boots straight to Home: 7.3 s to a quiet Home against 8.1–9.4 s through deep sleep and a wake. The
  `stock` fixture uses it; `boot_to_home(name, wake=True)` keeps the old path. **Found on the way:** with
  `net_en=1` a POWERON cold boot makes ESP-IDF run the *full* PHY calibration (a DSLEEP wake reuses the
  calibration kept in RTC memory), and that path blocks after three `pll_cal exceeds 2ms` lines — the
  radio task stops with `ana_i2c`/`rf`/`saradc` frozen at analog-master transaction m1 block 0x6b reg
  0x02 = 0x4e while everything else runs. So `test_stock_wifi_fails_fast` alone still boots through the
  wake; answering that register (with the PLL lock flag, §3.2.6) is the coordinator's S1 item.
- **`swipe` holds every point until the firmware reads it.** The light-panel test failed once in the
  suite after the cold-boot change: the pull-down never opened, the trace showed no update after Home.
  The swipe streamed 31 points over 600 ms blind, and the GT911 model keeps only the latest point, so a
  late read collapses the gesture; each point is now held until consumed (0.5 s cap), the release too,
  and `swipe` takes `--quiet`/`--wait` like `tap`. Two runs green afterwards.

## 2026-09-06 — Session 5: squads in parallel (S1 close, S2 oracle, S4 replay), CI green

First session run end to end as `docs/NEXT_PHASE.md` §10 describes: the coordinator (Fable 5.1) formed four
agents at once — two Fable for the symbol-less firmware work, two Opus for the mechanical items — one owner
per file, one agent allowed in the QEMU tree (building in its own `qemu/build-s1` with `X4EMU_QEMU` pointing
at it, so the shared binary stayed stable for the others), briefs with the facts already known and a
mechanical acceptance each. Baseline at the start: 20/20 in 4:31.

- **CI (S3) went green on its first Linux run** once the owner pushed main (b7f5918): 12 min end to end,
  `make build` 9 min (QEMU + CrossPoint from scratch), `make test` 86 s, an artifact of 178 KB of screenshots
  and emulator logs (so the CrossPoint tests ran rather than skipped). Pushes are the owner's (GitHub Desktop):
  this Mac has no push credential and no `gh`; the public Actions API answers run/step/artifact queries
  without a login, the logs need one.
- **S1 (Fable; QEMU patches 0013–0015).** (1) The cold-boot "PLL" block was not the PLL. Core 0 was
  *spinning* at app 0x422e45ef on **0x6000E04C**: the PHY's DC-offset (DCO) calibration writes 0x113cf1 then
  0x113cf3 (bit 1 start), polls bit 24 with no timeout and takes bits 31:30 as two comparator outputs for a
  12-step binary search on two 9-bit codes (start 0x100, steps 124, 63, 32, 17, 9, 5, 3, 2, 1…, the last four
  averaged), five passes per calibration (function 0x422e4558; the PHY function table at 0x3fcef3d8 resolves to
  ROM `rom_pbus_force_test`, `rom_index_to_txbbgain`, `rom_pbus_set_dco`; the format string `dco:%d,%d,%d,%d`
  sits next to `pll_cal exceeds`). The `pll_cal exceeds 2ms` printer (0x422e0d48) polls
  `readReg_Mask(0x62, 0x07, 1, 1)` every 20 µs, 100 tries, after the calibration pulse on reg 0x00 bits 6/5
  (0x422e0c80): the lock flag is **0x62/0x07 bit 1**, not 0x0c (0x0c is read in the cap search and never
  checked). Both answered (`cal-cmp` property, `ana_answered[]`): a cold boot with `net_en=1` reaches
  `wifi:mode : sta` at 13.3 s, `force witi stop` +7.6 s, `phy_init: Saving new calibration data`, and **zero**
  `pll_cal` lines on both paths, as on the device (`state.ana_i2c.pll_cals` 7, `cal_starts` 168 cold; 0
  after a wake). `test_stock_wifi_fails_fast` boots cold now. (2) **Home pad.** `--trace-i2c` on `x4emu home`:
  the stock reads 0x814E → 0x90, then **one byte at 0x814F** every 11 ms while the pad is down — the GT911
  key-value byte that follows the point records (0x814F + 8·count, bit 0 = key 1; Linux's goodix driver reads
  it the same way; session 4's 0x8177 guess was wrong). A finger reads 8 bytes from 0x814F, track id first:
  the record starts at 0x814F, CrossPoint reads from 0x8150 and so sees X at its byte 0 (`hardware.md`
  corrected). Served → in the stock the pad is **Back, one level**: nav menu → Home (pixel-identical), reader →
  All Files (where the book was opened; the reading menu's arrow goes to the bookshelf instead).
  `test_stock_home_pad_acts_as_back`; `state.gt911.key_reads`. (3) **RTC IO** stored-value overlay
  (`x4pro.rtcio` at 0x60008400, W1TS/W1TC act on OUT/ENABLE/STATUS, `state.rtcio`); the stock programs
  TOUCH_PAD3/5/6/7/13/14, XTAL_32P/N and PAD_DAC2 (32 pad writes); no `x4pro/rtcio` line after a stock boot or
  a CrossPoint sleep/wake, asserted in both suites. (4) BM8563 **`base-epoch`** property
  (`x4emu run … -- -global driver=x4pro.pcf8563,property=base-epoch,value=N`, the `--` before raw QEMU
  arguments; `test_stock_rtc_pinned_by_base_epoch` reads 2021-03-04 05:06 back over I2C). Left open: a QMP
  `pmemsave` of DRAM from the running stock came back with 44 of 120 pages zero (`tcbwalk.py` found no task);
  the PC sufficed, so it was not chased — pause the VM first next time.
- **S2 (Fable, then Opus).** The stock's Developer row in the pull-down light panel (Memory / Developer /
  Screen Capture) is gated by a predicate that is a **compile-time stub returning 0** in 7.2.4 (app
  0x4233abe0: `entry; movi.n a2,0; retw.n`, the value the dev log prints as `developer_mode=%u`): no NVS key
  (the app's whole key table has none), no card file, and the About Device version-tap reveal (Device ID, SN
  Code, Build Time, Diagnostics Test → Test Mode with Factory Full Test / Aging Test) is a different mechanism.
  One byte (file offset 0x4eabe4 of the app, 0x02 → 0x12 = `movi.n a2,1`) plus the ESP checksum and SHA-256
  recomputed exposes the buttons; the patched app boots normally. Path: `swipe 5 240 300 240 --ms 600`,
  `tap 735 199` → "Screenshot saved", `/sdcard/screenshots/screenshot_YYYYMMDD_HHMMSS.xic` (48,024 bytes);
  `tap 735 347` writes `/sdcard/logs/dev_*.log`. **`.xic` decoded and verified**: 24-byte header (`XIC\0`,
  u16 width/height, version 1, levels 1 | 4, planes 1 | 2, u32 payload = stride·height·planes, the rest 0),
  1 bpp, MSB first, 1 = black, rows top-down, the upright portrait 480×800 = the landscape panel screenshot
  rotated 90° clockwise (the decoder rotates a capture 90° counter-clockwise to get the panel back); **0 pixels** from the panel (run 2; run 1 differed by 69 px, all the clock's
  minute rollover). The capture records the last fully painted base frame, not the translucent panel.
  `docs/xic.md`; `tools/xic2png.py` (decode, `--info`, `--diff`, `--encode`); `tools/stockdev.py` (the patch);
  samples under `tests/data/`. The first decoder version, written from the card's 320×96 font-preview sample
  before the writer was read, took header byte 8 for bits-per-pixel and invented a packed 2-bpp format — the
  writer says version / levels / planes and a second appended plane; corrected. Consequence for the device
  oracle: Screen Capture is unreachable on an unpatched device, so the oracle needs either the patched stock in
  an app slot (rule 2 allows app-slot writes after the backup — the owner's call) or photos.
- **S4 (Opus).** `x4emu record FILE` / `record --stop` journal the input commands as `{t_ms, cmd, args}` at the
  guest time issued; `replay FILE` waits for the guest clock and calls the same functions in-process;
  `wait-guest-ms` / `wait-guest-until` poll `state.uptime_us`; `run --deterministic` = `-icount 3` (+ the
  pinned RTC now that `base-epoch` exists). `tests/test_replay.py`: a CrossPoint flow replayed onto two fresh
  boots, 0 pixels apart. Finding: QEMU's QMP chardev serves **one client at a time** — a second connection
  blocks and times out after 5 s, sequential close/connect is free — so replay drops its connection before
  each step. With `--deterministic --fast-epd` CrossPoint reaches Home at guest 1.4 s in ~2.4 s wall.

Lessons: the console line names the wrong culprit as often as the right one — the `pll_cal` message pointed at
block 0x62 while the spin was on 0x6000E04C; take the PC first. A "the firmware ignores X" note needs an I2C
trace before it becomes a fact (the Home pad was reading a byte the model did not serve). A format guessed
from one sample is a guess until the writer's code is read. Running one QEMU owner with its own build
directory and `X4EMU_QEMU` let the other three agents boot the shared binary undisturbed for two hours.

**State at the end of session 5:** `make test` 40 cases in 7 min 15 s on QEMU with patches 0001–0015 (13
CrossPoint including `test_replay.py`, 8 stock including `test_stock_capture.py`, 19 fast). The first full run
lost one menu tap in `test_stock_wifi_fails_fast` (the stock read nothing; the panel trace ended in its idle
pre-sent plane) while the test passed alone in 34 s — the stock's known dropped tap under a loaded host — so
the stock tests' menu taps now go through `tap_repaints` (one retry). CI green on b7f5918; device untouched.

**Owner's device check (2026-09-06, end of session 5):** shown the emulator's stock screens upright (Home, nav
menu, light panel, Settings, All Files, a reader page — `tests/data/stock-home-panel.png` and the
`tests/golden/stock-*.png` goldens rotated `ROTATE_270`), the owner judged them **1:1 with the device**. That is
the device oracle for the stock firmware at the level of the eye; a pixel-exact `.xic` capture from the device
(S2-device) stays optional and needs the developer-patched stock on the device.

## 2026-09-07 — Session 6: squad S5 (grey fidelity, SDMMC noise, window tests) and the interface polish

Cost-balanced squad, as the owner asked: one Fable agent for the model whose behaviour had to be inferred
(grey), three Opus agents for the mechanical C work (SDMMC trace patch, window tests, then the SSD1677 fixes
the tests uncovered), one Sonnet agent for the Python interface polish. One agent per file set; QEMU work in
`qemu/build-s1` / `build-s2`, models work in per-agent `BUILD=` directories.

- **Grey shading, waveform level (Fable; repo `cbfe81e`, QEMU patch 0017, `docs/grayscale.md`).** The
  UC8279 X4 LUT tables are 7 groups × 7 bytes (header, four `level<<6 | frames` phases, repeat, tail); the
  family datasheet's 6-byte groups do not fit (0x86 would be 134 frames) and the 7-byte reading makes the
  five tables of a bank agree on their frame totals. CrossPoint's AA page: base frame with every glyph pixel
  black, then DTM1 = ~(base|lsb), DTM2 = ~(plane0^msb), PSR 0x37, the 49-byte AA bank, CDI 0x97, DRF, then
  both planes restored to the base without a refresh (page turns add a 42-byte settle bank). The model keeps
  a reflectance per pixel and maps it through the pixel's transition class (WW/BW/WB/BB by CDI's DDX
  polarity) with a saturating step `gray_k` per frame at VDH/VDL (default 28; the settle bank's 25 frames
  must saturate). **Finding:** the AA bank for LUT_VER 0x68 has BW and WB byte-identical, so anti-aliased
  text on this panel has three levels (0 / 84 / 255), not four; the old fixed table showed the dark-grey
  glyph pixels lighter (170) than the light-grey ones (85). Off by default (`waveform-gray`); 14,967 px of
  the reader page change when it is on, black and white counts identical. The owner was sent the crops.
- **SDMMC log noise (Opus; QEMU patch 0016, an Espressif file).** 0x800 is the ESP32-specific SDMMC_CLOCK
  register (card clock divider/phase), read-modify-written on every card operation; it and CLKDIV, CLKENA,
  TMOUT (reset 0xffffff40), CTYPE, UHS_REG, BMOD (SWR self-clearing), CARDTHRCTL, EMMC_DDR are stored now,
  CDETECT/WRTPRT answer from the SD bus, the compiled-out DEBUG macro became ten `dwc_sdmmc_*` trace events.
  A stock boot's qemu.log: 484,963 → 1,788 lines; CrossPoint 1,005 → 718. Next noisiest: `esp32s3.gpspi
  unhandled read +0x38` (SPI_DMA_INT_CLR, 608 per stock boot) and the `x4pro/iomux` logger (525 + 525).
- **Window and data-entry tests (Opus; `models/tests/test_windows.c`, repo `904d051`).** 28 cases, 115
  checks from the SSD1677/UC8279 datasheets and the freeink drivers. Twelve checks record model gaps:
  X-decrement windows (0x44 given high address first, as `Ssd1677Driver::setRamArea` sends with mirrorX)
  collapse to one column because `ssd_ram_write` wraps against `x_start`; the AM bit of 0x11 is ignored
  (modes 4..7 act like 0..3); the gate-scan byte of 0x01 never mirrors the glass. None reachable by
  CrossPoint or the stock as configured (X4 profiles NO_FLIP, data entry 0x01, the desk unit a UC8279); a
  ROTATE_180 SSD1677 board would render garbage. Also: "the post-refresh RED/DTM1 resync is modelled" was
  an overstatement — the drivers send it themselves. An Opus fix followed (below).
- **SSD1677 addressing fixed (Opus; repo `5f26ba3`).** `ssd_ram_write` follows the SSD16xx rule: AM (0x11 bit 2)
  picks Y as the fast axis, 0x44/0x45 are start and end in the direction of travel (so the driver's mirrored
  windows walk the whole window), a terminus is an equality in byte columns / rows, the fast axis returns to
  its start when the slow one steps; the 0x01 scan byte's TB bit reverses the gate scan in compose. The
  twelve XFAILs pass; NO_FLIP boards unchanged (`tests/test_panels.py` green on the rebuilt binary).
- **Interface polish (Sonnet; repo `6ec624f`).** `x4emu/api.py` runs a command in-process and returns its
  JSON object or human text (an `Out` collect mode); `x4emu shell` is a REPL with the CLI's exact output;
  `x4emu watch` a stdlib HTTP live view (`/`, `/panel.png`, `/state.json`; short QMP connections, so other
  commands keep working while it runs); the MCP server imports the package instead of shelling out, passes
  `efuse` (it was dropped before), takes `boot_hold_power`/`deterministic` on `emu_run` and adds
  `emu_record`/`emu_replay`/`emu_wait_guest_ms`. Two buffering bugs found and fixed on the way (block-buffered
  stdout hid the URL line from a pipe; a stray `{}` at shell exit in JSON mode). Human output of every
  existing command byte-identical, checked against the committed package. The cheapest model did this
  cleanly against a precise spec with mechanical acceptance — the right split.
- Skipped on purpose: the per-opcode BUSY timing table (§4). The device logs only carry PON 40 ms and DRF
  483 / 1327 ms, which are already the defaults; the stock logs no timings. Nothing to extract.

Lessons: a console line or a driver comment names a symptom, the datasheet names the mechanism — the AA bank
being three-level was in the driver's own comment ("BW/WB carry the dark-gray channel") all along; tests
written from the datasheet instead of from the model found three real gaps in an hour; one model owner per
file plus per-agent build directories let four agents share one checkout and one QEMU source tree without a
single collision.

**State at the end of session 6:** `make test` 44 cases in 6 min 53 s on QEMU with patches 0001–0017 (13
CrossPoint, 4 interface, 8 stock, 19 fast). The full run lost the first menu tap of `test_stock_screens_walk`:
`x4emu tap` reported "read by the firmware after 0.03s" and no refresh followed within 20 s — the stock read
the GT911 frame and did nothing with it, the second time in three full-suite runs (always the first or second
tap after Home, never alone). The stock tests' `tap()` retries once now, like `tap_repaints`; whether the
device drops such taps is an open parity question (`docs/NEXT_PHASE.md` §0.4). Device untouched.

## 2026-09-07 — Session 7: the owner's direction is the stock firmware; survey and first data-only tools

The owner: "I do intend on enhancing the stock firmware with help by you. Basically making it prettier with
better UX." The emulator becomes the workbench for modding the closed-source `xteink_app` 7.2.4; S6 is
re-scoped accordingly (`docs/NEXT_PHASE.md` §0.5). Cost-balanced again: one Fable survey, Opus for the
tools and probes, Sonnet for the tap-path hunt.

- **Survey (Fable; `docs/stock-firmware.md`).** Binary shape and segments (app 5.5 MB in a 7.9 MB slot);
  the C++ architecture from the typeinfo names; no FreeType — every glyph a pre-rendered 1-bpp bitmap; the
  `.xtf`/`.xtfont` external-font system (data by design); wallpapers from the card; a four-language string
  pack for every label; layout numbers computed from font metrics and theme structs, not immediates; risks
  (boot-preflight, otadata VALID = no auto-rollback for app0: use app1 + pending-verify on the device); the
  tooling gap (Ghidra + Xtensa recommended; `capstone` added to the venv); and a **Lua 5.5 app host** with
  drawing/refresh/gesture verbs and a hidden "Lua Apps" menu entry.
- **Labels (Opus; `tools/stockstrings.py`, `b003b01`).** list/get/set/compact/restore over three packs
  (labels 234 groups at 0x3c3f7d54, counts 18, the headerless toasts pack 688 groups at 0x3c490124). The
  survey's "~1 KB of padding" was wrong: 2/1/0 bytes; `compact` suffix-merges the label blob (497 bytes
  freed). The emulator renders appended labels (`test_stock_relabelled_menu`: the nav menu differs from its
  golden only inside the relabelled entry).
- **Wallpaper (Sonnet; no patch).** All Files → the image → tap again → **Set as Wallpaper** writes
  `lockscrWallp` and `shutWallp` (+ `lockscrIdle`/`lockscrShut` = 3); PNG 480×800 fits exactly, persists
  across a cold reboot; a landscape image is scaled to width with black bars on the real sleep screen (the
  preview rotates it — the two renderers disagree); a 100×150 one shows a placeholder and is still accepted.
  No separate Images/Wallpapers page exists in this build. The owner can do this on the device today.
- **Fonts (Opus; `tools/xtfont.py`, `532fb0a`).** Metric bytes `advance_x, advance_y, x_off, y_off`;
  rendering "Bookshelf" from the card's `.xtf` reproduces the Home title in 0 of 2260 pixels; header 0x14 =
  range count, 0x2c = glyph-data length, no tail; `.hot.xtfp` not required (the app generates the caches and
  restarts). A generated package is listed correctly, parses, and is **refused at load** ("External font
  failed to load", "Failed to switch system font") — until the loader (app 0x4210db20) was read: header 0x30 and
  0x34 are zlib CRC-32s of the glyph records and of the 52 header bytes before them. Written, **a converted
  Arial Bold installs through Settings → System Font, the stock restarts and draws Home in it** (`dd5e675`).
  `install --direct` cannot activate a font by itself: the UI switch writes the caches and the boot plan.
- **Lua host (Opus ×2; nothing committed).** A plain `XTApps/hello/` directory is never mentioned and All
  Files hides `/sdcard/XTApps`. The nav menu's gate A (VA 0x4234516a, `b6 29 07` → `b6 29 ff`) unhides
  **Preload List** and **Statistics** (both work); gate B (a stub twin of the developer one, 0x4233abe8) gates
  page id 0x09 but no eighth row appears. The lock-screen route: `user_config/lockscrLuaApp` (str) +
  `lockscrMode` = 2 is exactly what `set_as_lockscreen_app` writes, yet four boots painted the byte-identical
  "Get Started" guide — the loader path (`ScriptHostPagePresenter` 0x42129ea4 → `app.xtapp` → `assets.xtab`
  → plain `manifest.json` at 0x42064b50 → `index.lua`/`lockscreen.lua`) is complete and supports unwrapped
  directories, but nothing on the standby path constructs the presenter. Verdict: shipped disabled at build
  time; finding the stub is a call-graph job (Ghidra).

Lessons: the survey's guesses ("~1 KB padding", "an Images page") each cost an agent an hour — a survey
should mark every unverified number as such (it mostly did); a probe that reads the firmware's own writer
(what `set_as_lockscreen_app` stores) is worth more than trying values; `appdis.py` + capstone got the
agents to the gates, but "who calls this presenter" across 5 MB is where a disassembler with a call graph
is the cheaper tool.

**State at the end of session 7:** `make test` 65 cases in 7 min 50 s (13 CrossPoint, 4 interface, 12 stock,
36 fast); the full run lost one more stock tap — the book-row tap in `test_stock_screens_walk` produced a
refresh but left All Files on screen (the walk now verifies every screen against its golden through `step()`
and retries once; alone it passed three times). Committed on `main`, the owner pushes. Device untouched.

**Device oracle for the reader (2026-09-07, later):** the owner put `tests/mkepub.py`'s book on the device's
card (USB drive mode; `Test Book.epub` copied from the Mac, card ejected cleanly) and took CrossPoint's
screenshot chord on pages 1–3 (`/screenshots/Emulator-Test-Book/*_ch1_pN_*.bmp`, mirrored into
`images/device/sd-files/screenshots/`). Page 1 and page 2 differ from the emulator's reader pages (as base
frames: any ink → black, since the device BMP is the 1-bit frame CrossPoint sends before the AA planes) in
**307 pixels each, all the footer clock digits** (landscape x 780..792, y 404..461); the wrong page differs in
~80,000. `tests/golden/device-reader-page{1,2}-*.bmp` and `test_reader_pages_match_device_screenshots` keep
that. The device's screenshots cannot show the grey edges (1-bit), so the grey verdict still needs the
owner's eye or a photo.
