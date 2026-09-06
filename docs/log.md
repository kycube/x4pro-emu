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
