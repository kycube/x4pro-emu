#!/usr/bin/env python3
"""Write a minimal StarDict dictionary ZIP as wikdict.org ships them (test fixture): a flat ZIP of
NAME.ifo, NAME.idx and NAME.dict.dz (dictzip: a gzip member whose FEXTRA 'RA' subfield lists the
compressed chunk sizes). XTOS Licorice 26102901 imports such a ZIP from /dictionaries/ on the card
(docs/stock-licorice.md). Deterministic: fixed timestamps, so the card is the same on every run."""
import gzip, struct, sys, zipfile, zlib

WORDS = {'brown': 'a dark colour, between red and black (emulator test)',
         'chapter': 'a main division of a book (emulator test)',
         'dog': 'a domesticated canine (emulator test)',
         'fox': 'a small wild canine with a bushy tail (emulator test)',
         'lazy': 'unwilling to work (emulator test)',
         'paragraph': 'a distinct section of writing (emulator test)',
         'quartz': 'a hard crystalline mineral (emulator test)',
         'quick': 'fast, moving with speed (emulator test)'}


def dictzip(data):
    """`data` as a one-chunk dictzip (gzip with the 'RA' random-access table)."""
    co = zlib.compressobj(9, zlib.DEFLATED, -15)
    comp = co.compress(data) + co.flush(zlib.Z_FULL_FLUSH) + co.flush()
    ra = struct.pack('<HHHH', 1, 58315, 1, len(comp))              # version, chunk length, count, sizes
    extra = b'RA' + struct.pack('<H', len(ra)) + ra
    head = b'\x1f\x8b\x08\x04' + struct.pack('<I', 0) + b'\x02\x03' + struct.pack('<H', len(extra)) + extra
    out = head + comp + struct.pack('<II', zlib.crc32(data) & 0xffffffff, len(data))
    assert gzip.decompress(out) == data
    return out


def make_stardict(path, name='emutest', words=WORDS, bookname='Emulator Test (en-en)'):
    order = sorted(words, key=lambda w: (w.lower(), w))           # StarDict's index order
    data, idx = b'', b''
    for w in order:
        d = words[w].encode()
        idx += w.encode() + b'\0' + struct.pack('>II', len(data), len(d))
        data += d
    ifo = (f"StarDict's dict ifo file\nversion=2.4.2\nwordcount={len(order)}\nidxfilesize={len(idx)}\n"
           f"bookname={bookname}\nsametypesequence=m\n").encode()
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as z:
        for fn, blob in ((f'{name}.ifo', ifo), (f'{name}.idx', idx), (f'{name}.dict.dz', dictzip(data))):
            z.writestr(zipfile.ZipInfo(fn, date_time=(2026, 10, 29, 0, 0, 0)), blob, zipfile.ZIP_DEFLATED)


if __name__ == '__main__':
    make_stardict(sys.argv[1] if len(sys.argv) > 1 else 'emutest.zip')
