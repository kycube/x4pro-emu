#!/usr/bin/env python3
"""Read and edit ESP-IDF NVS (non-encrypted) inside a flash image.

  nvsedit.py IMAGE list                          list every entry (values of *ssid/*pwd/*creds keys hidden)
  nvsedit.py IMAGE set-u8 NAMESPACE KEY VALUE    write KEY=VALUE (u8) into the active page, retire the old entry
  nvsedit.py IMAGE set-str NAMESPACE KEY VALUE   the same for a string (header + data entries, both CRCs)
  nvsedit.py IMAGE erase NAMESPACE KEY           mark every copy of KEY erased, on every page (span included)
  nvsedit.py IMAGE redact                        erase user_config/{sta_ssid,sta_pwd,wifi_creds}, net_en = 0

Every writing command edits the image in place unless `-o/--out FILE` is given, and is idempotent.

Partition: --offset 0x9000 --size 0x5000 (the stock Xteink layout, docs/device/partitions.md).
Format: 4 KB pages (header 32 B: state, seq, version, unused, crc32 over bytes 4..27), a 2-bit
entry-state bitmap at 32..63 (11 empty, 10 written, 00 erased), 126 x 32 B entries at 64..:
ns, type, span, chunk, crc32 (bytes 0..3 + 8..31), key[16], data[8]. CRCs are crc32_le(0xffffffff).
Namespaces are entries with ns 0 whose data byte is the index. Hash lists are rebuilt by NVS at load.
Variable-length items (str 0x21, blob 0x41/0x42) put <u16 size><u16 0xffff><u32 crc32(data)> in the
header's data field and the `size` bytes themselves in the following span-1 entries, 0xff-padded to
the entry boundary; a str's `size` counts its terminating NUL, and its chunk index is 0xff. Every
entry of a span carries the same state, so writing sets 'written' and erasing sets 'erased' on all of
them (ESP-IDF's eraseEntryAndSpan). New entries go to the first empty entry of the active page and
never leave a written entry behind an empty one, which is the invariant NVS checks when it loads.

Recipe — a stock flash image without the owner's WiFi credentials (the dump itself stays in images/,
CLAUDE.md rule 6; the redacted copy may leave it):

  .venv/bin/python tools/mkflash.py images/stock-clean.bin --raw images/device/flash-2026-09-06-a.bin
  .venv/bin/python tools/nvsedit.py images/stock-clean.bin redact      # the three keys + net_en = 0
  .venv/bin/python tools/nvsedit.py images/stock-clean.bin list | grep -E 'ssid|pwd|creds|net_en'
        -> only "user_config/net_en (u8) = 0": the credential entries are gone
  .venv/bin/python tools/nvsedit.py images/stock-clean.bin set-str user_config sta_ssid emulator
        -> optional: a placeholder SSID, e.g. for a test that wants the key present

`erase` (and the retirement half of `set-u8`/`set-str`) blanks the entry to 0xff on top of marking it
erased, so the value is really gone from the image, not just unreachable. `redact` leaves a bootable
image: the stock reaches Home exactly as with net_en = 0 (which also skips its 18 s WiFi start), and
`tests/test_nvsedit.py` diffs that boot against tests/golden/stock-home.png. Still in the image, and
still the owner's: the app, the phy calibration blobs, `otaPromptDay`, the serial in eFuse BLOCK3 and
the MAC (docs/device/partitions.md).
"""
import argparse, struct, sys

PAGE = 4096
STATE_ACTIVE, STATE_FULL, STATE_UNINIT = 0xfffffffe, 0xfffffffc, 0xffffffff
TYPES = {0x01: 'u8', 0x02: 'u16', 0x04: 'u32', 0x08: 'u64', 0x11: 'i8', 0x12: 'i16', 0x14: 'i32', 0x18: 'i64',
         0x21: 'str', 0x41: 'blob', 0x42: 'blob_data', 0x48: 'blob_idx'}
