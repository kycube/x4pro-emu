# Xteink X4 Pro hardware, as the emulator models it

Every fact here cites its source. "struct" means `firmware/freeink-sdk/libs/hardware/BoardConfig/include/BoardConfig.h`,
`constexpr BoardProfile XTEINK_X4_PRO` (line 1495 at freeink-sdk cb9167d). "doc" means
`firmware/freeink-sdk/docs/xteink-x4pro-support.md`. "device" means a measurement on the desk unit,
recorded under `docs/device/`. When the doc and the struct disagree, the struct wins. See
`docs/audit.md` for the claim-by-claim audit of the original brief.

## SoC

| | Value | Source |
|---|---|---|
| Chip | ESP32-S3 (QFN56), silicon rev v0.2 (wafer major 0, minor 2), 40 MHz crystal | device: `esptool chip-id`, `efuse-summary.txt` |
| Flash | 16 MB external, QIO at runtime (`spi_flash: flash io: qio`), DIO for the bootloader | device boot log; `platformio.ini` `flash_mode = dio` |
| PSRAM | 8 MB embedded octal (AP vendor id 0x0d, 64 Mbit, "AP_3v3"), 80 MHz | device boot log, efuse `PSRAM_CAP=8M`, `PSRAM_VENDOR=AP_3v3` |
| MAC | 98:c3:77:be:ea:30 | device |
| eFuse | no secure boot, no flash encryption, all key purposes USER, USB Serial/JTAG enabled | `docs/device/efuse-summary.txt`; raw words in `efuse-dump.txt` (replayed by `tools/mkefuse.py`) |
| USB | native USB on GPIO19 (D-) / GPIO20 (D+); console is the USB Serial/JTAG peripheral; TinyUSB MSC only from the file-transfer screen | doc; device enumerates 0x303A:0x1001 "USB JTAG_serial debug unit" |

Peripheral bases (ESP-IDF v5.5 `reg_base.h`): GPIO 0x60004000, RTC_CNTL 0x60008000, RTC_IO 0x60008400,
IO_MUX 0x60009000, I2C0 0x60013000, LEDC 0x60019000, SPI2 0x60024000, SPI3 0x60025000, SDMMC 0x60028000,
USB_SERIAL_JTAG 0x60038000, USB_WRAP 0x60039000, GDMA 0x6003F000, SYSTEM 0x600C0000, INTERRUPT 0x600C2000.
Register offsets used by the models are listed in `docs/audit.md` ("Register offsets").

## Firmware on the desk unit

Stock `xteink_app` 7.2.4 (ESP-IDF v6.0.1, built 2026-08-14) in **app0**; app1 is erased; otadata seq 1 → app0.
Stock partition table: nvs 0x9000/0x5000, otadata 0xE000/0x2000, app0 0x10000/0x7E0000, app1 0x7F0000/0x7E0000,
spiffs 0xFD0000/0x14000, coredump 0xFE4000/0x1C000 (`docs/device/partitions.md`). CrossPoint's own table
(`firmware/partitions.csv`): app0 0x10000/0x640000, app1 0x650000/0x640000, spiffs 0xC90000/0x360000,
coredump 0xFF0000/0x10000. The community installs CrossPoint by writing only the app at 0x10000 over the
stock bootloader and stock table (`firmware/README.md`).

## Panel: 800x480 mono e-paper

Three controller variants share the glass and the pins: **SSD1677**, **UC8179**, **UC8279** (X4 800x480
variant). The SDK decides at boot with a bit-banged probe (below). The desk unit's factory NVS
`hw_calib/screenType = 2` means **UC8279** by the SDK's mapping; the `[XTDET]` line from a CrossPoint boot on
the device is the final word (pending, needs CrossPoint flashed).

| Signal | GPIO | Notes |
|---|---|---|
| SCLK | 12 | SPI2 (FSPI) via Arduino `SPIClass`, mode 0, MSB first, no DMA |
| MOSI | 11 | also the half-duplex read line during the probe (firmware switches it to INPUT_PULLUP) |
| CS | 13 | driven by `digitalWrite` around every transfer |
| DC | 18 | LOW = command, HIGH = data |
| RST | 14 | pulse: high 10 ms, low 10 ms, high 10 ms (`EpdBus::reset`) |
| BUSY | 6 | input; SSD1677: busy = HIGH; UC parts: idle = HIGH (`BusyPolarity::UcIdleHigh`) |

Source: struct `display = {12, 11, 13, 18, 14, 6, PIN_UNASSIGNED}`, `displaySpiHz = 20000000`
(stock uses 5 MHz). No MISO pin. No external EPD PMIC; the controller's internal booster (0x0C) makes the rails.
GPIO1 (`power.latch0`) is driven HIGH first thing at boot as the peripheral rail enable.

### Boot-time panel probe (`XteinkDetect.cpp`)

