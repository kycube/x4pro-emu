#!/usr/bin/env python3
"""Assemble a 16 MB flash image for the emulator.

Three input forms:
  mkflash.py out.bin --build firmware/.pio/build/x4pro [--boot-app0 PATH]
      bootloader.bin @0x0, partitions.bin @0x8000, boot_app0.bin @0xE000,
      firmware.bin @0x10000 (ESP32-S3 bootloader offset is 0x0).
  mkflash.py out.bin --raw images/device/flash-XXXX-a.bin
      a raw 16 MB dump is copied as-is (padded/checked to 16 MB).
  mkflash.py out.bin 0x0:bootloader.bin 0x8000:partitions.bin 0x10000:app.bin ...
      explicit offset:file pairs.

--base IMG overlays the parts onto an existing image instead of 0xFF fill
(e.g. put a CrossPoint build into a device dump keeping its NVS and SD state).
"""
import argparse, glob, os, sys

FLASH_SIZE = 0x1000000
S3_BOOTLOADER_OFF = 0x0
PARTTABLE_OFF = 0x8000
OTADATA_OFF = 0xE000
APP0_OFF = 0x10000

def find_boot_app0():
    home = os.path.expanduser('~')
    cands = glob.glob(os.path.join(home, '.platformio/packages/framework-arduinoespressif32*/tools/partitions/boot_app0.bin'))
    return sorted(cands)[-1] if cands else None

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('out')
    ap.add_argument('parts', nargs='*', help='offset:file pairs')
    ap.add_argument('--build', help='pioarduino build directory')
    ap.add_argument('--raw', help='raw 16 MB dump to copy as-is')
    ap.add_argument('--base', help='start from this image instead of 0xFF fill')
    ap.add_argument('--boot-app0', help='path to boot_app0.bin (default: search ~/.platformio)')
    ap.add_argument('--no-otadata', action='store_true', help='do not write boot_app0.bin at 0xE000')
    a = ap.parse_args()

    if a.raw:
        data = bytearray(open(a.raw, 'rb').read())
        if len(data) > FLASH_SIZE:
            sys.exit(f'{a.raw} is larger than 16 MB')
        data += b'\xff' * (FLASH_SIZE - len(data))
        open(a.out, 'wb').write(data)
        print(f'{a.out}: raw copy of {a.raw} ({len(data)} bytes)')
        return

    if a.base:
        img = bytearray(open(a.base, 'rb').read())
        img += b'\xff' * (FLASH_SIZE - len(img))
    else:
        img = bytearray(b'\xff' * FLASH_SIZE)

    parts = []
    if a.build:
        b = a.build
        parts.append((S3_BOOTLOADER_OFF, os.path.join(b, 'bootloader.bin')))
        parts.append((PARTTABLE_OFF, os.path.join(b, 'partitions.bin')))
        if not a.no_otadata:
            ba = a.boot_app0 or find_boot_app0()
            if not ba:
                sys.exit('boot_app0.bin not found; pass --boot-app0')
            parts.append((OTADATA_OFF, ba))
        parts.append((APP0_OFF, os.path.join(b, 'firmware.bin')))
    for p in a.parts:
        off, path = p.split(':', 1)
        parts.append((int(off, 0), path))
    if not parts:
        sys.exit('nothing to assemble')

    for off, path in parts:
        blob = open(path, 'rb').read()
        if off + len(blob) > FLASH_SIZE:
            sys.exit(f'{path} at {off:#x} overflows the 16 MB image')
        img[off:off + len(blob)] = blob
        print(f'  {off:#09x} {len(blob):9d}  {path}')
    open(a.out, 'wb').write(img)
    print(f'{a.out}: {len(img)} bytes')

if __name__ == '__main__':
    main()
