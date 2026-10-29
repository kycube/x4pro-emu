"""USB mode on the stock firmware: the ESP32-S3's USB OTG core (Synopsys DWC2, full speed) in device
mode, hw/misc/esp32s3_usb_otg.c, docs/usb-otg.md.

Both stock generations turn the reader into a USB disk with TinyUSB on that core. They restart into
the mode, switch the internal PHY from the USB Serial/JTAG to OTG (RTC_CNTL_USB_CONF, USB_WRAP), reset
the core and connect. Before the model the region read as zero: TinyUSB's check_dwc2() found no
Synopsys ID (`check_dwc2 177: ASSERT FAILED` and four more on the console) and the screen said "Error"
(Licorice also "Could not start USB disk mode. Exit and retry."). Now TinyUSB comes up and the
reader waits as on the device with no computer attached: "On", the bus idle (no reset, no SOF, no
SETUP, not one OTG interrupt). The model's built-in host then resets the bus and reads the device
descriptor the firmware hands back: 0x303A:0x4002, the mass-storage personality the desk unit showed a
Mac in 7.x's USB mode (docs/log.md), and on 7.6.8 the same fields as the firmware's own console table.
The USB Serial/JTAG console keeps working throughout (on the device it would go away). Skips without
the dumps (stockkit.stock_flash redacts every copy).
"""
import json
import re

import parity                                          # tools/ (conftest puts it on sys.path): landscape()
from stockkit import (DUMP_768, HAMBURGER, SKIP_IDLE, booted, boot_to_home,  # noqa: F401
                      golden_check, licorice, settle, state, stock_card, tap, wait_for, x4emu)

OTG = '/machine/x4pro-usb-otg'
LICORICE_MENU = (94, 33)                    # the ≡ at the right of the Bookshelf tabs (test_licorice.py)
LICORICE_USB_ROW = parity.landscape(330, 493)    # the drawer's "USB mode" row
LICORICE_EXIT = parity.landscape(240, 760)       # "Exit" under the picture
USB_ROW_768 = parity.landscape(329, 330)         # 7.x nav menu: Read, All Files, USB Mode, Cloud, Settings

# GET_DESCRIPTOR(Device) as both firmwares answer it: bcdUSB 2.00, class 0 (per interface), EP0 64 bytes,
# idVendor 0x303A (Espressif), idProduct 0x4002, bcdDevice 1.00, strings 1/2/3, one configuration
DEVICE_DESCRIPTOR = '12 01 00 02 00 00 00 40 3a 30 02 40 00 01 01 02 03 01'


def otg(name):
    return state(name)['usb_otg']


def in_usb_mode(name):
    """The usb_otg state once the firmware has the PHY on OTG and the core soft-connected."""
    u = otg(name)
    return u if u['phy'] == 'otg' and u['soft_connect'] and u['connects'] else None


def check_waiting_for_a_host(u):
    """TinyUSB initialised the core as a device and sits on an idle bus."""
    assert u['phy'] == 'otg' and u['usb_conf'] == '0x00180000', u        # SW_HW_USB_PHY_SEL + SW_USB_PHY_SEL
    assert u['pad_enable'] and u['device_mode'] and u['soft_connect'] and u['dp_pullup'], u
    assert u['core_resets'] == 1 and u['rx_flushes'] == 1 and u['tx_flushes'] == 1, u
    assert u['disconnects'] == 1 and u['connects'] == 1, u               # dcd_disconnect, then dcd_connect
    assert int(u['gahbcfg'], 16) & 0x21 == 0x21, u                       # GLBLINTRMSK + DMAEN: buffer DMA
    assert int(u['gintmsk'], 16) & 0x3000 == 0x3000, u                   # USBRST + ENUMDNE unmasked
    assert not u['irq'] and u['irq_raises'] == 0, u                      # nothing on the bus
    assert u['host'] == {'step': 'idle', 'error': '', 'resets': 0, 'setups': 0, 'descriptor': ''}, u


def enumerate_once(name):
    """The built-in host: bus reset, GET_DESCRIPTOR(Device), status stage; returns the usb_otg state."""
    x4emu(name, 'qmp', json.dumps({'execute': 'qom-set',
                                   'arguments': {'path': OTG, 'property': 'host', 'value': 'enumerate'}}))
    u = wait_for(lambda: (v := otg(name))['host']['step'] in ('done', 'error') and v, 60, 'the built-in host')
    assert u['host']['step'] == 'done', u['host']
    assert u['host']['resets'] == 1 and u['host']['setups'] == 1 and u['irq_raises'] > 0, u
    return u


def no_tinyusb_failure(name):
    log = x4emu(name, 'log').stdout
    for bad in ('ASSERT FAILED', 'Init TinyUSB stack failed', 'Guru Meditation', 'abort()'):
        assert bad not in log, f'the console shows {bad!r} in USB mode:\n{log[-2000:]}'
    return log


