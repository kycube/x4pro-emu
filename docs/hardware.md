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
USB_SERIAL_JTAG 0x60038000, USB_WRAP 0x60039000, GDMA 0x6003F000, SYSTEM 0x600C0000, INTERRUPT 0x600C2000;
radio side: I2C_ANA_MST 0x6000E000, SENS 0x60008800, FE2 0x60005000, FE 0x60006000, RX/NRX 0x6001C000
(NRX at +0xC00), BB 0x6001D000, WiFi MAC 0x60033000, WDEV 0x60035000 (RNG at +0x7C).
Register offsets used by the models are listed in `docs/audit.md` ("Register offsets").

## Firmware on the desk unit

Delivered state: stock `xteink_app` 7.2.4 (ESP-IDF v6.0.1, built 2026-08-14) in **app0**; app1 erased;
otadata seq 1 → app0. Current state (2026-09-06): app0 = CrossPoint 1.6.0-x4pro (built with the EpdBus
trace patch), app1 = a copy of the stock 7.2.4 image, otadata untouched; `tools/device.py restore-stock`
reverses it. Panel identity from CrossPoint on the device: **UC8279**, `VER=00 0F 68 00 00 FLG=13`.
Stock partition table: nvs 0x9000/0x5000, otadata 0xE000/0x2000, app0 0x10000/0x7E0000, app1 0x7F0000/0x7E0000,
spiffs 0xFD0000/0x14000, coredump 0xFE4000/0x1C000 (`docs/device/partitions.md`). CrossPoint's own table
(`firmware/partitions.csv`): app0 0x10000/0x640000, app1 0x650000/0x640000, spiffs 0xC90000/0x360000,
coredump 0xFF0000/0x10000. The community installs CrossPoint by writing only the app at 0x10000 over the
stock bootloader and stock table (`firmware/README.md`); the emulator reproduces that (CrossPoint on the
stock bootloader/table/NVS reads `hw_calib/screenType=2` and boots to Home).

NVS (0x9000, 20 KB, three used pages): namespaces `xteink_sys`, `hw_calib` (`screenType=2`; the code also
knows `region` (1 CN / 2 overseas, unset on this unit) and `lightDS`), `user_config` (`pwrTimingVer`,
`sta_ssid`, `sta_pwd`, `wifi_creds`, `cfg_init`, `fbLangDone`, `net_en`, `cloud_bind_st`, `otaPromptDay`),
`misc`, `nvs.net80211`, `phy` (`cal_data`). `tools/nvsedit.py` lists and edits it. The flash is read in QIO
(0xEB, 24-bit address, 6 dummy cycles) after `esp_flash_init`; IDF reports the real chip as "generic" and
QEMU's `is25lp128` as "issi", which changes nothing observable so far. The eFuse user block holds the
serial `X4CB02EN26082416646`.

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

### UC8279 as the stock firmware drives it (emulator trace, 2026-09-06)

