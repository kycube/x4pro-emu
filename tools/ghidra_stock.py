#!/usr/bin/env python3
"""Ghidra over a stock `xteink_app` image: analyse once, then answer questions in milliseconds.

`tools/appdis.py` gives disassembly windows but no function boundaries and no cross-references,
so the questions the firmware survey kept hitting -- "who calls this presenter", "which function
draws this row", "what points at this string" -- were unanswerable without a real analyser. This
wraps Ghidra's `analyzeHeadless` to answer them.

    Which image           `--image PATH` and/or `--version TAG` (global options, accepted before
                          or after the subcommand) pick the app image. The default is unchanged:
                          7.2.4 = images/device/stock-app0-7.2.4.bin. `--version 7.5.4` alone finds
                          images/device/stock-app*-7.5.4.bin; `--image` alone reads the version out
                          of the image's esp_app_desc (offset 0x20, version at +0x10); both together
                          must agree. Everything else follows the choice: the Ghidra project is
                          images/ghidra/stock-<version>.gpr, the exports live in
                          images/ghidra/stock-<version>/, and the staleness check compares them
                          with *that* image. Nothing in the layout is version-specific: the
                          segment table is read from the image header, so 7.5.4 (DROM at
                          0x3c340020, a 244 KB smaller IROM) imports the same way.
    Layout                the image is imported as a *raw* binary with the Xtensa LE language;
                          `tools/ghidra/StockLayout.java` then throws away the flat block and
                          rebuilds memory from the ESP image header -- one block per segment at its
                          load VA (7.2.4: DROM 0x3c380020, IROM 0x42000020 executable, IRAM
                          0x40378000 executable, the DRAM/RTC blocks) -- adds uninitialized
                          windows for the ESP32-S3 mask ROM and labels them from
                          images/rom/esp32s3_rev0_rom.nm, so calls to 0x4000xxxx read as names.
    Analysis              Ghidra's default analysers -- about **1 minute** on an M-series Mac for
                          the 3.5 MB of Xtensa code (24.8 k functions), because the shipped Xtensa
                          function-start patterns match the windowed-ABI `retw.n` / `entry`
                          prologue. Ghidra 12.1.3 ships the Xtensa processor (language version 4.1,
                          prebuilt .sla, so nothing has to be compiled) and its decompiler produces
                          usable C for this code. The "Invalid PNG/GIF data" errors in the log are
                          the embedded-media analyser hitting false magic in DROM; ignore them.
    Exports               images/ghidra/stock-<version>/{functions,xrefs,strings,calls}.txt,
                          written by `tools/ghidra/StockExport.java`. Every query subcommand below
                          reads only these text files -- no JVM, no Ghidra, no project lock.

Quick start (a future agent needs no more than this):

    tools/ghidra_stock.py analyze                 # once, ~1 min; idempotent, skips if up to date
    tools/ghidra_stock.py func 0x4210db20         # name, size, callers, callees   (0.1 s)
    tools/ghidra_stock.py callers 0x42128a84      # who constructs this / who calls it
    tools/ghidra_stock.py xrefs 0x3c497334        # what points at this string or datum
    tools/ghidra_stock.py decompile 0x4233abe0    # C for one function (~3 s, starts a JVM)
    tools/ghidra_stock.py --version 7.5.4 analyze          # the 7.5.4 image, own project + exports
    tools/ghidra_stock.py --version 7.5.4 xrefs 0x3c3...   # every query takes the same options
    tools/ghidra_stock.py --image SCRATCH/app.bin func ...  # any xteink_app image (version read
                                                           # from its esp_app_desc)

`analyze` re-runs only when the image or a tools/ghidra/*.java script is newer than the exports
(or with --force). `decompile` is the only query that needs Ghidra; it opens the finished project
read-only with -noanalysis, so it costs a JVM start, not a re-analysis. Addresses are always
those of the selected image: nothing found in one version means anything in another
(docs/stock-7.5.4.md is the 7.2.4 -> 7.5.4 re-location table).

Three things to expect from this particular binary. Nothing has C++ symbols, so functions are all
`FUN_<addr>` (only the 254 ROM labels have names); identify them the way the survey did (typeinfo
strings, vtables) and cross-check with `callers`. A string with **no** xrefs is usually not a miss:
the i18n / toast pack at 0x3c490000 is addressed by index through the table at 0x3c4916a4, so
nothing in the image points at its members -- checked against the whole image, not one word holds a
pointer into it -- and `xrefs` says so, then names the pack base instead. And the shipped Xtensa
SLEIGH does not decode every opcode: a decompilation can end in `halt_baddata()` ("Bad instruction
- Truncating control flow"). That is localised; `appdis.py` at the same address is the fallback.

For a human: the project is a normal Ghidra project at images/ghidra/stock-<version>.gpr (the
default: stock-7.2.4.gpr). Open it with `$(brew --prefix ghidra)/libexec/ghidraRun`, then File >
Open Project > that .gpr, and double-click the program (the image's file name, e.g.
`stock-app0-7.2.4.bin` or `stock-app1-7.5.4.bin`). Close the GUI before running `analyze` or
`decompile` -- Ghidra locks the project. Everything under images/ is gitignored.

Install (done once, Homebrew only):
    brew install ghidra          # formula, pulls openjdk@21; the `ghidra` *cask* does not exist
                                 # and the temurin cask needs an interactive sudo.
"""

