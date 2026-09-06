# x4pro-emu — Xteink X4 Pro emulator (QEMU, ESP32-S3)

Linux/macOS-hosted emulator for unmodified Xteink X4 Pro firmware images, driven from a shell:
buttons, touch, console, panel screenshots, JSON state. Status by milestone is in `docs/log.md`.

## What is NOT emulated (how it shows up for a firmware developer)

- **USB mass storage / TinyUSB**: the "USB Drive" file-transfer screen does nothing; the host never sees a disk.
- **WiFi / BLE**: any radio call fails or times out; the stock app's WiFi start after boot will not connect.
- **Grayscale**: two-plane 4-level rendering is an approximation of the real waveform (labelled in `state`).
- **Deep sleep**: modelled as a machine reset with reset cause 5 and ext1 wake status on the power button.
  Power draw, RTC-domain retention beyond RTC memory, and hold latches are not simulated.
- **Timing**: BUSY durations, the RTC and PWM follow guest time and are configurable; nothing is cycle-accurate.
- **GPIO matrix / IO_MUX**: not modelled electrically. SPI2 always reaches the panel; pull settings are
  ignored (the GPIO model has its own per-pin idle levels).

## Layout

```
qemu/        Espressif QEMU (esp-develop @ febae182), branch x4pro; our changes also live in qemu-patches/
models/      pure-C device cores (SSD1677, UC8279/UC8179, GT911, ...) with host unit tests
tools/       x4emu CLI, mkflash.py, mksd.py, mkefuse.py, device.py
tests/       pytest end-to-end (boots an image, drives it, checks screenshots/console)
firmware/    crosspoint-reader checkout (+ freeink-sdk submodule) for reference builds
docs/        hardware.md (facts + sources), audit.md (brief audit), log.md (dated engineering log), device/
images/      gitignored: flash dumps, SD images, build outputs
```

Paths must not contain spaces (QEMU's configure and the ESP-IDF toolchain refuse them). The repo lives at
`/Users/mini/x4pro-emu`; `/Users/mini/xteink x4/x4pro-emu` is a symlink to it.

## Build

```
make setup      # python venv (.venv), firmware + qemu clones, brew/apt deps check
make build      # qemu (xtensa-softmmu) + CrossPoint x4pro image + models
make test       # models unit tests + pytest end-to-end (no device needed)
```

Manual equivalents: `qemu/build/qemu-system-xtensa` is built with
`../configure --target-list=xtensa-softmmu --enable-gcrypt --enable-slirp --enable-png --disable-user
--disable-capstone --disable-vnc --disable-gtk --disable-sdl --disable-docs && ninja qemu-system-xtensa`
(on macOS the `qemu-system-xtensa` target runs the code-signing step). Firmware:
`cd firmware && pio run -e x4pro`. Everything Python runs from `.venv/bin/python` (`-m esptool`,
`-m espefuse`).

## Boot a firmware image and take a screenshot (five commands)

```
.venv/bin/python tools/mkflash.py images/flash.bin --build firmware/.pio/build/x4pro
.venv/bin/python tools/mkefuse.py images/efuse.bin --dump docs/device/efuse-dump.txt
tools/x4emu run --flash images/flash.bin --efuse images/efuse.bin --fast-epd
tools/x4emu wait-refresh --timeout 60
tools/x4emu screenshot out.png
```

CLI reference: `tools/x4emu --help` (run, stop, reset, status, screenshot, press, hold, tap, swipe, home,
battery, light, log, wait-text, wait-refresh, state, mem, gdb). Instances live under `.x4emu/<name>/`
(console.log, uart0.log, qemu.log, qmp.sock, pid).

## The real device (rules, in priority order)

A physical X4 Pro is attached over pogo pins as `/dev/cu.usbmodem*` (USB Serial/JTAG, 0x303A:0x1001).

1. Nothing is written to the device without a verified double backup on disk
   (`tools/device.py backup`: two 16 MB reads, SHA-256 equal). Current backup:
   `images/device/flash-2026-09-06-{a,b}.bin`, `docs/device/flash-backup-sha256.txt`.
2. Never `erase-flash`, never `espefuse burn-*`, never write 0x0..0x10000 (bootloader, partition table,
   otadata) except to restore the verified backup byte for byte. Writing an OTA app slot
   (0x10000 or 0x7F0000 under the stock table) is allowed after the backup.
3. Do not touch the device during a transfer; the magnetic pogo adapter detaches easily. If a transfer
   breaks, check the device state before retrying.
4. GPIO19/20 are USB D-/D+: never run any pin or bus probe over them.
5. If the device stops responding, stop, write down what was done, and follow
   `firmware/docs/fix-bricked-xteink.md` and the CrossPoint web tools.
6. The flash dump contains the owner's WiFi credentials (NVS). It never leaves `images/`.

`tools/device.py` subcommands: `inventory`, `efuse-summary`, `backup`, `console [--reset]`, `image-sd`,
`flash-crosspoint` (refuses without a verified backup). Every write-capable command prints its plan first.

## Known facts about the desk unit

ESP32-S3 rev v0.2, 8 MB octal PSRAM, MAC 98:c3:77:be:ea:30. Stock `xteink_app` 7.2.4 (IDF 6.0.1) in app0,
app1 erased. Factory NVS says the panel is a **UC8279** (`hw_calib/screenType=2`). See `docs/hardware.md`.
