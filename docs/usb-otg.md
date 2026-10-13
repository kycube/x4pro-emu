# USB OTG: the stock firmware's USB mode

Written 2026-10-13. The model is `hw/misc/esp32s3_usb_otg.c` (qemu-patches 0036, `esp32s3.usb_otg: the
DWC2 OTG core in device mode, USB_WRAP, RTC_CNTL_USB_CONF`); the regression test is
`tests/test_usb_otg.py`, with goldens `tests/golden/licorice-usb-mode.png` and `stock768-usb-mode.png`.

## Why

Both stock generations turn the reader into a USB disk: XTOS Licorice from ≡ → "USB mode", 7.x from the
nav menu's "USB Mode". Either way the firmware restarts into the mode, hands the S3's internal
full-speed PHY from the USB Serial/JTAG to the USB OTG controller (a Synopsys DWC2 core at 0x60080000),
and starts TinyUSB's `dcd_dwc2` driver for a mass-storage device. Before this model the board answered
the region with a zero logger, and TinyUSB's first check found no Synopsys ID:

```
check_dwc2 177: ASSERT FAILED
dwc2_core_init 203: ASSERT FAILED
dcd_init 458: ASSERT FAILED
tud_rhport_init 598: ASSERT FAILED
tusb_rhport_init 118: ASSERT FAILED
E (7051) tinyusb_task: Init TinyUSB stack failed            # 7.6.8 only; Licorice logs nothing else
```

Licorice then showed "Error … Could not start USB disk mode. Exit and retry.", 7.6.8 "Error". On the
device the mode works (the desk unit showed a Mac the disk "XTEink X4 Pro", 0x303A:0x4002, under 7.x;
`docs/log.md`).

## What is modelled

**The core, in device mode** (one register file, 0x60080000..0x600BFFFF). The values a firmware can
read come from ESP-IDF's `soc/esp32s3/register/soc/usb_reg.h`, whose per-field defaults give each
register's reset value:

| register | value | what it says |
|---|---|---|
| GSNPSID (+0x40) | `0x4F54400A` | an OTG core (ID `0x4F54`, which `check_dwc2` wants), release 4.00a: `GRSTCTL.CSFTRST` clears itself (no `CSFTRST_DONE`, that came with 4.20a) |
| GHWCFG1 (+0x44) | `0x00000000` | |
| GHWCFG2 (+0x48) | `0x224DD930` | OTG_ARCH 2 (internal DMA), FS PHY type 1, no HS PHY, 6 device endpoints + EP0 |
| GHWCFG3 (+0x4C) | `0x010004B5` | data FIFO 256 words (= `OTG_DFIFO_DEPTH` in `usb_dwc_cfg.h`) |
| GHWCFG4 (+0x50) | `0xD3F0A030` | dedicated TX FIFOs, 4 IN endpoints + EP0 |
| GUSBCFG (+0x0C) | `0x00001440` reset | PHYSEL 1 read-only (the FS PHY), TRDT 5 |
| GRXFSIZ / GNPTXFSIZ / GNPTXSTS | `0x100` / `0x01000100` / `0x00040100` | |
| DIEPTXF1..4 | `0x10000200` | |
| DCFG / DCTL / DSTS | `0x08100000` / `0x00002000` / `0x00000002` reset | DSTS: full speed until a host says otherwise |
| DIEPCTLn / DOEPCTLn | `0x00008000` reset | USBACTEP |

Licorice reads only GSNPSID and GHWCFG2 of the ID registers (an access trace of its USB mode,
2026-10-13); 7.6.8 makes as many accesses up to the idle bus (22 reads, 19 writes).

Behaviour: `GRSTCTL` reads AHBIDLE and lets CSFTRST, HSFTRST, RXFFLSH and TXFFLSH clear at once (a soft
reset clears the interrupt and NAK state, DSTS and the RX queue); `GOTGCTL` reads CONID_B and BSESVLD,
as the GPIO-matrix inputs ESP-IDF's PHY setup ties to constants for a device give them (read back in
Licorice's USB mode: IDDIG, B-valid, VBUS-valid = constant 1, A-valid = constant 0); `GINTSTS` is
write-1-to-clear with CURMOD, OTGINT, RXFLVL, the global-NAK bits and IEPINT/OEPINT (from DAINT under
DAINTMSK) computed; `DCTL.SFTDISCON` is the soft disconnect; on the endpoint banks SNAK/CNAK drive
NAKSTS and EPDIS on an enabled endpoint disables it with EPDISBLD; `DIEPINTn.TXFEMP` reads 1 and
`DTXFSTSn` the FIFO's whole depth. The interrupt line is `ETS_USB_INTR_SOURCE` on the interrupt matrix:
GAHBCFG's global enable and `GINTSTS & GINTMSK`.

