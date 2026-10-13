#!/usr/bin/env python3
"""Put the IO probe on a card image, with every file it reads.

    .venv/bin/python apps/probe-io/mkcard.py CARD.img

1. `tools/xtapp.py install CARD apps/probe-io` -- the container, index.lua, the modules
   (mod.lua, lib/mod.lua, lib/init.lua), the small data files (story.txt, where.txt, data/*,
   bytes.bin, bytes-r.bin). `install` copies every file of the directory and its subdirectories.
2. The generated text files, written to a temporary directory and copied with mtools into
   /XTApps/probe-io/: big100k.txt (102,400 B), big400k.txt (409,600 B) and the size ladder
   lad064k/128k/256k/512k/1m/2m/4m.txt. They are not kept in the repository; the probe regenerates
   the expected content itself (line k = "%07d " + FILL + "\\n", 64 bytes), so it needs no table of
   checksums.
3. Two markers outside the app directory: /XTApps/where.txt ("xtapps-dir") and /where.txt
   ("card-root"); the app's own where.txt says "app-root", data/where.txt "app-data". Which one a
   path form reads tells what ctx.data resolves it against.

mtools addresses the FAT32 partition at 1 MiB (tools/mksd.py's layout):
`mcopy -i CARD@@1048576 FILE ::/XTApps/probe-io/`.
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', '..', 'tools'))
import xtapp  # noqa: E402

FILL = 'abcdefghijklmnopqrstuvwxyz ABCDEFGHIJKLMNOPQRSTUVWXYZ 0'   # 55 chars: lines are 64 bytes
assert len(FILL) == 55
LADDER = {'lad064k.txt': 64 << 10, 'lad128k.txt': 128 << 10, 'lad256k.txt': 256 << 10,
          'lad512k.txt': 512 << 10, 'lad1m.txt': 1 << 20, 'lad2m.txt': 2 << 20,
          'lad4m.txt': 4 << 20}
BIG = {'big100k.txt': 1600 * 64, 'big400k.txt': 6400 * 64}


def text_of(size):
    assert size % 64 == 0
    return ''.join('%07d %s\n' % (k, FILL) for k in range(1, size // 64 + 1)).encode('ascii')


def mcopy(img, src, dst):
    xtapp.mtools(img, 'mcopy', '-o', src, dst)


def main(argv):
    if len(argv) != 2:
        sys.exit(__doc__)
    img = argv[1]
    rep = xtapp.install(img, HERE)
    print(f'installed {rep["card_dir"]}: {", ".join(rep["files"])}')
    with tempfile.TemporaryDirectory() as tmp:
        for name, size in list(BIG.items()) + list(LADDER.items()):
            p = os.path.join(tmp, name)
            with open(p, 'wb') as f:
                f.write(text_of(size))
            mcopy(img, p, f'::/XTApps/{rep["app_id"]}/')
            print(f'  + /XTApps/{rep["app_id"]}/{name} ({size} bytes)')
        for dst, body in (('::/XTApps/where.txt', b'xtapps-dir'), ('::/where.txt', b'card-root')):
            p = os.path.join(tmp, 'where.txt')
            with open(p, 'wb') as f:
                f.write(body)
            mcopy(img, p, dst)
            print(f'  + {dst[2:]} ({body.decode()})')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
