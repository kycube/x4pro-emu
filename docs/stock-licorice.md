# XTOS Licorice (stock 261013, 26102901): the stock firmware's new generation

On 2026-10-13 the owner took the OTA update offered as "v261013" (released the evening before). It is not
a 7.x point release: the image calls itself **XTOS Licorice 261013** and was built from a different
project. None of the 7.x addresses, patch sites or string packs carry over. This page records what is
known so far; everything is from the device backup and the emulator, on 2026-10-13.

## The image

| | 7.6.8 (app0 until 2026-10-29) | Licorice 261013 (app1, booting until 2026-10-29) |
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
| Auto power-off | after 20 minutes idle (Settings → General) it deep-sleeps on its own: at 1,205 s of guest time under `--icount shift=2,sleep=off` (5 host minutes: it polls touch and the gauge too often for the idle skip to jump far) |
| Battery | the gauge is polled all along (54 I2C transactions in 90 s); the percentage is repainted with the minute clock (15 % showed within 90 s of `x4emu battery --soc 15`); `--charging on` shows the bolt at once |
| Images | a book cover is drawn as an ordered 1-bit dither: Licorice sends no LUTs (PSR REG 0, the panel's own waveforms), so nothing on it is grey; "Image mode" switches Built-in / External, not grey |
| Settings | all seven subpages open (Account, General, Display, Network with OPDS / Calibre wireless / KOReader sync, Buttons, Reading, About); About reads "X4 Pro-EA30", the MAC and serial from the emulated efuses and "XT Licorice 261013" |
| USB mode | comes up and waits for a computer ("USB mode — On"), since the USB OTG core is modelled (`docs/usb-otg.md`); before that TinyUSB asserted (`check_dwc2 177: ASSERT FAILED` … `tusb_rhport_init`) and the screen ended at "Could not start USB disk mode". Licorice restarts the CPU into the mode (two `RTC_SW_CPU_RST` banners). The built-in host reads its device descriptor, 0x303A:0x4002; no disk crosses the bus |
| Mini apps | a package built with `tools/xtapp.py pack --format licorice` installs through the UI ("Installed" toast, lands at `/sdcard/XTApps/<app_id>/`) and **runs**: Mini Apps (shown once ten taps on Settings → About → "Model" set NVS `fw/lua_nav`) lists it, a tap launches it, it takes taps, and the Home pad leaves it. The 7.x-style `app.xtapp` gives "Install failed". The format and the launch are in `docs/licorice-apps.md` |

**The owner's check (2026-10-13).** Licorice has no screen capture, so the pixel-exact parity method of
`docs/parity.md` has no device side here. The owner compared twelve emulator screens with the reader by eye
(Bookshelf, drawer, Desktop, All files, reader, light panel, Settings, Settings → Network, Settings → About,
USB mode, the sleep screen, an app install) and judged all of them right.

Licorice prints nothing through ESP-IDF's log on the console, but plain `printf` does reach it (the
TinyUSB asserts above), so the console is wired; the firmware's log level is simply off.

## 26102901 (2026-10-29): the second build

The owner took the OTA of 2026-10-29 ("26102901", released that day). It is a Licorice build, not a new
generation: same project and IDF, a new commit, the identity string now carrying a build number.

| | 261013 (app1, kept) | **26102901** (app0, booting) |
|---|---|---|
| `esp_app_desc` | `dragon_example` / `e77bdf08-dirty` | `dragon_example` / `87a73a0e-dirty` |
| built, IDF | Oct 13 2026 11:18:34, v6.1-dirty | Oct 29 2026 12:53:50, v6.1-dirty |
| size | 5,821,312 bytes | 6,446,912 bytes, 8 segments (DROM now from 0x3c2d0020) |
| identity strings | `XTOS Licorice 261013` | `XTOS Licorice 26102901` (eight digits: `stockver.release_tag` → `v26102901`) |

