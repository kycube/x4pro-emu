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
| M3 home screen with the device's SD contents | done except the device-screenshot diff | needs the owner to take CrossPoint's screenshot chord on the device and expose the card over USB, then `x4emu screenshot --diff` against the BMP |
| M4 touch, I2C bus, battery, RTC | done | GT911 product ID / raw touch frame not read from the real chip (its ID registers are only reachable through a dev build) |
| M5 frontlight PWM, deep sleep | done | device wake-log comparison pending: the device auto-slept and only its power button wakes it |
| M6 stock firmware, other panels | stock runs to its idle loop after a power wake; variants tested in the emulator | stock's blocker (SAR ADC / SD-FATFS / an interrupt wait) not identified; UC8179 unverified on hardware |

Device state at the end of the session: app0 = CrossPoint 1.6.0-x4pro with the EpdBus trace
(`images/crosspoint-epdtrace.bin`), app1 = stock 7.2.4, bootloader/partition table/otadata
untouched; `tools/device.py restore-stock --yes` returns app0 to stock. The device is in deep sleep
(auto-sleep) and its USB port is gone until the power button is pressed.