Runs before the display driver, on every boot and wake, with `digitalWrite`/`digitalRead` at ~500 kHz
(1 µs delays), no SPI peripheral:

1. CS high, SCLK low, DC low, MOSI output, BUSY input. RST: hold released (`gpio_hold_dis`), high 2 ms, low
   for 1 ms (pass 1) or 50 ms (pass 2 if pass 1 matched), high. Then 30 ms.
2. Command + read: MOSI output, DC low, CS low, 1 µs, clock the command MSB first (set MOSI, 1 µs, SCLK high,
   1 µs, SCLK low), DC high, MOSI → INPUT_PULLUP, 1 µs, then per byte: 1 µs, sample MOSI while SCLK low,
   SCLK high, 1 µs, SCLK low. CS high, MOSI → output.
3. Reads 1 byte with 0x71 (FLG) and 5 bytes with 0x70 (VER). A UC part is claimed only if FLG is neither
   0x00 nor 0xFF with bit 0 set, VER is not five identical bytes, and both passes agree on VER.
4. If FLG looked driven, it also reads 0xA2 (RMTP): 1 dummy byte then 48 MTP bytes; a UC part can be
   confirmed from MTP alone (byte 0 == 0xA5, or a non-uniform dump that repeats exactly).
5. VER byte 2 (LUT_VER) selects the driver: 0x02 / 0x68 / 0x69 → UC8279 (`uc8279X4Driver`), anything else →
   UC8179. A real UC8179 was seen returning `VER 00 00 01 FF FF, FLG 0x13`.
6. Afterwards every probe pin goes to INPUT (CS INPUT_PULLUP).

Emulator consequences: the GPIO model lets a board device drive a pin the firmware has switched to input, with
the pull-up as the idle level; the SSD1677 model ignores 0x70/0x71/0xA2 (line floats to 0xFF); the UC models
answer them on the bit-banged clock. The verdict is printed as `[XTDET] bus probe VER=… FLG=… -> …` but only
while HWCDC believes the host is connected.

### SSD1677 command stream (`Ssd1677Driver.cpp`, `ssd1677DefaultConfig`)

Init: 0x12, fixed 10 ms, wait BUSY; 0x18 0x80; 0x0C AE C7 C3 C0 80; 0x01 DF 01 02; 0x3C 0x80; RAM window
(0x11 0x01; 0x44 x0 x1; 0x45 yEnd yStart (high row first); 0x4E; 0x4F); 0x46 0xF7 + wait; 0x47 0xF7 + wait.
Frames: 0x24 (BW) and 0x26 (RED = previous frame for differential updates, second plane for grayscale), 48,000
bytes each, 100 bytes per row, 1 = white. Refresh: 0x21 (0x00 fast, 0x40 otherwise); 0x3C 0xC0; half only:
0x1A 0x5A; 0x22 (0xF7 full, 0xFC fast, 0xD7 half); 0x20; then BUSY high for the waveform (doc: full ~1800 ms,
fast ~500 ms; device measurement pending). Power-off: 0x3C 0x80, 0x22 0x03, 0x20, 200 ms + wait. Deep sleep:
0x10 0x03 (RAM discarded). Grayscale: 0x32 + 105 LUT bytes, 0x03, 0x04 ×3, 0x2C, 0x3C 0xC0, then 0x22 0xCC
(or 0xC7 factory mode) + 0x20. First paint after boot is promoted to HALF (0xD7).

### UC8279 / UC8179 (X4 Pro variants)

Command set (`Uc8279X4Driver.cpp`): 0x00 PSR, 0x02 POF, 0x03 PFS, 0x04 PON, 0x07 DSLP (0xA5), 0x10 DTM1 (old
plane), 0x12 DRF, 0x13 DTM2 (new plane), 0x30 PLL, 0x50 CDI, 0x61 TRES, 0x65 GSST, 0x90 PTL, 0x91 PTIN,
0x92 PTOUT, 0xE0 CCSET, 0xE1 gate scan, 0xE5 TSSET, 0x20..0x24 LUT banks (49 or 42 bytes). BUSY_N idle HIGH,
polled with `delay(1)`. Full details are read at M2/M6 when the model is written.

## Touch: GT911