The stock `xteink_app` 7.2.4 talks to the panel through ESP-IDF's `spi_master` (interrupt + GDMA
channel 0 bound to SPI2, 24-bit-address transactions, frames as 60,000-byte DMA transfers) and
reads VER (0x70) once through the same host in half-duplex. Init after three RST pulses:
`00 37 4D` (PSR), `61 03 20 02 58` (TRES 800x600: the driver addresses 600 gates and uses 120..599),
`65 00 00 00 00` (GSST), `03 20` (PFS), `E1 02`, DTM2/DTM1/DTM2 planes, `50 97` (CDI), `E0 02` (CCSET),
`E5 1E` (TSSET), `04` (PON, 40 ms), `00 17 4D`, `12` (DRF, GC, 1.3 s). Page updates: PTIN, PTL
full window (`90 00 00 03 1F 00 78 02 57 01` = x 0..799, y 120..599), DTM1 48,000 bytes (the old plane,
always in full), PTOUT, PTIN, PTL of the changed window, DTM2 with that window's bytes only, PTOUT,
`50 D7`, `E0 02`, `E5 5A`, `03 20`, `E1 02`, sometimes the full PTL again, PTIN, `00 17 4D`, DRF (fast);
toasts and the status bar use small windows (a clock repaint sends 112 bytes). **The two halves of an
update are not sent together**: right after every DRF (once BUSY clears) the stock pre-sends the frame
it just showed as the next update's old plane (PTOUT, PTIN, PTL, DTM1 48,000 bytes), and the new plane +
DRF follow when a repaint is due — at once for a UI event, or at the next change of the minute for the
status-bar clock. So the third refresh after a wake (the clock) comes 0..60 s after Home, and a panel
trace that ends in a DTM1 is the idle state, not a stall (docs/log.md 2026-09-06, session 4). SPI2 runs
at 10 MHz (CLOCK 0x70c7): a 48,000-byte plane takes 38 ms, a 60,000-byte one 48 ms; the emulator's GP-SPI
model charges that time for transfers of a millisecond or more (`state.spi2.transfer_ms`, `busy`); shorter
ones (CrossPoint's 64-byte polled chunks) complete at once, since QEMU timers carry about a millisecond
of slack that would stretch a 940-chunk frame to over a second. The frontlight defaults to the warm channel (GPIO9, LEDC) at ~25 %.

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

LEDC low-speed PWM, 25 kHz, 10-bit, active-high: cool white on GPIO8, warm on GPIO9 (struct
`frontlight = {8, 25000, 10, true, 9}`). The stock app uses LEDC channels 4 and 5 (support doc);
CrossPoint's `FrontlightManager` goes through the IDF `ledc` driver on timer 0 and lands on channels
0 (GPIO8) and 1 (GPIO9), as the emulator's GPIO-matrix routing shows (`state.ledc`). The two are
mixed for colour temperature; a power-button double-click toggles the light on the X4 Pro.

The stock app lights the warm channel at 249 ‰ (DUTY 4080 = 255.0/1024) when it wakes, **fades it out
60 s after the last input** and back in on the next touch, with ESP-IDF's hardware fade: CONF1 =
DUTY_SCALE 1, DUTY_CYCLE 24, DUTY_NUM 255, DUTY_INC 0, DUTY_START 1 (255 steps × 24 periods at 25 kHz =
245 ms), DUTY_CHNG_END interrupt enabled for the fading channel. The emulator walks such a fade in guest
time (`state.ledc.channels[].fading/target_permille`, `state.ledc.fades`); `DUTY_R` follows it, which
`ledc_fade_isr` relies on to know the fade reached its target (docs/log.md 2026-09-06, session 4).

The stock's light controls are a **pull-down panel** (`BrightnessLayer`) opened over any page by a slow
drag from the portrait top edge (`x4emu swipe 5 240 300 240 --ms 600`; it covers landscape columns
0..369 and dims the page behind; a tap on the dimmed page closes it). Brightness 0..100 % in 10 % steps
(`−` at landscape (130, 433), `+` at (130, 45)): each step moves the lit channel by 10 % of duty
(100 % = 249 ‰ warm). Colour temperature is nine presets, Cool 4 … Cool 1, Balanced, Warm 1 … Warm 4
(`−` (245, 433), `+` (245, 45)); from Warm 4 the cool channel mixes in as warm drops (Warm 3 → 28/175,
Warm 2 → 59/149, Warm 1 → 87/125, Balanced → 119/99 ‰ cool/warm, roughly constant total). Buttons:
Boost (307, 419), Full Refresh (307, 299), Sleep Lock (307, 179), Light (307, 60) — Light switches the
frontlight off and on. Every change is a hardware fade. NVS `user_config/lightBri`, `lightCT` (0..100)
and `lightOn` follow the panel. A short power press is sleep in the stock (a "Get Started" lock
wallpaper, then deep sleep), not the light toggle CrossPoint has.

## I2C bus 39/38 device map

| Address | Device |
|---|---|
| 0x51 | BM8563 RTC, PCF8563 register-compatible (16 registers 0x00..0x0F, BCD) |
| 0x5D | GT911 touch (only while GPIO2 is LOW) |
| 0x63 | CW2017 fuel gauge: SoC reg 0x04; VCELL regs 0x02/0x03 (14-bit, mV = (raw·5 + 8) >> 4); reports 0 % until an 80-byte BATINFO profile is written to 0x10..0x5F |

## Power and sleep

Deep sleep: `esp_sleep_enable_ext1_wakeup(1<<3, ANY_LOW)`, rails held at their off level with `gpio_hold_en`
(RST HIGH, GPIO5 HIGH, GPIO2 HIGH), `gpio_deep_sleep_hold_en()`, `esp_deep_sleep_start()`
(`PowerManager.cpp`). No light sleep in CrossPoint (grep); it lowers the CPU clock when idle instead
(`HalPowerManager`, "Going to low-power mode"). CrossPoint sleeps on a power-button hold of ≥ 400 ms
(`getPowerButtonDuration()`; 10 ms when "short press = sleep" is set) or after its inactivity timeout;
a wake press must still be held ~1.3 s after the reset when `verifyPowerButtonWakeup()` samples it,
otherwise the firmware re-sleeps. In deep sleep the USB Serial/JTAG is unpowered: the port vanishes.

Register sequence (ESP-IDF `rtc_sleep_start`): `RTC_CNTL_WAKEUP_STATE.WAKEUP_ENA` (bits 15..31) =
trigger mask (EXT1 = bit 1), `INT_CLR`, `STATE0.SLEEP_EN` (bit 31), then spin on `INT_RAW &
(SLP_REJECT|SLP_WAKEUP)`. Deep sleep is announced by `DIG_PWC.DG_WRAP_PD_EN` (bit 31). On wake the ROM
sees reset cause 5 (DSLEEP); the app reads `SLP_WAKEUP_CAUSE` (+0x130, EXT1 = bit 1) and
`EXT_WAKEUP1_STATUS` (+0xE4, RTC IO bit; GPIO3 = RTC IO 3). The emulator's `x4pro.sleep` overlay
implements exactly these registers and pauses the VM while asleep.

## USB Serial/JTAG console

Register map in `docs/audit.md`. Output flows only while (a) ESP-IDF's connection-monitor tick hook keeps
seeing the SOF raw bit (`usb_serial_jtag_connection_monitor.c`) and (b) Arduino HWCDC's `connected` flag is
true, which the SERIAL_IN_EMPTY or SERIAL_OUT_RECV_PKT interrupts set and USB_BUS_RESET clears. CrossPoint sets
`tx_timeout_ms = 1`, so an unconnected console drops every line after 1 ms.

## Analog master, SAR/temperature sensor, radio blocks (what the stock's WiFi start needs)

Sources: `images/rom/esp32s3_rev0_rom.elf` (esp-rom-elfs 20241011, disassembled at the symbols named),
`regi2c_ctrl_ll.h`/`regi2c_defs.h` and `sens_reg.h` (ESP-IDF v5.5 headers), and the stock app disassembled
at the PCs of its polling loops (docs/log.md 2026-09-06, "Stock WiFi start"). The TRM documents none of it.

| Block | Base | Behaviour the firmware relies on | Evidence |
|---|---|---|---|
| I2C_ANA_MST (regi2c masters) | 0x6000E000 | +0x00 / +0x04 command word `block \| reg<<8 \| data<<16`, bit 24 write, bit 25 busy, bit 26 start; a read's answer is in bits 23:16 once busy clears. Blocks: BOD/ULP 0x61, radio 0x62..0x64 (master 1), BBPLL 0x66, 0x67, SAR ADC 0x69, 0x6a, 0x6b, DIG_REG 0x6D. +0x40 ANA_CONF0 (BBPLL cal STOP_FORCE_HIGH/LOW bits 2/3, CAL_DONE bit 24), +0x44 ANA_CONFIG (block enables, cleared bit = on; the ROM writes the whole word), +0x48 ANA_CONFIG2. | ROM `rom_chip_i2c_readReg_org` 0x400354fc, `rom_chip_i2c_writeReg` 0x40035818, `rom_get_i2c_hostid` 0x400354bc, `rom_i2c_master_reset` 0x4003726c; `regi2c_defs.h` |
| SAR2 power detector (same block) | 0x6000E050.. | +0x50 bit 1 start, bits 26:24 == 7 idle/done, bits 6/7 (`rom_en_pwdet`); +0x5C low 16 bits 0x16a, bits 19/21/23 toggled around a measurement; +0x60 bits 4:3 input select, bit 1; +0x80..+0x9C eight 13-bit samples. | ROM `rom_pkdet_vol_start` 0x40036a18, `rom_pwdet_sar2_init` 0x40036470, `rom_en_pwdet` 0x40036508, `rom_get_sar2_vol` 0x40036afc, `rom_read_sar_dout` 0x40036aa4 |
| SENS | 0x60008800 | MEAS1_CTRL2 +0x0C / MEAS2_CTRL2 +0x30: DATA [15:0], DONE bit 16, START bit 17, EN_PAD [30:19]; TSENS_CTRL +0x50: OUT [7:0], READY bit 8, CLK_DIV [21:14], POWER_UP bit 22 (the PHY polls READY with no timeout); PERI_CLK_GATE_CONF +0x104 (IOMUX_CLK_EN bit 31). | `sens_reg.h`; app 0x422aac19 |
| FE / FE2 (RF front end) | 0x60006000 / 0x60005000 | pbus register writes through +0xC8 (command word, polled for busy) and +0xCC (data); capture: +0x140, +0x144 bit 1 start, done flag +0x174 bit 16, results +0x148..+0x154 (I/Q sums). | app 0x422e2c68..0x422e2d1d; ROM `rom_pbus_*` |
| RX (NRX at +0xC00) | 0x6001C000 | +0x02C bits 30/26/25 configuration, bit 23 capture start; +0x08C[18:12] a level the PHY counts while a capture runs. | app 0x422e2c08, 0x422e2cfd |
| WiFi MAC | 0x60033000 | +0xD14: bit 1 command, bit 0 ready (first MAC access after `phy_init`); +0x000/+0x004 (+8n) station MAC address (bytes 4,5 masked into +0x004), +0x020/+0x024 (+8n) all-ones, +0x064 (+8n), +0x0D8 (+4n) per-queue bits, +0xC34/+0xC40. | app 0x4230be3c..0x4230c017 |

What the emulator does with them: the analog master and SENS are models (idle/done answers, values as QOM
properties, registers read back as written); FE/FE2/RX/BB/MAC are a storage stub with a table of status bits
that read as set (`esp32s3.rfstub`, `sticky` property). There is no radio: the driver's TX queue never
drains and ESP-IDF's own timeouts stop WiFi about 8 s after `wifi:mode : sta`.

## Simplifications the emulator makes (by design)

- The GPIO matrix is not modelled electrically: SPI2 transmits on its bus regardless of pin routing, and board
  devices read CS/DC/RST/SCLK/MOSI straight from the GPIO model's per-pin outputs.
- IO_MUX and RMT stay `unimp`; pull-up/pull-down settings written there are ignored, so the GPIO model carries a
  per-pin idle level itself (buttons, MOSI, RST idle HIGH).
- BUSY durations and the RTC follow guest time.
- USB OTG (TinyUSB MSC) and BLE are absent. WiFi has the register blocks its start touches (analog master,
  SENS, FE/RX/BB/MAC stub) but no radio: the driver comes up, finds no air and stops itself.
- SAR ADC oneshots and the temperature sensor answer fixed values (`sar1-data`, `sar2-data`, `tsens-out`);
  the analog registers behind the regi2c masters hold what was written (no PLL, no calibration results).