def test_licorice_usb_mode_waits_for_a_host(request, tmp_path, stock_card, licorice):
    """≡ → USB mode: Licorice restarts into the mode and shows "On" with no error; the core waits for a
    host; the built-in host reads the device descriptor without changing the screen; Exit restarts to
    the Bookshelf with the PHY back on the USB Serial/JTAG."""
    with booted(request, tmp_path, stock_card, dump=licorice.dump, icount=SKIP_IDLE) as s:
        n = s.name
        x4emu(n, 'wait-refresh', '--total', 1, '--timeout', 90)
        settle(n, 2, 60)
        tap(n, *LICORICE_MENU)
        settle(n, 1.5, 60)
        tap(n, *LICORICE_USB_ROW)
        wait_for(lambda: in_usb_mode(n), 90, 'Licorice to start TinyUSB')
        settle(n, 2, 60)
        shot = tmp_path / 'licorice-usb-mode.png'
        x4emu(n, 'screenshot', shot)
        golden_check(shot, licorice.golden('licorice-usb-mode.png'), why=' (USB mode "On": no "Could not start USB disk mode")')
        no_tinyusb_failure(n)
        check_waiting_for_a_host(otg(n))

        u = enumerate_once(n)
        assert u['host']['descriptor'] == DEVICE_DESCRIPTOR, u['host']
        settle(n, 1, 60)
        x4emu(n, 'screenshot', shot)
        golden_check(shot, licorice.golden('licorice-usb-mode.png'), why=' (after the built-in host enumerated)')

        refreshes = state(n)['refresh_count']
        tap(n, *LICORICE_EXIT)
        wait_for(lambda: (st := state(n))['usb_otg']['phy'] == 'usj' and st['refresh_count'] > refreshes + 1,
                 90, 'Licorice to leave USB mode and paint')
        settle(n, 2, 60)
        home = tmp_path / 'licorice-home.png'
        x4emu(n, 'screenshot', home)
        golden_check(home, licorice.golden('licorice-home.png'), why=' (Exit restarts to the Bookshelf)')
        assert otg(n)['dp_pullup'] is False
        no_tinyusb_failure(n)


def test_stock_768_usb_mode_waits_for_a_host(request, tmp_path, stock_card):
    """Nav menu → USB Mode on stock 7.6.8: it restarts, esp_tinyusb installs ("TinyUSB Driver installed
    on port 0", where the console used to say "Init TinyUSB stack failed"), the screen says "On", and the
    descriptor the built-in host reads is the one the firmware's own console table describes."""
    with booted(request, tmp_path, stock_card, dump=DUMP_768, icount=SKIP_IDLE) as s:
        n = s.name
        boot_to_home(n)
        tap(n, *HAMBURGER)
        settle(n, 1.5, 60)
        tap(n, *USB_ROW_768)
        x4emu(n, 'wait-text', 'TinyUSB Driver installed on port 0', '--timeout', 90)
        wait_for(lambda: in_usb_mode(n), 60, '7.6.8 to connect the core')
        settle(n, 2, 60)
        shot = tmp_path / 'stock768-usb-mode.png'
        x4emu(n, 'screenshot', shot)
        golden_check(shot, 'stock768-usb-mode.png', why=' (USB "On", not "Error")')
        log = no_tinyusb_failure(n)
        check_waiting_for_a_host(otg(n))

        u = enumerate_once(n)
        desc = bytes.fromhex(u['host']['descriptor'])
        table = dict(re.findall(r'│(\w+)\s*│\s*(0x[0-9a-f]+|\d+)\s*│', log))
        field = lambda k: int(table[k], 0)                                  # noqa: E731
        assert len(desc) == 18 and desc[:2] == b'\x12\x01', desc.hex(' ')
        assert list(desc[4:8]) == [field('bDeviceClass'), field('bDeviceSubClass'), field('bDeviceProtocol'),
                                   field('bMaxPacketSize0')], (desc.hex(' '), table)
        assert [int.from_bytes(desc[i:i + 2], 'little') for i in (8, 10, 12)] == \
            [field('idVendor'), field('idProduct'), field('bcdDevice')], (desc.hex(' '), table)
        assert list(desc[14:18]) == [field('iManufacturer'), field('iProduct'), field('iSerialNumber'),
                                     field('bNumConfigurations')], (desc.hex(' '), table)
        assert u['host']['descriptor'] == DEVICE_DESCRIPTOR, u['host']
        # the console kept flowing after the PHY went to OTG
        assert log.index('TinyUSB Driver installed') < log.rindex('main_task: Returned from app_main()'), log[-1500:]
