#!/usr/bin/env python3
"""Read and edit ESP-IDF NVS (non-encrypted) inside a flash image.

  nvsedit.py IMAGE list                         list every entry (values of *ssid/*pwd/*creds keys hidden)
  nvsedit.py IMAGE set-u8 NAMESPACE KEY VALUE   write KEY=VALUE (u8) into the active page, retire the old entry

Partition: --offset 0x9000 --size 0x5000 (the stock Xteink layout, docs/device/partitions.md).
Format: 4 KB pages (header 32 B: state, seq, version, unused, crc32 over bytes 4..27), a 2-bit
entry-state bitmap at 32..63 (11 empty, 10 written, 00 erased), 126 x 32 B entries at 64..:
ns, type, span, chunk, crc32 (bytes 0..3 + 8..31), key[16], data[8]. CRCs are crc32_le(0xffffffff).
Namespaces are entries with ns 0 whose data byte is the index. Hash lists are rebuilt by NVS at load.
"""
import argparse, struct, sys

PAGE = 4096
STATE_ACTIVE, STATE_FULL, STATE_UNINIT = 0xfffffffe, 0xfffffffc, 0xffffffff
TYPES = {0x01: 'u8', 0x02: 'u16', 0x04: 'u32', 0x08: 'u64', 0x11: 'i8', 0x12: 'i16', 0x14: 'i32', 0x18: 'i64',
         0x21: 'str', 0x41: 'blob', 0x42: 'blob_data', 0x48: 'blob_idx'}
HIDE = ('ssid', 'pwd', 'creds', 'psk', 'pass')

def crc32_le(data, init=0xFFFFFFFF):
    c = init ^ 0xFFFFFFFF
    for b in data:
        c ^= b
        for _ in range(8):
            c = (c >> 1) ^ (0xEDB88320 if c & 1 else 0)
    return c ^ 0xFFFFFFFF

def entry_state(page, i):
    return (page[32 + i // 4] >> ((i % 4) * 2)) & 3

def set_entry_state(page, i, st):
    b = 32 + i // 4; sh = (i % 4) * 2
    page[b] = (page[b] & ~(3 << sh)) | (st << sh)

class Nvs:
    def __init__(self, img, offset, size):
        self.img, self.offset, self.size = img, offset, size
        self.pages = [bytearray(img[offset + p:offset + p + PAGE]) for p in range(0, size, PAGE)]
        self.ns = {}
        for pg in self.pages:
            for i, e in self.entries(pg):
                if e[0] == 0:
                    self.ns[e[24]] = e[8:24].split(b'\0')[0].decode(errors='replace')

    def state(self, pg): return struct.unpack('<I', pg[:4])[0]
    def seq(self, pg): return struct.unpack('<I', pg[4:8])[0]

    def entries(self, pg):
        if self.state(pg) == STATE_UNINIT:
            return
        i = 0
        while i < 126:
            st = entry_state(pg, i); e = pg[64 + 32 * i:64 + 32 * (i + 1)]
            if st == 2:
                yield i, e
                i += max(e[2], 1)
            else:
                i += 1

    def list(self):
        for n, pg in enumerate(self.pages):
            if self.state(pg) == STATE_UNINIT:
                continue
            print(f'page {n}: state 0x{self.state(pg):08x} seq {self.seq(pg)}')
            for i, e in self.entries(pg):
                key = e[8:24].split(b'\0')[0].decode(errors='replace')
                if e[0] == 0:
                    print(f'  [{i:3}] namespace {e[24]} = {key}'); continue
                t = TYPES.get(e[1], hex(e[1]))
                if t in ('u8', 'i8'): v = e[24]
                elif t in ('u16', 'i16'): v = struct.unpack('<H', e[24:26])[0]
                elif t in ('u32', 'i32'): v = struct.unpack('<I', e[24:28])[0]
                elif t == 'str':
                    n_ = struct.unpack('<H', e[24:26])[0]; raw = pg[64 + 32 * (i + 1):64 + 32 * (i + 1) + n_].split(b'\0')[0]
                    v = f'<{len(raw)} chars hidden>' if any(h in key.lower() for h in HIDE) else raw.decode(errors='replace')
                else: v = f'<{t} {struct.unpack("<H", e[24:26])[0]} bytes>'
                if any(h in key.lower() for h in HIDE) and t not in ('str',): v = '<hidden>'
                print(f'  [{i:3}] {self.ns.get(e[0], "ns%d" % e[0])}/{key} ({t}) = {v}')

    def set_u8(self, nsname, key, value):
        nsi = next((k for k, v in self.ns.items() if v == nsname), None)
        if nsi is None:
            sys.exit(f'namespace {nsname!r} not found; have {sorted(self.ns.values())}')
        kb = key.encode()
        # retire existing copies (any page)
        old = 0
        for pg in self.pages:
            for i, e in list(self.entries(pg)):
                if e[0] == nsi and e[8:24].split(b'\0')[0] == kb:
                    set_entry_state(pg, i, 0); old += 1
        # append to the active page (highest seq with state ACTIVE), else the first FULL page with room
        cands = [pg for pg in self.pages if self.state(pg) == STATE_ACTIVE] or \
                [pg for pg in self.pages if self.state(pg) == STATE_FULL]
        if not cands:
            sys.exit('no writable page')
        pg = max(cands, key=self.seq)
        i = 0
        while i < 126 and entry_state(pg, i) != 3:
            i += 1
        if i >= 126:
            sys.exit('active page has no free entry')
        e = bytearray(32); e[0] = nsi; e[1] = 0x01; e[2] = 1; e[3] = 0xff
        e[8:8 + len(kb)] = kb; e[24] = value & 0xff; e[25:32] = b'\xff' * 7
        e[4:8] = struct.pack('<I', crc32_le(bytes(e[0:4] + e[8:32])))
        pg[64 + 32 * i:64 + 32 * (i + 1)] = e
        set_entry_state(pg, i, 2)
        print(f'{nsname}/{key} = {value}: wrote entry {i} in page seq {self.seq(pg)}, retired {old} old cop{"y" if old == 1 else "ies"}')

    def save(self, path):
        out = bytearray(self.img)
        for n, pg in enumerate(self.pages):
            out[self.offset + n * PAGE:self.offset + (n + 1) * PAGE] = pg
        open(path, 'wb').write(out)

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('image'); ap.add_argument('--offset', type=lambda x: int(x, 0), default=0x9000)
    ap.add_argument('--size', type=lambda x: int(x, 0), default=0x5000)
    sp = ap.add_subparsers(dest='cmd', required=True)
    sp.add_parser('list')
    p = sp.add_parser('set-u8'); p.add_argument('namespace'); p.add_argument('key'); p.add_argument('value', type=lambda x: int(x, 0))
    p.add_argument('-o', '--out', help='write to this file instead of in place')
    a = ap.parse_args()
    nvs = Nvs(open(a.image, 'rb').read(), a.offset, a.size)
    if a.cmd == 'list':
        nvs.list()
    else:
        nvs.set_u8(a.namespace, a.key, a.value)
        nvs.save(a.out or a.image)

if __name__ == '__main__':
    main()