`images/device/stock-app0-v26102901.bin` (sha256 ea26cc60…, ELF sha256 df08a73f…). Backup
`images/device/flash-2026-10-29-v26102901-{a,b}.bin` (sha256 5ca66d1c…, both reads equal).

**On the device.** The OTA wrote the other slot as always: **app0 = 26102901**, otadata entry 0 seq 5 →
app0 boots; app1 still holds 261013 byte for byte; the pristine 7.6.8 that was in app0 is gone from the
device (it stays in the 2026-10-12/13 backups). Bootloader, partition table and coredump are unchanged, and the
efuses read byte for byte as before (`docs/device/efuse-*.txt` unchanged, re-read the same day).
NVS was rewritten throughout (all five sectors), and the `/radio` store gains `wifi_last.bin` and
`excerpt_digest.bin`.

**New secrets in NVS, now redacted.** 26102901 stores the WiFi network in the WiFi driver's own keys too
(`nvs.net80211/sta.ssid`, `sta.pswd`, `sta.apinfo`), a 16-hex-digit digest beside the new **startup
passcode** (`fw/boot_pin_fw`), a Bluetooth identity key (`nimble_bond/local_irk_1`), and `fw/wifi_on`.
The `redact` of 2026-10-13 knew none of them: a "redacted" copy of the new dump still held the password
three times (two live keys plus an erased NVS copy) and started WiFi at boot. `tools/nvsedit.py redact`
now erases them, sets `fw/wifi_on` = 0, and blanks every erased NVS slot; `tests/test_nvsedit.py` checks
on both Licorice dumps that no credential value the dump held is left anywhere in the image. No copy left
`images/`, the session scratchpad or pytest's temporary folders, and the leaky ones were deleted.

**In the emulator: it runs as 261013 does.** No model change. Home in refresh 1, PSR `3B 48`,
`epd_unknown_cmds` 0, and the same peripheral footprint (only the IO_MUX lines in `iolog_hot`; with WiFi
left on, the boot starts the PHY, which the radio stubs answer). Every Licorice test runs on both builds
(`stockkit.licorice`; 26102901's goldens are `tests/golden/licorice-v26102901-*.png`): Home and the
drawer, All files, the reader paging with the side buttons, the light panel driving both LEDC channels,
Settings, deep sleep and wake, USB mode, and a mini app installed, unlocked, launched, paged and left.

What the build changed, as the emulator shows it:

| | 261013 | 26102901 |
|---|---|---|
| Home | the **Bookshelf** tab | a **Recent ⌄** tab (a sort menu), "NEW" on an unread cover |
| Frontlight at boot | LEDC set up, duty 0 | LEDC untouched until the light is first used |
| Wake from deep sleep | resumes the last book | **Recent**, the book on top (not yet seen on the device) |
| Mini apps | "Mini Apps" | **"Ink App"** ("Install Ink App", "Leave this Ink App?"); the same package and unlock |
| App API | `g.ttf_measure`, `ttf_text`, `ttf_text_box` | gone (`docs/licorice-apps.md`) |
| App key | DROM 0x3c32c674 | the same key at DROM 0x3c3a4918 |

New strings point at what else arrived: a startup passcode, collections, dictionaries, phone pairing, an
Ink App lock, "Back to previous spot", and translations (about 16,000 new strings, a CA bundle among them).
None of that is walked yet. The owner has not compared 26102901's emulator screens with the reader.

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

- 26102901 on the device: whether a wake really shows Recent rather than the book, and the owner's look at its
  screens beside the reader; its new features (passcode, collections, dictionaries, phone pairing) are
  not walked.
- Mini apps beyond the code section: the assets / data / fonts sections are read but not yet built, and
  the >512 KB streamed code path is untested (`docs/licorice-apps.md` "Open").
- USB mode beyond the device descriptor (SET_ADDRESS, configuration, the mass-storage traffic): no
  computer sees the disk.
- What the RTC segments hold (the bootloader loads them; the emulator boots, so they are at least not
  in the way).
