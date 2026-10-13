# XTOS Licorice (stock 261013): the stock firmware's new generation

On 2026-10-13 the owner took the OTA update offered as "v261013" (released the evening before). It is not
a 7.x point release: the image calls itself **XTOS Licorice 261013** and was built from a different
project. None of the 7.x addresses, patch sites or string packs carry over. This page records what is
known so far; everything is from the device backup and the emulator, on 2026-10-13.

## The image

| | 7.6.8 (app0, still on the device) | Licorice (app1, booting) |
|---|---|---|
| `esp_app_desc` project / version | `xteink_app` / `7.6.8` | **`dragon_example` / `e77bdf08-dirty`** |
| built, IDF | Sep 22 2026, v6.0.1 | Oct 13 2026 11:18:34, **v6.1-dirty** |
| size | 6,250,640 bytes | 5,821,312 bytes, 8 segments |
| IROM load address | app offset + 0x41da0000 | 0x42000020 (the default layout) |
| RTC segments | none | three (RTC slow 0x50000000, 0x500000cc; RTC fast 0x600fe000) |
| identity strings | `xteink_app` | `XT Licorice 261013`, `XTOS Licorice 261013` |

`images/device/stock-app1-v261013.bin` (sha256 661f23d2…, `esptool image-info`: checksum 0xef valid,
validation hash valid; ELF sha256 2ac6c769…). Backup `images/device/flash-2026-10-13-v261013-{a,b}.bin`
(sha256 5027bfc0…, both reads equal).

## What the update changed on the device

Compared region by region with the pre-parity backup of the same morning:

- **otadata**: entry 1, seq 4, VALID: **app1 boots**. app0 still holds the pristine 7.6.8 byte for byte;
  the 7.5.4 that was in app1 is gone from the device (it stays in the older backups).
- **Bootloader, partition table, coredump: unchanged.** The **efuses read exactly as before**
  (`docs/device/efuse-*.txt` unchanged): no flash encryption, no secure boot, `SECURE_VERSION` 0,
  download mode open. The update locked nothing.
- **The `spiffs` partition (0xFD0000, 80 KB) is now a LittleFS** holding `/radio/wifi_known.bin` and
  `/radio/bt_bonds.bin`. Every earlier dump had a SPIFFS there with only `/README.md`. Known WiFi
  networks now live outside NVS, so **`tools/nvsedit.py redact` also blanks this partition** when it
  holds the radio store (CLAUDE.md rule 6; `tests/test_nvsedit.py`). The firmware formats it again
  at the next mount.
- **NVS**: two sectors changed; a new namespace `fw` (`pwr_off`, `rd_combo`, `home_app` = `sys.home`,
  `lang`, `tz_min`) beside the familiar `user_config`, `hw_calib`, `phy`, `nvs.net80211`.

## In the emulator

The redacted dump copy runs as it is, with `--boot-hold-power` and a card from `images/device/sd-files`:

- **Home (the Bookshelf, "Open a book to start") in the first refresh**, `epd_unknown_cmds` 0,
  `iolog_hot` only the IO_MUX lines every stock shows. No new peripheral gap on that path.
- **One model fix was needed.** Licorice sends the UC8279 **PSR `3B 48`**: UD = 1 (the gate scan reversed)
  and SHL = 0 (the sources mirrored), streaming its 800 × 600 frame for that scan. CrossPoint and stock
  7.x send `37 4D`. The core honoured the two bits only on the UC8179, so Licorice came out upside down
  and 120 rows short. `models/epd_core.c` now applies them on both UC parts (the 7.x and CrossPoint mapping
  is unchanged; `models/tests/test_epd.c` `test_uc8279_scan`; `make test` 234 passed).
- **Touch works.** The ≡ opens a side drawer: Desktop, Bookshelf, All files, Cloud, USB mode, Settings,
  with the date. **Desktop** is a widget page (a large clock, the recent book, a month of reading
  time). **Settings** is regrouped: Account, General, Display, Network, Buttons (side keys, touch
  zones), Reading, About.
- **The console is silent** after the ROM banner on both USB Serial/JTAG and UART0: no ESP-IDF log
  lines. So the 7.x tests' `wait-text` markers (`main_task: Returned from app_main()`) do not exist;
  waits go by refresh count.
- `tests/test_licorice.py` boots it, checks the PSR, and compares Home and the drawer with
  `tests/golden/licorice-{home,drawer}.png` (4 s under icount).

The walk (2026-10-13, by hand, then `test_licorice_walk`, 20 s):

