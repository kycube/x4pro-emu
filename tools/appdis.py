#!/usr/bin/env python3
"""Disassemble a window of an ESP32-S3 app image around a virtual address.

  tools/appdis.py images/device/stock-app0-7.2.4.bin 0x4230bfb8 [BEFORE] [AFTER]

VADDR is where a CPU sits (QMP `info registers -a`, PC=...); BEFORE/AFTER are the window in bytes
(hex or decimal, default 0x180 / 0x100). The ESP image header is parsed (magic 0xE9, segment count,
then per segment: load address u32, length u32, data), the segment holding VADDR is written to a
temporary file and objdump'd as raw Xtensa at the right VMA; the line at VADDR is marked "<== PC".
ROM addresses (0x4000xxxx) are not in the app: use images/rom/esp32s3_rev0_rom.nm / .elf instead.
Objdump comes from the pioarduino toolchain (~/.platformio/packages/toolchain-xtensa-esp-elf).
"""
import glob, os, struct, subprocess, sys, tempfile


def parse_int(s):
    return int(s, 0)


def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    img, vaddr = sys.argv[1], parse_int(sys.argv[2])
    before = parse_int(sys.argv[3]) if len(sys.argv) > 3 else 0x180
    after = parse_int(sys.argv[4]) if len(sys.argv) > 4 else 0x100
    d = open(img, 'rb').read()
    if d[0] != 0xE9:
        sys.exit(f'{img}: not an ESP image (magic 0x{d[0]:02x})')
    nseg, off, segs = d[1], 24, []
    for _ in range(nseg):
        la, ln = struct.unpack_from('<II', d, off)
        off += 8
        segs.append((la, ln, off))
        off += ln
    ods = glob.glob(os.path.expanduser('~/.platformio/packages/toolchain-xtensa-esp-elf/bin/xtensa-esp-elf-objdump'))
    if not ods:
        sys.exit('xtensa-esp-elf-objdump not found under ~/.platformio/packages/toolchain-xtensa-esp-elf')
    for la, ln, fo in segs:
        if la <= vaddr < la + ln:
            print(f'segment load 0x{la:08x} len 0x{ln:x} file offset 0x{fo:x}')
            t = tempfile.NamedTemporaryFile(suffix='.bin', delete=False)
            t.write(d[fo:fo + ln])
            t.close()
            lo, hi = max(la, vaddr - before), min(la + ln, vaddr + after)
            out = subprocess.run([ods[0], '-D', '-b', 'binary', '-m', 'xtensa', f'--adjust-vma=0x{la:x}',
                                  f'--start-address=0x{lo:x}', f'--stop-address=0x{hi:x}', t.name],
                                 capture_output=True, text=True)
            os.unlink(t.name)
            for line in out.stdout.splitlines()[7:]:
                print(line + (' <== PC' if line.startswith(f'{vaddr:8x}:') else ''))
            return
    print('vaddr not in any segment; segments:')
    for la, ln, fo in segs:
        print(f'  0x{la:08x} +0x{ln:x} (file 0x{fo:x})')
    sys.exit(1)


if __name__ == '__main__':
    main()