import argparse
import bisect
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEVICE_DIR = os.path.join(REPO, 'images', 'device')
DEFAULT_VERSION = '7.2.4'
DEFAULT_IMAGE = os.path.join(DEVICE_DIR, 'stock-app0-7.2.4.bin')
ROM_NM = os.path.join(REPO, 'images', 'rom', 'esp32s3_rev0_rom.nm')
SCRIPT_DIR = os.path.join(REPO, 'tools', 'ghidra')
PROJ_DIR = os.path.join(REPO, 'images', 'ghidra')
LANGUAGE = 'Xtensa:LE:32:default'
DESC_OFF = 0x20                 # esp_app_desc: 24-byte image header + 8-byte segment header
DESC_MAGIC = 0xABCD5432

# The image under analysis. `select()` rebinds these from --image / --version; the defaults are
# the 7.2.4 app0 image, so the historical invocation behaves exactly as before.
IMAGE = DEFAULT_IMAGE
VERSION = DEFAULT_VERSION
PROJ_NAME = 'stock-' + VERSION
OUT_DIR = os.path.join(PROJ_DIR, PROJ_NAME)
PROGRAM = os.path.basename(IMAGE)

EXPORTS = ('functions.txt', 'xrefs.txt', 'strings.txt', 'calls.txt')

_FUNCS = None                   # functions.txt cache, dropped by select()


class Error(Exception):
    pass


# --------------------------------------------------------------------------- which image

def app_desc(path):
    """{'project', 'version', 'elf_sha256'} from the image's esp_app_desc, or None when the file
    is not an ESP-IDF app image (no 0xE9 magic, no 0xABCD5432 descriptor)."""
    try:
        with open(path, 'rb') as fh:
            head = fh.read(DESC_OFF + 0x100)
    except OSError:
        return None
    if len(head) < DESC_OFF + 0xb0 or head[0] != 0xE9:
        return None
    if int.from_bytes(head[DESC_OFF:DESC_OFF + 4], 'little') != DESC_MAGIC:
        return None
    field = lambda off, n: head[DESC_OFF + off:DESC_OFF + off + n].split(b'\0', 1)[0].decode(
        'ascii', 'replace')
    return {'version': field(0x10, 32), 'project': field(0x30, 32),
            'elf_sha256': head[DESC_OFF + 0x90:DESC_OFF + 0xb0].hex()}


def find_image(version):
    """The images/device/stock-app*-<version>.bin for a version tag (app0 before app1)."""
    hits = sorted(f for f in os.listdir(DEVICE_DIR)
                  if re.fullmatch(r'stock-app\d+-' + re.escape(version) + r'\.bin', f)) \
        if os.path.isdir(DEVICE_DIR) else []
    if not hits:
        raise Error(f'no images/device/stock-app*-{version}.bin; pass --image PATH')
    return os.path.join(DEVICE_DIR, hits[0])


def select(image=None, version=None):
    """Bind the module to one image: IMAGE, VERSION, PROJ_NAME (stock-<version>), OUT_DIR
    (images/ghidra/stock-<version>/) and PROGRAM. Neither argument = the 7.2.4 default. With only
    --version the image is images/device/stock-app*-<version>.bin; with only --image the version
    comes from the image's esp_app_desc; with both they must agree (a tag that does not match the
    descriptor would file the exports under the wrong name)."""
    global IMAGE, VERSION, PROJ_NAME, OUT_DIR, PROGRAM, _FUNCS
    if image is None and version is None:
        image, version = DEFAULT_IMAGE, DEFAULT_VERSION
    elif image is None:
        image = find_image(version)
    image = os.path.abspath(image)
    desc = app_desc(image)
    if version is None:
        if desc is None:
            raise Error(f'{image}: not an ESP-IDF app image (no esp_app_desc); pass --version TAG')
        version = desc['version']
    elif desc is not None and desc['version'] != version:
        raise Error(f'{image} says it is {desc["project"]} {desc["version"]}, not {version}; '
                    f'drop --version or pass the matching image')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*', version):
        raise Error(f'not a usable version tag: {version!r}')
    IMAGE, VERSION = image, version
    PROJ_NAME = 'stock-' + version
    OUT_DIR = os.path.join(PROJ_DIR, PROJ_NAME)
    PROGRAM = os.path.basename(image)
    _FUNCS = None
    return {'image': IMAGE, 'version': VERSION, 'project': PROJ_NAME, 'exports': OUT_DIR,
            'desc': desc}