| Feature | In the emulator |
|---|---|
| All files, opening an EPUB, pagination | works; the right side button pages forward, the left back |
| Light panel (pull from the top edge) | brightness, colour temperature and the Wi-Fi / Bluetooth / Dark / Light toggles; the slider drives both LEDC channels through a perceptual curve (3 % = duty 1 of 1024 on the warm channel, the slider's right part about 12 % on each), the temperature moves the cool/warm split |
| Dark mode | the whole UI inverts |
| Home pad | Back, one level, as in 7.x |
| Deep sleep and wake | a held power button (Settings → General: "Power button Long 1s") sleeps with the "Get Started" screen, EXT1 on GPIO3; power wakes it with reset cause DSLEEP, and it **resumes the last book**, not the screen it slept on |
| Wi-Fi on | the full PHY calibration runs (168 calibration starts, 7 PLL calibrations in 2 s), then the radio idles: no networks to scan, nothing to join, the UI stays responsive |
| Bluetooth on | on in 0.3 s, a BT glyph in the status bar; the radio stub sees no access |
| Settings | all seven subpages open (Account, General, Display, Network with OPDS / Calibre wireless / KOReader sync, Buttons, Reading, About); About reads "X4 Pro-EA30", the MAC and serial from the emulated efuses and "XT Licorice 261013" |
| USB mode | comes up and waits for a computer ("USB mode — On"), since the USB OTG core is modelled (`docs/usb-otg.md`); before that TinyUSB asserted (`check_dwc2 177: ASSERT FAILED` … `tusb_rhport_init`) and the screen ended at "Could not start USB disk mode". Licorice restarts the CPU into the mode (two `RTC_SW_CPU_RST` banners). The built-in host reads its device descriptor, 0x303A:0x4002; no disk crosses the bus |
| Installing a `.xtapp` | **not yet**: the prompt appears as on the device, OK gives "Install failed" for the 7.x-style `app.xtapp` (below) |

Licorice prints nothing through ESP-IDF's log on the console, but plain `printf` does reach it (the
TinyUSB asserts above), so the console is wired; the firmware's log level is simply off.

## What the stock tools make of it

`tools/stockver.py identify` refuses it (no `stockver.d` file; project `dragon_example` is none of
the known ones), and so do `stockpatch.py`, `stockdev.py` and `stockstrings.py`, as designed. A version
file would not be enough, because the things the files describe are gone:

- **No developer menu and no Screen Capture**: no `Developer` or `Screen Capture` string in the image,
  so `stockdev.py` has nothing to enable and **the parity harness (`docs/parity.md`) has no capture
  path on Licorice**. `.xic` still appears in the image (4 hits) for page caches and covers.
- **No XTTM string packs** (`stockstrings.py` edits those) and no `Presenter`/`KeyboardLayer`
  class names (7.6.8 has 230 and 5): the UI layer was rewritten, so `docs/stock-firmware.md`'s map
  does not apply.

## Lua apps: installed from a package now

7.5.4–7.6.8 showed a Mini Apps row as soon as `/XTApps` held an app folder. Licorice does not: with
`apps/gull-rock` copied in by `tools/xtapp.py install`, neither the drawer nor the Desktop lists it. Its
strings describe a different flow: "No mini apps yet", "Install a .xtapp from All Files", "Install this
package?", "Installed", "Install failed", "Cannot run yet", "Launch failed", "Uninstall". Installed apps go to
`/sdcard/XTApps/%s`, and there are per-device key-wrapping names (`P256-ECDH-HKDF-SHA256-AESKW`,
`xteink.xtapp.appdek.wrap.v2`, `xteink:store-seed-binding:v1`) and an app store URL
(`https://www.xteink.com/pages/xteink-apps?v=`). The manifest reader still knows `app_id`,
`display_name`, `index.lua`, `permissions` with `sys.battery` and `sys.status`, and adds `net.http` and
`net.https`.

## Open

- Whether an unsigned package installs at all (the `.xtapp` format Licorice expects, and whether it
  checks a signature or only unwraps a key when one is present), and where Mini Apps lives in the UI.
- USB mode beyond the device descriptor (SET_ADDRESS, configuration, the mass-storage traffic): no
  computer sees the disk.
- What the RTC segments hold (the bootloader loads them; the emulator boots, so they are at least not
  in the way).
- A Ghidra pass over the new image (`tools/ghidra_stock.py` assumes the 7.x layout and needs teaching).