| | Value | Source |
|---|---|---|
| I2C | SDA 39, SCL 38, 400 kHz, address 0x5D (alt 0x14), Arduino `Wire` (I2C0) | struct |
| INT | GPIO10 (address select during reset; read as a level afterwards, LOW = pending; CrossPoint does not depend on it) | struct (the doc's I2C-map paragraph has INT/RST swapped; stale) |
| RST | GPIO4 | struct |
| Power | GPIO2 driven LOW (active-low enable) with GPIO1 HIGH | struct `powerEnable=2, powerEnableActiveHigh=false` |
| Mount | portrait: reports X 0..480, Y 0..800 on the landscape panel → `swapXY=true, flipX=false, flipY=true`, raw ranges X 0..799 / Y 0..479 after the swap | struct ("confirmed by corner-tap") |
| Home pad | bit 4 of status 0x814E (capacitive key, not a GPIO) | `InputManager.cpp` |

Bring-up (`beginGt911`): GPIO2 LOW, 50 ms; `Wire.begin(39, 38, 400000)`, timeout 10 ms; reset dance with INT
driven to the select level: INT/RST outputs, RST LOW, INT=level, 10 ms, RST HIGH, 10 ms, INT=level, 50 ms,
INT → INPUT, 50 ms; probe 0x5D then 0x14 by address ACK (with INT LOW first, then the HIGH level if nothing
answered). No config upload; the chip self-loads. Polling (`pollGt911`): write 16-bit register MSB first,
repeated start, read. 0x814E: bit 7 buffer ready, bit 4 Home down, bits 0..3 point count. Then count×8 bytes
from 0x8150 (X lo, X hi, Y lo, Y hi, size lo, size hi, reserved, track id; coordinates at byte 0), then write
0x00 to 0x814E.

Touch → panel mapping (`pollGt911`): `sx = swapXY ? rawY : rawX`, `sy = swapXY ? rawX : rawY`, map through
the raw ranges, then `flipY: y = 479 - y`. The CLI inverts this: a landscape tap (px, py) becomes GT911
raw X = 479 - py, raw Y = px.

## Buttons and charger

Left GPIO0 (also the boot strap), Right GPIO7, Power GPIO3: plain digital, active-low, `INPUT_PULLUP`
(struct `input = {…, up=0, down=7, power=3, powerActiveHigh=false}`). Charger STAT on GPIO21, input, no pull,
HIGH = charging (struct `batteryChargeStatus=21`, `batteryChargeStatusActiveHigh=true`). Recovery mode:
Power-button wake with Right (Down) held (`main.cpp`).

## SD card: native SDMMC

Slot 1, 1-bit: CLK 41, CMD 42, DAT0 40, internal pull-ups, 40 MHz (struct `sdmmc = {41, 42, 40, -, -, -, 1}`).
Card data path gated by GPIO5 active-low: mount pulses it HIGH 80 ms then LOW 120 ms, runs `sdmmc_card_init`
plus a real sector-0 read, and retries the whole sequence on failure; GPIO5 stays LOW while the card is in use
(doc; `SdmmcBlockDevice::begin`). The emulator ignores GPIO5 and logs its level.

## Frontlight

LEDC low-speed channels: cool white GPIO8 on channel 4, warm GPIO9 on channel 5, 25 kHz, 10-bit, active-high
(struct `frontlight = {8, 25000, 10, true, 9}`). `FrontlightManager` mixes the two for colour temperature.

## I2C bus 39/38 device map

| Address | Device |
|---|---|
| 0x51 | BM8563 RTC, PCF8563 register-compatible (16 registers 0x00..0x0F, BCD) |
| 0x5D | GT911 touch (only while GPIO2 is LOW) |
| 0x63 | CW2017 fuel gauge: SoC reg 0x04; VCELL regs 0x02/0x03 (14-bit, mV = (raw·5 + 8) >> 4); reports 0 % until an 80-byte BATINFO profile is written to 0x10..0x5F |

## Power and sleep

Deep sleep: `esp_sleep_enable_ext1_wakeup(1<<3, ANY_LOW)`, rails held at their off level with `gpio_hold_en`
(RST HIGH, GPIO5 HIGH, GPIO2 HIGH), `gpio_deep_sleep_hold_en()`, `esp_deep_sleep_start()`
(`PowerManager.cpp`). No light sleep in CrossPoint (grep). Wake cause is read from RTC_CNTL at boot;
"after USB power" does not sleep on the X4 Pro.

## USB Serial/JTAG console

Register map in `docs/audit.md`. Output flows only while (a) ESP-IDF's connection-monitor tick hook keeps
seeing the SOF raw bit (`usb_serial_jtag_connection_monitor.c`) and (b) Arduino HWCDC's `connected` flag is
true, which the SERIAL_IN_EMPTY or SERIAL_OUT_RECV_PKT interrupts set and USB_BUS_RESET clears. CrossPoint sets
`tx_timeout_ms = 1`, so an unconnected console drops every line after 1 ms.

## Simplifications the emulator makes (by design)

- The GPIO matrix is not modelled electrically: SPI2 transmits on its bus regardless of pin routing, and board
  devices read CS/DC/RST/SCLK/MOSI straight from the GPIO model's per-pin outputs.
- IO_MUX and RMT stay `unimp`; pull-up/pull-down settings written there are ignored, so the GPIO model carries a
  per-pin idle level itself (buttons, MOSI, RST idle HIGH).
- BUSY durations and the RTC follow guest time.
- USB OTG (TinyUSB MSC), WiFi, BLE are absent.