# --------------------------------------------------------------------------- Ghidra invocation

def ghidra_dir():
    """The Ghidra installation root (the directory holding support/analyzeHeadless)."""
    env = os.environ.get('GHIDRA_INSTALL_DIR')
    candidates = [env] if env else []
    brew = shutil.which('brew')
    if brew:
        try:
            prefix = subprocess.run([brew, '--prefix', 'ghidra'], capture_output=True,
                                    text=True, timeout=30).stdout.strip()
            if prefix:
                candidates.append(os.path.join(prefix, 'libexec'))
        except (OSError, subprocess.SubprocessError):
            pass
    candidates.append('/opt/homebrew/opt/ghidra/libexec')
    for c in candidates:
        if c and os.path.isfile(os.path.join(c, 'support', 'analyzeHeadless')):
            return c
    raise Error('Ghidra not found. `brew install ghidra`, or set GHIDRA_INSTALL_DIR.')


def java_home():
    for c in (os.environ.get('JAVA_HOME'), '/opt/homebrew/opt/openjdk@21'):
        if c and os.path.isfile(os.path.join(c, 'bin', 'java')):
            return c
    return None


def run_headless(args, echo=True, capture=False):
    """Run analyzeHeadless with `args`. With `capture`, swallow its very chatty log and print it
    only if the run fails; otherwise stream it. Returns the exit status."""
    cmd = [os.path.join(ghidra_dir(), 'support', 'analyzeHeadless')] + args
    env = dict(os.environ)
    jh = java_home()
    if jh:
        env['JAVA_HOME'] = jh
    if echo:
        print('$ ' + ' '.join(cmd), file=sys.stderr, flush=True)
    if not capture:
        return subprocess.run(cmd, env=env).returncode
    p = subprocess.run(cmd, env=env, capture_output=True, text=True)
    if p.returncode != 0:
        sys.stderr.write(p.stdout + p.stderr)
    return p.returncode


# --------------------------------------------------------------------------- analyze

def up_to_date():
    """True when every export exists and is newer than the image and the Ghidra scripts."""
    outs = [os.path.join(OUT_DIR, n) for n in EXPORTS]
    if not all(os.path.isfile(p) for p in outs):
        return False
    newest_input = max([os.path.getmtime(IMAGE)] +
                       [os.path.getmtime(os.path.join(SCRIPT_DIR, f))
                        for f in os.listdir(SCRIPT_DIR) if f.endswith('.java')])
    return min(os.path.getmtime(p) for p in outs) > newest_input


def cmd_analyze(opts):
    if not os.path.isfile(IMAGE):
        raise Error(f'no app image at {IMAGE}')
    if up_to_date() and not opts.force:
        print(f'{OUT_DIR}: exports are newer than the image and the scripts; nothing to do '
              f'(--force to redo).')
        return 0
    os.makedirs(OUT_DIR, exist_ok=True)
    args = [PROJ_DIR, PROJ_NAME,
            '-import', IMAGE, '-overwrite',
            '-processor', LANGUAGE, '-cspec', 'default',
            '-loader', 'BinaryLoader', '-loader-baseAddr', '0x0',
            '-scriptPath', SCRIPT_DIR,
            '-preScript', 'StockLayout.java', IMAGE, ROM_NM,
            '-postScript', 'StockExport.java', OUT_DIR,
            '-log', os.path.join(OUT_DIR, 'analyze.log')]
    t0 = time.time()
    rc = run_headless(args)
    dt = time.time() - t0
    print(f'\nanalyzeHeadless finished in {dt / 60:.1f} min (exit {rc})', file=sys.stderr)
    if rc != 0:
        raise Error(f'analyzeHeadless failed; see {os.path.join(OUT_DIR, "analyze.log")}')
    for name in EXPORTS:
        p = os.path.join(OUT_DIR, name)
        n = sum(1 for _ in open(p, encoding='utf-8', errors='replace')) - 1
        print(f'  {name:<14} {n:>8} rows')
    return 0


