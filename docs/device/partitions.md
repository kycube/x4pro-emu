# Flash layout of the desk unit (decoded from the 2026-09-06 dump)

Partition table at 0x8000 (magic 0x50AA entries):

| Name | Type | Subtype | Offset | Size |
|---|---|---|---|---|
| nvs | data | nvs (0x02) | 0x009000 | 0x005000 |
| otadata | data | ota (0x00) | 0x00E000 | 0x002000 |
| app0 | app | ota_0 (0x10) | 0x010000 | 0x7E0000 |
| app1 | app | ota_1 (0x11) | 0x7F0000 | 0x7E0000 |
| spiffs | data | spiffs (0x82) | 0xFD0000 | 0x014000 |
| coredump | data | coredump (0x03) | 0xFE4000 | 0x01C000 |

otadata (0xE000): entry 0 `seq=1 state=2 (VALID) crc=0x4743989a`; entry 1 erased.
ESP-IDF selects slot `(seq-1) % 2 = 0` → **app0 boots**.

| Region | Content |
|---|---|
| 0x0 bootloader | ESP-IDF v6.0.1 second-stage bootloader, built Aug 14 2026 19:24:53, entry 0x403c8870, min rev v0.0, max v0.99 |
| app0 @0x10000 | `xteink_app` **7.2.4**, ESP-IDF v6.0.1, compiled Aug 14 2026 19:25:05, ELF SHA256 5e6f90d4…, 8 segments, entry 0x40379908, hash valid |
| app1 @0x7F0000 | **erased** (0xFF) |
| spiffs @0xFD0000 | 135 non-0xFF bytes in 80 KB (an almost empty SPIFFS) |
| coredump @0xFE4000 | erased |
| nvs @0x9000 | namespaces `xteink_sys`, `hw_calib` (`screenType=2`, `region=2`), `user_config` (WiFi credentials, `lightBri=100`, `lightCT=100`, `timeZone`, `language`, `activate`, `cloud_bind_st`, `otaPromptDay`), `misc`, `nvs.net80211`, `phy` (calibration blobs) |

`hw_calib/screenType = 2` maps, per `XteinkDetect.cpp`, to **UC8279** (1/0x0B = UC8179, 2/0x0C = UC8279, other = SSD1677).

The stock app contains driver classes for all three controllers (`XTEink::SSD1677_800x480`,
`UC8179_800x480`, `UC8279_800x480`, plus `ZHX_UC8279Base`), board tags `ESP32S3_X4_TL`,
`ESP32S3_X4_TL_SSD1677`, `ESP32S3_X4_CLA`, `ESP32S3_X4R2_CLA`, and `GT911Driver`,
`BM8563Driver`, `Cw2017PowerHal`.

eFuse BLOCK3 (user data) holds the ASCII string `X4CB02EN26082416646` (a factory serial).

The dump is the owner's copy of proprietary firmware and contains WiFi credentials in NVS.
It stays in `images/device/` (gitignored).
