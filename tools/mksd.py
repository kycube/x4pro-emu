#!/usr/bin/env python3
"""Create a FAT32 SD-card image of a power-of-two size and populate it with mtools.

No root, no loop mounts. QEMU's SD model refuses images whose size is not a
power of two, so the size is validated here.

  mksd.py out.img [--size 256M] [--src DIR ...] [--label X4PRO]
"""
import argparse, os, shutil, subprocess, sys

def parse_size(s):
    s = s.strip().upper()
    mult = {'K': 1 << 10, 'M': 1 << 20, 'G': 1 << 30}
    if s[-1] in mult:
        return int(s[:-1]) * mult[s[-1]]
    return int(s)

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('out')
    ap.add_argument('--size', default='256M', help='image size, power of two (default 256M)')
    ap.add_argument('--src', action='append', default=[], help='directory whose contents go to the root of the image')
    ap.add_argument('--label', default='X4PRO')
    ap.add_argument('--force', action='store_true')
    a = ap.parse_args()

    size = parse_size(a.size)
    if size & (size - 1):
        sys.exit(f'size {size} is not a power of two (QEMU hw/sd/sd.c requires it)')
    if os.path.exists(a.out) and not a.force:
        sys.exit(f'{a.out} exists; pass --force to overwrite')
    for tool in ('mkfs.vfat', 'mcopy'):
        if not shutil.which(tool):
            sys.exit(f'{tool} not found (brew install dosfstools mtools / apt install dosfstools mtools)')

    with open(a.out, 'wb') as f:
        f.truncate(size)
    subprocess.run(['mkfs.vfat', '-F', '32', '-n', a.label, a.out], check=True, stdout=subprocess.DEVNULL)
    for src in a.src:
        entries = sorted(os.listdir(src))
        if not entries:
            continue
        # -s recursive, -m preserve mtime, -Q quit on error; :: is the image root
        cmd = ['mcopy', '-i', a.out, '-s', '-m', '-Q'] + [os.path.join(src, e) for e in entries] + ['::/']
        subprocess.run(cmd, check=True)
    print(f'{a.out}: {size >> 20} MiB FAT32, label {a.label}, populated from {a.src or "nothing"}')

if __name__ == '__main__':
    main()
