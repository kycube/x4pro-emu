# Device inventory — Xteink X4 Pro on the desk

Captured 2026-09-06 (read-only operations only).

| Item | Value | Source |
|---|---|---|
| USB VID:PID | 0x303A:0x1001 | `ioreg -p IOUSB -l` |
| USB product string | "USB JTAG_serial debug unit" | ioreg |
| USB serial string | 98:C3:77:BE:EA:30 | ioreg (equals base MAC) |
| Serial port | /dev/cu.usbmodem31101 | `ls /dev/cu.*` |
| Chip | ESP32-S3 (QFN56) revision v0.2 | `esptool chip-id` |
| Features | Wi-Fi, BT 5 (LE), Dual Core + LP Core, 240MHz, Embedded PSRAM 8MB (AP_3v3) | esptool |
| Crystal | 40 MHz | esptool |
| USB mode | USB-Serial/JTAG | esptool |
| MAC | 98:c3:77:be:ea:30 | esptool |
| Reset into ROM | `--before default-reset` works first try | esptool |
