#!/usr/bin/env python3
"""Create the 1 KB eFuse file QEMU's nvram.esp32s3.efuse device loads.

File layout (hw/nvram/esp_efuse.c reads `struct blocks` from offset 0):
  BLOCK0 6 words, BLOCK1 6 words, BLOCK2..BLOCK10 8 words each,
  little-endian uint32, zero padded to 1024 bytes.

  mkefuse.py out.bin                       all zero
  mkefuse.py out.bin --mac 98:c3:77:be:ea:30
  mkefuse.py out.bin --dump docs/device/efuse-dump.txt   replay `espefuse dump`
  mkefuse.py out.bin --dump ... --no-keys  replay but zero BLOCK4..BLOCK10 (keys)
"""
import argparse, re, struct, sys

BLOCK_WORDS = [6, 6, 8, 8, 8, 8, 8, 8, 8, 8, 8]
FILE_SIZE = 1024

def parse_dump(path):
    blocks = {}
    for line in open(path):
        m = re.match(r'^\S+\s+\(.*?\)\s+\[\s*(\d+)\s*\]\s+dump:\s+([0-9a-fA-F ]+)$', line.strip())
        if m:
            idx = int(m.group(1))
            words = [int(w, 16) for w in m.group(2).split()]
            blocks[idx] = words
    if len(blocks) != 11:
        sys.exit(f'{path}: expected 11 blocks, found {sorted(blocks)}')
    return blocks

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('out')
    ap.add_argument('--dump', help='espefuse dump output to replay')
    ap.add_argument('--mac', help='base MAC aa:bb:cc:dd:ee:ff (overrides the dump)')
    ap.add_argument('--no-keys', action='store_true', help='zero BLOCK4..BLOCK10')
    a = ap.parse_args()

    blocks = {i: [0] * n for i, n in enumerate(BLOCK_WORDS)}
    if a.dump:
        for i, words in parse_dump(a.dump).items():
            if len(words) != BLOCK_WORDS[i]:
                sys.exit(f'BLOCK{i}: expected {BLOCK_WORDS[i]} words, got {len(words)}')
            blocks[i] = list(words)
    if a.no_keys:
        for i in range(4, 11):
            blocks[i] = [0] * BLOCK_WORDS[i]
    if a.mac:
        b = [int(x, 16) for x in a.mac.split(':')]
        if len(b) != 6:
            sys.exit('bad MAC')
        # BLOCK1 word0 = mac[5]<<0 | mac[4]<<8 | mac[3]<<16 | mac[2]<<24 ; word1 low16 = mac[1] | mac[0]<<8
        blocks[1][0] = b[5] | (b[4] << 8) | (b[3] << 16) | (b[2] << 24)
        blocks[1][1] = (blocks[1][1] & 0xFFFF0000) | b[1] | (b[0] << 8)

    data = b''.join(struct.pack('<I', w) for i in range(11) for w in blocks[i])
    data += b'\0' * (FILE_SIZE - len(data))
    open(a.out, 'wb').write(data)
    mac_w0, mac_w1 = blocks[1][0], blocks[1][1]
    mac = [(mac_w1 >> 8) & 0xFF, mac_w1 & 0xFF, (mac_w0 >> 24) & 0xFF, (mac_w0 >> 16) & 0xFF, (mac_w0 >> 8) & 0xFF, mac_w0 & 0xFF]
    print(f'{a.out}: {len(data)} bytes, MAC {":".join(f"{x:02x}" for x in mac)}')

if __name__ == '__main__':
    main()