HIDE = ('ssid', 'pwd', 'creds', 'psk', 'pass')
ENTRIES = 126                       # data entries per page (4096 - 64 header/bitmap bytes) / 32
REDACT = ('sta_ssid', 'sta_pwd', 'wifi_creds')      # user_config keys holding the owner's WiFi credentials

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
        while i < ENTRIES:
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

    # --- writing -------------------------------------------------------------------------------
    def ns_index(self, nsname):
        nsi = next((k for k, v in self.ns.items() if v == nsname), None)
        if nsi is None:
            sys.exit(f'namespace {nsname!r} not found; have {sorted(self.ns.values())}')
        return nsi

    def retire(self, nsi, kb):
        """Mark every copy of NS/KEY erased on every page, span entries (a str's or blob's data, a
        blob's chunks) included, as ESP-IDF's eraseEntryAndSpan does, and blank their 32-byte slots
        to 0xff so the value is gone from the image as well (NVS reads only the state bitmap for an
        erased entry, and an erased entry never becomes free again). Returns the number of copies."""
        old = 0
        for pg in self.pages:
            for i, e in list(self.entries(pg)):
                if e[0] == nsi and e[8:24].split(b'\0')[0] == kb:
                    for j in range(i, min(i + max(e[2], 1), ENTRIES)):
                        set_entry_state(pg, j, 0)
                        pg[64 + 32 * j:64 + 32 * (j + 1)] = b'\xff' * 32
                    old += 1
        return old

    def alloc(self, span):
        """(page, first entry) for `span` consecutive empty entries: the active page (highest seq),
        else the newest FULL page that still has room. Entries are appended at the page's first empty
        entry, so nothing written ever ends up behind an empty entry."""
        cands = [pg for pg in self.pages if self.state(pg) == STATE_ACTIVE] or \
                [pg for pg in self.pages if self.state(pg) == STATE_FULL]
        if not cands:
            sys.exit('no writable page')
        pg = max(cands, key=self.seq)
        i = 0
        while i < ENTRIES and entry_state(pg, i) != 3:
            i += 1
        if i + span > ENTRIES or any(entry_state(pg, j) != 3 for j in range(i, i + span)):
            sys.exit('active page has no free entry' if span == 1 else
                     f'active page has no room for {span} consecutive entries')
        return pg, i

    def write_entry(self, pg, i, nsi, kb, typ, data_field, body=b''):
        """One item at entry i: the 32-byte header (key, type, span, both CRCs) plus `body` in the
        span-1 data entries, 0xff-padded; every entry of the span goes to state 'written'."""
        if len(kb) > 15:
            sys.exit(f'key {kb.decode(errors="replace")!r} is longer than 15 bytes')
        span = 1 + (len(body) + 31) // 32 if body else 1
        e = bytearray(32); e[0] = nsi; e[1] = typ; e[2] = span; e[3] = 0xff
        e[8:8 + len(kb)] = kb; e[24:32] = data_field
        e[4:8] = struct.pack('<I', crc32_le(bytes(e[0:4] + e[8:32])))
        pg[64 + 32 * i:64 + 32 * (i + 1)] = e
        if body:
            pg[64 + 32 * (i + 1):64 + 32 * (i + span)] = body + b'\xff' * ((span - 1) * 32 - len(body))
        for j in range(i, i + span):
            set_entry_state(pg, j, 2)
        return span

    def set_u8(self, nsname, key, value):
        nsi, kb = self.ns_index(nsname), key.encode()
        old = self.retire(nsi, kb)
        pg, i = self.alloc(1)
        self.write_entry(pg, i, nsi, kb, 0x01, bytes([value & 0xff]) + b'\xff' * 7)
        print(f'{nsname}/{key} = {value}: wrote entry {i} in page seq {self.seq(pg)}, retired {old} old cop{"y" if old == 1 else "ies"}')

    def set_str(self, nsname, key, value):
        """Write NS/KEY as a str (type 0x21): header + the NUL-terminated bytes in span-1 entries."""
        nsi, kb = self.ns_index(nsname), key.encode()
        body = value.encode() + b'\0'
        span = 1 + (len(body) + 31) // 32
        if span > ENTRIES:
            sys.exit(f'string of {len(body)} bytes does not fit in a page')
        old = self.retire(nsi, kb)
        pg, i = self.alloc(span)
        self.write_entry(pg, i, nsi, kb, 0x21, struct.pack('<HHI', len(body), 0xffff, crc32_le(body)), body)
        hidden = any(h in key.lower() for h in HIDE)
        shown = f'<{len(body) - 1} chars>' if hidden else repr(value)
        print(f'{nsname}/{key} = {shown}: wrote entries {i}..{i + span - 1} in page seq {self.seq(pg)}, '
              f'retired {old} old cop{"y" if old == 1 else "ies"}')

    def erase(self, nsname, key, quiet=False):
        """Mark every copy of NS/KEY erased (all pages, span entries included). Returns the count."""
        old = self.retire(self.ns_index(nsname), key.encode())
        if not quiet:
            print(f'{nsname}/{key}: erased {old} cop{"y" if old == 1 else "ies"}'
                  + ('' if old else ' (key not present)'))
        return old

    def redact(self):
        """Make the image shareable: drop the WiFi credentials of user_config and turn WiFi off."""
        total = 0
        for key in REDACT:
            total += self.erase('user_config', key)
        self.set_u8('user_config', 'net_en', 0)
        print(f'redacted: {total} credential entr{"y" if total == 1 else "ies"} erased, user_config/net_en = 0')

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

    def writer(name, help_):
        p = sp.add_parser(name, help=help_)
        p.add_argument('-o', '--out', help='write to this file instead of in place')
        return p
    p = writer('set-u8', 'write KEY = VALUE (u8)'); p.add_argument('namespace'); p.add_argument('key')
    p.add_argument('value', type=lambda x: int(x, 0))
    p = writer('set-str', 'write KEY = VALUE (str)'); p.add_argument('namespace'); p.add_argument('key')
    p.add_argument('value')
    p = writer('erase', 'mark every copy of KEY erased'); p.add_argument('namespace'); p.add_argument('key')
    writer('redact', 'erase the WiFi credentials of user_config and set net_en = 0')
    a = ap.parse_args()
    nvs = Nvs(open(a.image, 'rb').read(), a.offset, a.size)
    if a.cmd == 'list':
        nvs.list()
        return
    if a.cmd == 'set-u8':
        nvs.set_u8(a.namespace, a.key, a.value)
    elif a.cmd == 'set-str':
        nvs.set_str(a.namespace, a.key, a.value)
    elif a.cmd == 'erase':
        nvs.erase(a.namespace, a.key)
    else:
        nvs.redact()
    nvs.save(a.out or a.image)

if __name__ == '__main__':
    main()