# --------------------------------------------------------------------------- export readers

def _rows(name):
    path = os.path.join(OUT_DIR, name)
    if not os.path.isfile(path):
        raise Error(f'{path} is missing -- run `tools/ghidra_stock.py --version {VERSION} analyze` '
                    f'first.')
    with open(path, encoding='utf-8', errors='replace') as fh:
        for line in fh:
            if line.startswith('#'):
                continue
            line = line.rstrip('\n')
            if line:
                yield line.split('\t')


def functions():
    """[(addr, size, name)] sorted by address, with a parallel list of starts for bisect."""
    global _FUNCS
    if _FUNCS is None:
        fs = [(int(a, 16), int(s), n) for a, s, n in (r[:3] for r in _rows('functions.txt'))]
        fs.sort()
        _FUNCS = (fs, [f[0] for f in fs])
    return _FUNCS


def func_containing(addr):
    """The (addr, size, name) whose body covers `addr`, or None."""
    fs, starts = functions()
    i = bisect.bisect_right(starts, addr) - 1
    if i < 0:
        return None
    f = fs[i]
    return f if addr < f[0] + f[1] else None


def parse_addr(text):
    try:
        return int(text, 16) if re.fullmatch(r'(0[xX])?[0-9a-fA-F]+', text) else int(text, 0)
    except ValueError:
        raise Error(f'not an address: {text!r}')


def strings_at():
    return {int(r[0], 16): r[1] for r in _rows('strings.txt') if len(r) >= 2}


def describe(addr):
    """A short human label for an address: function, string, or bare."""
    f = func_containing(addr)
    if f:
        off = addr - f[0]
        return f'{f[2]} @0x{f[0]:08x}' + (f'+0x{off:x}' if off else '')
    return '-'


# --------------------------------------------------------------------------- queries

def cmd_xrefs(opts):
    addr = parse_addr(opts.addr)
    s = strings_at().get(addr)
    if s is not None:
        print(f'0x{addr:08x}  string  "{s}"')
    f = func_containing(addr)
    if f:
        print(f'0x{addr:08x}  in function {f[2]} @0x{f[0]:08x} (0x{f[1]:x} bytes)')
    hits, below = [], []
    for r in _rows('xrefs.txt'):
        if len(r) < 5:
            continue
        to = int(r[1], 16)
        if to == addr:
            hits.append(r[:5])
        elif to < addr:
            below.append((to, r[:5]))
    if hits:
        print(f'{len(hits)} reference(s) to 0x{addr:08x}:')
        for frm, _to, typ, ffa, ffn in hits:
            where = f'{ffn} @{ffa}' if ffa != '-' else '(not in a function)'
            print(f'  from {frm}  {typ:<16} {where}')
        return 0

    # Nothing points at it directly. On this image that usually means the datum lives inside a
    # blob that is addressed by index (the i18n / toast packs at 0x3c490000, the bitmap area):
    # the useful answer is then "who reaches the container", so show the nearest thing that is
    # referenced at or below the address.
    print(f'no direct reference to 0x{addr:08x}')
    if not below:
        return 1
    base = max(t for t, _ in below)
    refs = [r for t, r in below if t == base]
    print(f'nearest referenced address below is 0x{base:08x} (0x{addr - base:x} bytes lower) -- '
          f'if this is a packed table, that is its base:')
    for frm, _to, typ, ffa, ffn in refs:
        where = f'{ffn} @{ffa}' if ffa != '-' else '(not in a function)'
        print(f'  from {frm}  {typ:<16} {where}')
    return 1


def _calls():
    return [r[:5] for r in _rows('calls.txt') if len(r) >= 5]


def callers_of(addr):
    """Call edges landing on the function containing `addr` (entry or interior)."""
    f = func_containing(addr)
    lo, hi = (f[0], f[0] + f[1]) if f else (addr, addr + 1)
    return [c for c in _calls() if lo <= int(c[3], 16) < hi]


def cmd_callers(opts):
    addr = parse_addr(opts.addr)
    f = func_containing(addr)
    if f:
        print(f'callers of {f[2]} @0x{f[0]:08x} (0x{f[1]:x} bytes)')
    else:
        print(f'callers of 0x{addr:08x} (no function defined there)')
    hits = callers_of(addr)
    if not hits:
        print('  none in calls.txt -- it may be reached only through a vtable or a jump table;')
        print(f'  try `xrefs 0x{addr:08x}` for data references (vtable slots show up there).')
        return 1
    seen = set()
    for cfa, cfn, site, callee, _cn in sorted(hits, key=lambda c: c[2]):
        key = (cfa, site)
        if key in seen:
            continue
        seen.add(key)
        who = f'{cfn} @{cfa}' if cfa != '-' else '(not in a function)'
        print(f'  {site} -> {callee}   in {who}')
    return 0