**The PHY switch.** USB_WRAP (0x60039000; `OTG_CONF` reset `0x001C0000`, `TEST_CONF`, `DATE`
`0x02102010`, from `usb_wrap_reg.h`) and the one RTC_CNTL word that muxes the internal PHY,
`RTC_CNTL_USB_CONF` (+0x120, bit 20 SW_HW_USB_PHY_SEL hands the mux to software, bit 19
SW_USB_PHY_SEL then picks OTG), are stored. Nothing electrical follows: the **USB Serial/JTAG console
keeps working** after the switch, where on the device the host would lose the serial port and see the
disk instead.

**What the firmwares do with it** (both alike in `state.usb_otg`): one core soft reset, one flush of
each FIFO, `dcd_disconnect` then `dcd_connect` (in Licorice's access trace DCTL `0x2002` → `0x2000`),
GAHBCFG `0x27` (global interrupt, buffer DMA), GINTMSK `0x80003004` (USB reset, enumeration done, OTG,
wake-up), the PHY on OTG (`usb_conf` `0x00180000`). The screen then says "On" (Licorice: no "Could not start" line), and the core
sits on an idle bus: no bus reset, no SOF, no SETUP, not one OTG interrupt. That is the reader in USB
mode with no computer attached. Exit restarts the firmware to its Home with the PHY back on the USJ.

## The built-in host

For a test that wants to see the firmware answer, the model plays one control transfer on request:

```
x4emu qmp '{"execute":"qom-set","arguments":{"path":"/machine/x4pro-usb-otg","property":"host","value":"enumerate"}}'
x4emu state          # usb_otg.host: step done, resets 1, setups 1, descriptor "12 01 …"
```

In guest time: a bus reset (`GINTSTS.USBRST`), its end 10 ms later (`ENUMDNE`, DSTS full speed at
48 MHz), `GET_DESCRIPTOR(Device)` with wLength 64 once EP0 OUT is armed for SETUP, the IN data stage
until a short packet, and a zero-length OUT status stage once EP0 OUT is enabled and no longer NAKs.
In buffer DMA, which both firmwares use, the SETUP bytes go to DOEPDMA0 and the data come from
DIEPDMA0 through the system address space; in slave mode they would pass through the RX queue and
FIFO 0. `"detach"` stops the host. The host refuses to start (a QMP error) unless D+ is pulled up:
PHY on OTG, pad on, core soft-connected.

Both firmwares answer `12 01 00 02 00 00 00 40 3a 30 02 40 00 01 01 02 03 01`: USB 2.0, class per
interface, EP0 64 bytes, **0x303A:0x4002**, bcdDevice 1.00, strings 1/2/3, one configuration. That is
the personality the desk unit enumerated as on a Mac, and on 7.6.8 every field matches the table
esp_tinyusb prints on the console ("USB Device Descriptor Summary"); `test_usb_otg.py` checks both.

## What is not modelled

- **No data transfer.** The built-in host stops after the device descriptor: no SET_ADDRESS, no
  configuration, no mass-storage (bulk) traffic, so the card is never offered to a computer and nothing
  on the host side mounts. There is no bridge to a real USB host (QEMU's usb-host or a USB/IP export).
- No SOF, suspend, resume, remote wake-up, disconnect detection or VBUS sensing; no host mode, HNP/SRP or
  LPM; no ISO or interrupt endpoints; the FIFOs hold nothing (what the firmware writes is taken at once)
  and the direct FIFO RAM window (+0x20000) reads 0.
- USB_WRAP's pull-up, pad and PHY bits and RTC_CNTL_USB_CONF only report (`state.usb_otg.phy`,
  `pad_enable`, `dp_pullup`); the USB Serial/JTAG is not disconnected by the switch.

## State (`x4emu state` → `usb_otg`)

`phy` (`otg` when RTC_CNTL_USB_CONF bits 20 and 19 are set and USB_WRAP selects the internal PHY, else
`usj`), `pad_enable`, `device_mode`, `soft_connect` (DCTL.SFTDISCON clear), `dp_pullup` (the override's
DP_PULLUP, else device mode and soft-connected, with the PHY on OTG and the pad on), `core_resets`,
`rx_flushes`, `tx_flushes`, `connects` / `disconnects` (SFTDISCON edges), `gahbcfg`, `gintmsk`,
`gintsts`, `dcfg`, `dctl`, `irq`, `irq_raises`, `fifo_words_in` / `fifo_words_out`, `reads`, `writes`,
`usb_conf`, `wrap_otg_conf`, and `host` (`step` idle / reset / enumdone / setup / data-in / status-out /
done / error, `error`, `resets`, `setups`, `descriptor` as hex bytes). The counters survive a system
reset (the restart into and out of USB mode); the registers do not.