def cmd_func(opts):
    addr = parse_addr(opts.addr)
    f = func_containing(addr)
    if not f:
        print(f'no function contains 0x{addr:08x}')
        return 1
    start, size, name = f
    print(f'{name}')
    print(f'  entry   0x{start:08x}')
    print(f'  size    {size} bytes (0x{size:x}), ends 0x{start + size:08x}')
    calls = _calls()
    ins = [c for c in calls if start <= int(c[3], 16) < start + size]
    outs = [c for c in calls if c[0] == f'0x{start:08x}']
    print(f'  callers ({len({(c[0], c[2]) for c in ins})} call sites)')
    for cfa, cfn, site, _callee, _cn in sorted(ins, key=lambda c: c[2]):
        who = f'{cfn} @{cfa}' if cfa != '-' else '(not in a function)'
        print(f'    {site}  in {who}')
    tgt = sorted({(c[3], c[4]) for c in outs})
    print(f'  callees ({len(tgt)} distinct)')
    for callee, cn in tgt:
        print(f'    {callee}  {cn}')
    return 0


def cmd_decompile(opts):
    addr = parse_addr(opts.addr)
    if not os.path.isfile(os.path.join(PROJ_DIR, PROJ_NAME + '.gpr')):
        raise Error(f'no Ghidra project at {PROJ_DIR}/{PROJ_NAME}.gpr -- run '
                    f'`tools/ghidra_stock.py --version {VERSION} analyze` first.')
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, 'decomp.c')
        rc = run_headless([PROJ_DIR, PROJ_NAME,
                           '-process', PROGRAM, '-noanalysis', '-readOnly',
                           '-scriptPath', SCRIPT_DIR,
                           '-postScript', 'StockDecompile.java', f'0x{addr:08x}', out,
                           '-log', os.path.join(tmp, 'decompile.log')],
                          echo=opts.verbose, capture=not opts.verbose)
        if rc != 0 or not os.path.isfile(out):
            raise Error('the decompiler run failed (is the project open in the Ghidra GUI?)')
        sys.stdout.write(open(out, encoding='utf-8', errors='replace').read())
    return 0


# --------------------------------------------------------------------------- cli

def main(argv=None):
    p = argparse.ArgumentParser(
        prog='ghidra_stock.py',
        description=__doc__.split('\n\n')[0],
        epilog='See the module docstring for the manual.',
        formatter_class=argparse.RawDescriptionHelpFormatter)
    # --image / --version work before or after the subcommand: the top-level copies carry the
    # defaults, the per-subcommand copies (SUPPRESS) only override when given.
    which = argparse.ArgumentParser(add_help=False)
    for parser, default in ((p, None), (which, argparse.SUPPRESS)):
        parser.add_argument('--image', metavar='PATH', default=default,
                            help='the xteink_app image to analyse / query '
                                 '(default images/device/stock-app0-7.2.4.bin)')
        parser.add_argument('--version', metavar='TAG', default=default,
                            help='its version tag: names the project and the exports directory '
                                 'images/ghidra/stock-TAG/ (default 7.2.4; alone, it selects '
                                 'images/device/stock-app*-TAG.bin)')
    sub = p.add_subparsers(dest='cmd', required=True)

    a = sub.add_parser('analyze', parents=[which],
                       help='import + analyse the image and write the exports')
    a.add_argument('--force', action='store_true', help='re-analyse even if the exports are current')
    a.set_defaults(fn=cmd_analyze)

    for name, fn, helptext in (
            ('xrefs', cmd_xrefs, 'every reference that points at ADDR'),
            ('callers', cmd_callers, 'call sites that reach the function containing ADDR'),
            ('func', cmd_func, 'name, size, callers and callees of the function at ADDR')):
        s = sub.add_parser(name, parents=[which], help=helptext)
        s.add_argument('addr', help='hex address, e.g. 0x42128a84')
        s.set_defaults(fn=fn)

    d = sub.add_parser('decompile', parents=[which],
                       help='decompile the function containing ADDR to stdout')
    d.add_argument('addr', help='hex address, e.g. 0x4233abe0')
    d.add_argument('-v', '--verbose', action='store_true',
                   help="show Ghidra's own log instead of only the C")
    d.set_defaults(fn=cmd_decompile)

    opts = p.parse_args(argv)
    try:
        select(opts.image, opts.version)
        return opts.fn(opts)
    except Error as e:
        print(f'ghidra_stock.py: {e}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
