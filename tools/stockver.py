#!/usr/bin/env python3
r"""Per-version constants for the stock `xteink_app` tools: `tools/stockver.d/<version>.json`.

  stockver.py                       list every known version and validate every version file
  stockver.py versions [--json]     the same, explicitly
  stockver.py identify IMAGE        say which version an image is (exit 2 if none matches)

`tools/stockdev.py`, `tools/stockpatch.py` and `tools/stockstrings.py` used to hard-code the
offsets found in stock 7.2.4 and refuse everything else. They now ask this module, which selects a
version file by what the image says about itself, so a second firmware is a **data file, not a code
edit**: drop a `tools/stockver.d/<version>.json` next to the others and the three tools work on it.

WHAT AN IMAGE SAYS ABOUT ITSELF (`esp_app_desc`, ESP-IDF)
    At image offset 0x20 (right after the 24-byte image header and the first 8-byte segment header):

        +0x00 u32 magic 0xABCD5432   +0x10 char version[32]   +0x30 char project_name[32]
        +0x50 char time[16]          +0x60 char date[16]      +0x70 char idf_ver[32]
        +0x90 u8  app_elf_sha256[32]

    `(project_name, version)` **selects** the version file. `app_elf_sha256` is the **identity** of
    the build and is checked on every run: our byte patches change the image but never the ELF hash,
    so a mismatch means a different build wearing the same version number and everything below it
    would be a guess -- it is refused. The file's own sha256 is informational for exactly that
    reason (patching changes it).

    A bare app image starts at 0; a 16 MB flash image has app0 at 0x10000 (`stockdev.app_base`
    tells them apart and `identify()` uses it).

THE SCHEMA (`tools/stockver.d/<version>.json`, one file per version -- this docstring is normative)

    Addresses and offsets are **hex strings** ("0x4eabe0", plain ints are accepted too); byte
    strings are **space-separated hex** ("36 41 00 0c 02 1d f0"); counts and sizes are plain ints.
    Any section or named entry may be **absent**: that means "not located in this version", and the
    loader refuses *that item* naming the version and the item, not the whole file. Unknown extra
    keys are allowed and ignored (a place for notes, evidence and future work).

    {
      "project": "xteink_app",                  // required, with "version": the selector
      "version": "7.2.4",                       // required
      "app_elf_sha256": "5e6f90d4...0f4df",     // required: esp_app_desc +0x90, checked every run
      "app_sha256": "c1fed9ff...a2fc1",         // the pristine file (informational: patching changes it)
      "app_bytes": 5503680,                     // the pristine file's size (informational)
      "built": "19:25:05 Aug 14 2026",          // esp_app_desc time + date (documentation)
      "idf": "v6.0.1",
      "source": "images/device/stock-app0-7.2.4.bin",
      "note": "free text: what this version is and where it came from",

      "dev_stub": {                             // tools/stockdev.py
        "app_offset": "0x4eabe0",               //   offset of the stub in the *app* image
        "va": "0x4233abe0",                     //   its app virtual address (documentation)
        "stub": "36 41 00 0c 02 1d f0",         //   the unpatched bytes, whole instructions
        "movi_index": 4,                        //   the one byte inside `stub` that changes
        "unpatched": "0x02", "patched": "0x12", //   movi.n a2,0 / movi.n a2,1
        "evidence": "how this site was identified in this version"
      },

      "patches": {                              // tools/stockpatch.py's manifest, in file order
        "developer-menu": {
          "app_offset": "0x4eabe0", "va": "0x4233abe0",
          "original": "36 41 00 0c 02 1d f0",   //   both must be the same length and differ
          "patched":  "36 41 00 0c 12 1d f0",
          "description": "developer mode: Memory / Developer / Screen Capture in the light panel",
          "detail": "movi.n a2,0 -> movi.n a2,1 in the developer-mode predicate stub (byte +4)",
          "docs": "docs/xic.md", "evidence": "..."
        },
        "hidden-menu-rows": { "...": "same shape" },
        "lua-apps-row":     { "...": "same shape" }
      },

      "packs": {                                // tools/stockstrings.py
        "counts": {"groups": 18,  "limit_va": "0x3c3f7d54", "header_va": "0x3c3f7a08",
                   "what": "count formats (%d books, %d/%d)"},
        "labels": {"groups": 234, "limit_va": "0x3c3fb988", "header_va": "0x3c3f7d54",
                   "what": "every menu / settings / panel label"},
        "toasts": {"groups": 688, "limit_va": "0x3c4a0e31", "offsets_va": "0x3c490124",
                   "blob_va": "0x3c4916a4", "blob_size": 63373,
                   "what": "toasts, dialogs, buttons (headerless)"}
      },
      "lang_count": 4,                          // languages per group (zh-CN, en, zh-TW, ja)
      "known_groups": {"0": "Bookshelf", "6": "Read", "9": "Lua Apps"}   // label ids, keys as strings
    }

    A pack is either **headed** (`header_va`, which carries group_count / lang_count / blob_size and
    is followed by the offset table) or **headerless** (`offsets_va` + `blob_va` + `blob_size`).
    `limit_va` is the first VA after the pack that holds live data -- the free tail never reaches it.

THE API THE THREE TOOLS USE
    identify(data, base=None) -> (base, Version)   the whole check: app_base, esp_app_desc, the
                                                   version file, the ELF hash. Raises UnknownVersion
                                                   (no file) or StockVerError (hash mismatch, ...).
    app_desc(data, base=0)    -> {'project', 'version', 'elf_sha256', 'built', 'idf'} or None
    versions()                -> [Version], every valid file, sorted
    problems()                -> {path: why} for every file that does not load
    baseline()                -> the reference version (7.2.4 when its file is there)
    known_line() / where_to_add()  the two phrases the tools put in their refusal messages

    A `Version` carries `.project`, `.version`, `.label`, `.app_elf_sha256`, `.app_bytes`, `.path`
    and the sections; `.need_dev_stub()`, `.need_patch(name)`, `.need_pack(name)` and
    `.need_lang_count()` return the item or raise `StockVerError` naming the version and the item,
    which is what "absent means not located in this version" comes to in practice.

ADDING A VERSION
    Copy `tools/stockver.d/7.2.4.json`, put the new `esp_app_desc` fields at the top (the ELF hash
    matters), delete every section whose sites have not been found in the new build yet, and find
    them one at a time (`tools/appdis.py`, `tools/ghidra_stock.py`). `tools/stockver.py` then lists
    it and the three tools accept the image. Never guess an offset across versions: the tools refuse
    an item that is absent, and that is the safe answer.
"""
import argparse, json, os, sys
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # `import stockdev` in app_base()

DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'stockver.d')
BASELINE = ('xteink_app', '7.2.4')      # the reference version, when its file is present
DESC_OFF = 0x20                         # esp_app_desc: 24-byte image header + 8-byte segment header
DESC_MAGIC = 0xABCD5432
DESC_VERSION, DESC_PROJECT, DESC_TIME, DESC_DATE, DESC_IDF, DESC_ELF = \
    0x10, 0x30, 0x50, 0x60, 0x70, 0x90
DESC_BYTES = 0xB0                       # what we read of the descriptor
SHA_HEX = 64


class StockVerError(ValueError):
    """A version file is malformed, an image is not the build a version file describes, or the
    item a tool asked for is not located in this version."""


class UnknownVersion(StockVerError):
    """No `tools/stockver.d/*.json` describes this image. `.project` / `.version` are what the
    image calls itself (None when it has no `esp_app_desc`), `.base` where its app starts."""

    def __init__(self, message, project=None, version=None, base=0):
        super().__init__(message)
        self.project, self.version, self.base = project, version, base


# ---------------------------------------------------------------- JSON field parsing
def _err(where, msg):
    raise StockVerError(f'{where}: {msg}')


def _int(value, where):
    """An address/offset: "0x4eabe0", "1234" or a plain int."""
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        _err(where, f'expected a number or a hex string, got {value!r}')
    if isinstance(value, int):
        return value
    try:
        return int(value, 0)
    except ValueError:
        _err(where, f'{value!r} is not a number ("0x4eabe0" or 1234)')


def _bytes(value, where):
    """A byte string: "36 41 00 0c 02 1d f0" (space-separated hex, at least one byte)."""
    if not isinstance(value, str):
        _err(where, f'expected space-separated hex, got {value!r}')
    try:
        out = bytes.fromhex(value.replace(',', ' '))
    except ValueError:
        _err(where, f'{value!r} is not space-separated hex ("36 41 00")')
    if not out:
        _err(where, 'is empty')
    return out


def _text(d, key, where, required=False, default=''):
    v = d.get(key, None)
    if v is None:
        if required:
            _err(where, f'no {key!r}')
        return default
    if not isinstance(v, str):
        _err(where, f'{key} must be a string, got {v!r}')
    return v


def _sha(value, where):
    v = str(value).strip().lower()
    if len(v) != SHA_HEX or any(c not in '0123456789abcdef' for c in v):
        _err(where, f'{value!r} is not a 64-character hex sha256')
    return v


def _need(d, key, where):
    if key not in d:
        _err(where, f'no {key!r}')
    return d[key]


def _table(d, key, where):
    v = d.get(key, None)
    if v is None:
        return {}
    if not isinstance(v, dict):
        _err(where, f'{key} must be an object, got {type(v).__name__}')
    return v


# ---------------------------------------------------------------- the typed sections
@dataclass(frozen=True)
class DevStub:
    """`tools/stockdev.py`'s developer-mode predicate stub: whole instructions, one byte of which
    is the immediate that turns `movi.n a2,0` into `movi.n a2,1`."""
    app_offset: int
    va: int
    stub: bytes
    movi_index: int
    unpatched: int
    patched: int
    evidence: str = ''

    @property
    def movi_offset(self) -> int:
        return self.app_offset + self.movi_index

    @property
    def size(self) -> int:
        return len(self.stub)

    def variant(self, value: int) -> bytes:
        """The whole stub with the immediate byte set to `value` (unpatched or patched)."""
        return self.stub[:self.movi_index] + bytes((value,)) + self.stub[self.movi_index + 1:]


@dataclass(frozen=True)
class Patch:
    """One named byte patch. `app_offset` is an offset into the *app image*; a 16 MB flash image
    adds `app_base` (0x10000). `original` and `patched` are equal-length byte strings and include
    enough context (a whole instruction, or the stub around it) to identify the site."""
    name: str
    description: str
    app_offset: int
    original: bytes
    patched: bytes
    va: int = 0                          # the app virtual address of app_offset (documentation)
    detail: str = ''                     # what the changed instruction does
    docs: str = ''
    evidence: str = ''

    @property
    def size(self) -> int:
        return len(self.original)

    def window(self, data, base=0) -> bytes:
        off = base + self.app_offset
        return bytes(data[off:off + self.size])

    def state(self, data, base=0) -> str:
        """'applied', 'original' or 'unknown' for the bytes at this site."""
        got = self.window(data, base)
        return {self.patched: 'applied', self.original: 'original'}.get(got, 'unknown')


@dataclass(frozen=True)
class PackSpec:
    """Where a string pack lives and how big it must be (a fingerprint of one version).

    `header_va` is None for a headerless pack, which instead names `offsets_va`, `blob_va` and
    `blob_size` outright. `limit_va` is the first VA after the pack that holds live data: the free
    tail can never reach it."""
    name: str
    groups: int
    limit_va: int
    header_va: Optional[int] = None
    offsets_va: Optional[int] = None
    blob_va: Optional[int] = None
    blob_size: Optional[int] = None
    what: str = ''
    evidence: str = ''


@dataclass(frozen=True)
class Version:
    """One `tools/stockver.d/*.json`, parsed and validated."""
    project: str
    version: str
    app_elf_sha256: str
    path: str
    app_sha256: str = ''
    app_bytes: int = 0
    built: str = ''
    idf: str = ''
    source: str = ''
    note: str = ''
    dev_stub: Optional[DevStub] = None
    patches: Dict[str, Patch] = field(default_factory=dict)
    packs: Dict[str, PackSpec] = field(default_factory=dict)
    lang_count: Optional[int] = None
    known_groups: Dict[int, str] = field(default_factory=dict)

    @property
    def key(self) -> Tuple[str, str]:
        return self.project, self.version

    @property
    def label(self) -> str:
        return f'{self.project} {self.version}'

    @property
    def file(self) -> str:
        """The version file, relative to the repository root, for messages."""
        return os.path.join('tools', 'stockver.d', os.path.basename(self.path))

    # --- "absent means: not located in this version" ------------------------------------------
    def _absent(self, what, how):
        return StockVerError(
            f'{what} is not located in {self.label}: {self.file} has no entry for it. '
            f'{how} and add it to {self.file} (the schema is the module docstring of '
            f'tools/stockver.py); an address borrowed from another version would land somewhere '
            f'else entirely in this build')

    def need_dev_stub(self) -> DevStub:
        if self.dev_stub is None:
            raise self._absent('the developer-mode predicate stub (`dev_stub`)',
                               'Find it again (tools/appdis.py, tools/ghidra_stock.py)')
        return self.dev_stub

    def need_patch(self, name) -> Patch:
        if name not in self.patches:
            raise self._absent(f'the patch site {name!r}',
                               'Find it again (tools/appdis.py, tools/ghidra_stock.py)')
        return self.patches[name]

    def need_pack(self, name) -> PackSpec:
        if name not in self.packs:
            raise self._absent(f'the {name} string pack',
                               'Find it again (tools/stockstrings.py on a known version shows what '
                               'the pack looks like)')
        return self.packs[name]

    def need_lang_count(self) -> int:
        if self.lang_count is None:
            raise self._absent('`lang_count` (the languages per string group)',
                               'Read it out of a pack header')
        return self.lang_count


# ---------------------------------------------------------------- reading a version file
def parse(doc, path='<memory>') -> Version:
    """Validate one already-decoded version document. Raises StockVerError naming the file."""
    where = os.path.basename(path)
    if not isinstance(doc, dict):
        _err(where, f'the top level must be an object, got {type(doc).__name__}')
    project = _text(doc, 'project', where, required=True)
    version = _text(doc, 'version', where, required=True)
    if not project.strip() or not version.strip():
        _err(where, 'project and version must not be empty')
    elf = _sha(_need(doc, 'app_elf_sha256', where), f'{where}: app_elf_sha256')

    stub = None
    if 'dev_stub' in doc and doc['dev_stub'] is not None:
        d, w = _table(doc, 'dev_stub', where), f'{where}: dev_stub'
        if not d:
            _err(w, 'is empty; leave the key out when the stub has not been located')
        raw = _bytes(_need(d, 'stub', w), f'{w}.stub')
        idx = _int(_need(d, 'movi_index', w), f'{w}.movi_index')
        un = _int(_need(d, 'unpatched', w), f'{w}.unpatched')
        pa = _int(_need(d, 'patched', w), f'{w}.patched')
        if not 0 <= idx < len(raw):
            _err(w, f'movi_index {idx} is outside the {len(raw)}-byte stub')
        for name, value in (('unpatched', un), ('patched', pa)):
            if not 0 <= value <= 0xff:
                _err(w, f'{name} {value:#x} is not a byte')
        if un == pa:
            _err(w, 'unpatched and patched are the same byte')
        if raw[idx] != un:
            _err(w, f'stub[{idx}] is {raw[idx]:#04x} but unpatched says {un:#04x}')
        stub = DevStub(app_offset=_int(_need(d, 'app_offset', w), f'{w}.app_offset'),
                       va=_int(d.get('va', 0), f'{w}.va'), stub=raw, movi_index=idx,
                       unpatched=un, patched=pa, evidence=_text(d, 'evidence', w))

    patches = {}
    for name, d in _table(doc, 'patches', where).items():
        w = f'{where}: patches.{name}'
        if not isinstance(d, dict):
            _err(w, f'must be an object, got {type(d).__name__}')
        orig = _bytes(_need(d, 'original', w), f'{w}.original')
        new = _bytes(_need(d, 'patched', w), f'{w}.patched')
        if len(orig) != len(new):
            _err(w, f'original is {len(orig)} bytes and patched {len(new)}: they must match')
        if orig == new:
            _err(w, 'original and patched are the same bytes')
        patches[name] = Patch(name=name, description=_text(d, 'description', w),
                              app_offset=_int(_need(d, 'app_offset', w), f'{w}.app_offset'),
                              original=orig, patched=new, va=_int(d.get('va', 0), f'{w}.va'),
                              detail=_text(d, 'detail', w), docs=_text(d, 'docs', w),
                              evidence=_text(d, 'evidence', w))

    packs = {}
    for name, d in _table(doc, 'packs', where).items():
        w = f'{where}: packs.{name}'
        if not isinstance(d, dict):
            _err(w, f'must be an object, got {type(d).__name__}')
        header = d.get('header_va', None)
        spec = PackSpec(name=name, groups=_int(_need(d, 'groups', w), f'{w}.groups'),
                        limit_va=_int(_need(d, 'limit_va', w), f'{w}.limit_va'),
                        header_va=None if header is None else _int(header, f'{w}.header_va'),
                        offsets_va=None if d.get('offsets_va') is None
                        else _int(d['offsets_va'], f'{w}.offsets_va'),
                        blob_va=None if d.get('blob_va') is None
                        else _int(d['blob_va'], f'{w}.blob_va'),
                        blob_size=None if d.get('blob_size') is None
                        else _int(d['blob_size'], f'{w}.blob_size'),
                        what=_text(d, 'what', w), evidence=_text(d, 'evidence', w))
        if spec.groups <= 0:
            _err(w, f'groups {spec.groups} must be positive')
        if spec.header_va is None and (spec.offsets_va is None or spec.blob_va is None
                                       or spec.blob_size is None):
            _err(w, 'a headerless pack needs offsets_va, blob_va and blob_size '
                    '(a headed one needs header_va)')
        packs[name] = spec

    langs = doc.get('lang_count', None)
    if langs is not None:
        langs = _int(langs, f'{where}: lang_count')
        if not 1 <= langs <= 16:
            _err(where, f'lang_count {langs} is out of range (1..16)')

    groups = {}
    for k, v in _table(doc, 'known_groups', where).items():
        try:
            groups[int(str(k), 0)] = str(v)
        except ValueError:
            _err(f'{where}: known_groups', f'the key {k!r} is not a group number')

    return Version(project=project, version=version, app_elf_sha256=elf, path=path,
                   app_sha256=_sha(doc['app_sha256'], f'{where}: app_sha256')
                   if doc.get('app_sha256') else '',
                   app_bytes=_int(doc.get('app_bytes', 0), f'{where}: app_bytes'),
                   built=_text(doc, 'built', where), idf=_text(doc, 'idf', where),
                   source=_text(doc, 'source', where), note=_text(doc, 'note', where),
                   dev_stub=stub, patches=patches, packs=packs, lang_count=langs,
                   known_groups=groups)


def load_file(path) -> Version:
    """Read and validate one version file. Raises StockVerError naming the file and the reason."""
    try:
        with open(path, 'rb') as f:
            doc = json.loads(f.read().decode('utf-8'))
    except (OSError, UnicodeDecodeError) as e:
        raise StockVerError(f'{os.path.basename(path)}: cannot be read ({e})') from None
    except json.JSONDecodeError as e:
        raise StockVerError(f'{os.path.basename(path)}: is not valid JSON ({e})') from None
    return parse(doc, path)


# ---------------------------------------------------------------- the directory, cached by mtime
_cache = {'signature': None, 'versions': {}, 'problems': {}}


def _signature():
    """(name, size, mtime) of every *.json in the directory -- so a version file another agent
    writes while we run is picked up on the next call instead of being missed or half-read."""
    try:
        entries = [e for e in os.scandir(DIR) if e.is_file() and e.name.endswith('.json')]
    except (FileNotFoundError, NotADirectoryError):
        return ()
    out = []
    for e in sorted(entries, key=lambda e: e.name):
        try:
            st = e.stat()
        except OSError:
            continue
        out.append((e.name, st.st_size, st.st_mtime_ns))
    return tuple(out)


def load_all(force=False):
    """({(project, version): Version}, {path: why}) for tools/stockver.d/. Never raises: a file
    that does not load lands in the second dict and the others still work."""
    sig = _signature()
    if force or sig != _cache['signature']:
        good, bad = {}, {}
        for name, _size, _mtime in sig:
            path = os.path.join(DIR, name)
            try:
                v = load_file(path)
            except StockVerError as e:
                bad[path] = str(e)
                continue
            if v.key in good:
                bad[path] = (f'{os.path.basename(path)}: {v.label} is already described by '
                             f'{os.path.basename(good[v.key].path)}')
                continue
            good[v.key] = v
        _cache.update(signature=sig, versions=good, problems=bad)
    return _cache['versions'], _cache['problems']


def versions(force=False):
    """Every valid version, sorted by project then version."""
    good, _ = load_all(force)
    return [good[k] for k in sorted(good)]


def problems(force=False):
    """{path: why} for every file in tools/stockver.d/ that does not load."""
    return dict(load_all(force)[1])


def get(version, project=BASELINE[0]) -> Version:
    """One version by name. Raises StockVerError when there is no such file."""
    good, bad = load_all()
    if (project, version) in good:
        return good[(project, version)]
    hint = _broken_note(project, version, bad)
    raise StockVerError(f'no version file for {project} {version} in tools/stockver.d/'
                        f'{hint} (known: {known_line()})')


def baseline() -> Optional[Version]:
    """The reference version (7.2.4 while its file is there, else the first known one, else None).
    Only used to word the refusal messages -- never to supply an offset for another build."""
    good, _ = load_all()
    if BASELINE in good:
        return good[BASELINE]
    every = versions()
    return every[0] if every else None


def known_line() -> str:
    """'xteink_app 7.2.4, xteink_app 7.5.4' -- the versions the tools can work on."""
    every = versions()
    return ', '.join(v.label for v in every) if every else '(none: tools/stockver.d/ is empty)'


def where_to_add() -> str:
    """The one sentence every refusal ends with."""
    return ('add a version file tools/stockver.d/<version>.json (the schema is the module docstring '
            'of tools/stockver.py, `tools/stockver.py versions` validates it)')


def _broken_note(project, version, bad):
    """When a file that should describe this version is malformed, say so instead of pretending the
    version is simply unknown (the file name is the only clue such a file still gives)."""
    for path, why in sorted(bad.items()):
        stem = os.path.splitext(os.path.basename(path))[0]
        if stem in (version, f'{project}-{version}'):
            return f' -- but tools/stockver.d/{os.path.basename(path)} does not load: {why}'
    return ''


# ---------------------------------------------------------------- identifying an image
def app_desc(data, base=0):
    """{'project', 'version', 'built', 'idf', 'elf_sha256'} from the image's `esp_app_desc`, or
    None when the descriptor magic is not there."""
    off = base + DESC_OFF
    if len(data) < off + DESC_BYTES:
        return None
    if int.from_bytes(bytes(data[off:off + 4]), 'little') != DESC_MAGIC:
        return None

    def s(at, n=32):
        return bytes(data[off + at:off + at + n]).split(b'\0', 1)[0].decode('utf-8', 'replace')

    return {'project': s(DESC_PROJECT), 'version': s(DESC_VERSION),
            'built': f'{s(DESC_TIME, 16)} {s(DESC_DATE, 16)}'.strip(), 'idf': s(DESC_IDF),
            'elf_sha256': bytes(data[off + DESC_ELF:off + DESC_ELF + 32]).hex()}


def app_base(data):
    """0 for a bare app image, 0x10000 for a 16 MB flash image (`stockdev.app_base`, imported here
    rather than at module level: stockdev imports this module)."""
    import stockdev                                                          # noqa: E402
    try:
        return stockdev.app_base(data)
    except stockdev.StockDevError as e:
        raise StockVerError(str(e)) from None


def identify(data, base=None):
    """(base, Version) for an image: where its app starts and which version file describes it.

    Raises UnknownVersion when no file matches (the message names every known version and where to
    add one) and StockVerError when the file's `app_elf_sha256` is not the image's -- a different
    build of the same version number, whose offsets are somebody else's."""
    if base is None:
        base = app_base(data)
    good, bad = load_all()
    desc = app_desc(data, base)
    if desc is None:
        raise UnknownVersion(
            f'no esp_app_desc at {base + DESC_OFF:#x} (magic {DESC_MAGIC:#x}): this is not an '
            f'ESP-IDF application image, so there is nothing to match against tools/stockver.d/ '
            f'(known: {known_line()})', None, None, base)
    key = (desc['project'], desc['version'])
    if key not in good:
        raise UnknownVersion(
            f'the image at {base:#x} is {desc["project"]} {desc["version"]}, which no version file '
            f'in tools/stockver.d/ describes'
            f'{_broken_note(desc["project"], desc["version"], bad)} '
            f'(known: {known_line()}); {where_to_add()}',
            desc['project'], desc['version'], base)
    ver = good[key]
    if desc['elf_sha256'] != ver.app_elf_sha256:
        raise StockVerError(
            f'the image at {base:#x} calls itself {ver.label} but its app_elf_sha256 is '
            f'{desc["elf_sha256"]}, not the {ver.app_elf_sha256} in {ver.file}: this is a different '
            f'build wearing the same version number, and every offset in that file was found in the '
            f'other one. Patching changes an image but never its ELF hash, so this is not one of our '
            f'own edits -- {where_to_add()} for this build, or check where the image came from')
    return base, ver


def identify_file(path):
    """(data, base, Version) for an image on disk."""
    data = bytearray(open(path, 'rb').read())
    base, ver = identify(data)
    return data, base, ver


# ---------------------------------------------------------------- CLI
def _describe(v, data=None):
    lines = [f'{v.label}   {v.file}']
    if v.built or v.idf:
        lines.append(f'  built {v.built}   idf {v.idf}')
    lines.append(f'  app_elf_sha256 {v.app_elf_sha256}')
    if v.app_sha256 or v.app_bytes:
        lines.append(f'  pristine file  {v.app_sha256 or "?"}  ({v.app_bytes or "?"} bytes, '
                     f'informational: patching changes it)')
    if v.source:
        lines.append(f'  source {v.source}')
    stub = v.dev_stub
    lines.append(f'  dev_stub     {stub.app_offset:#x} (VA {stub.va:#x}) {stub.stub.hex(" ")} '
                 f'byte +{stub.movi_index} {stub.unpatched:#04x} -> {stub.patched:#04x}'
                 if stub else '  dev_stub     -- not located in this version')
    lines.append(f'  patches      {", ".join(v.patches) if v.patches else "-- none located"}')
    lines.append(f'  packs        ' + (', '.join(f'{n} ({p.groups} groups)'
                                                 for n, p in v.packs.items())
                                       if v.packs else '-- none located'))
    lines.append(f'  lang_count   {v.lang_count if v.lang_count is not None else "-- not located"}'
                 f'   known_groups {len(v.known_groups)}')
    if v.note:
        lines.append(f'  note: {v.note}')
    return lines


def _do_versions(a):
    every, bad = versions(), problems()
    print(f'tools/stockver.d/: {len(every)} version(s), {len(bad)} unreadable file(s)')
    for v in every:
        for line in _describe(v):
            print('  ' + line)
    for path in sorted(bad):
        print(f'  BROKEN  tools/stockver.d/{os.path.basename(path)}: {bad[path]}', file=sys.stderr)
    if not every and not bad:
        print(f'  (empty: {where_to_add()})')
    return 1 if bad else 0


def _do_identify(a):
    data = bytearray(open(a.image, 'rb').read())
    base, ver = identify(data)
    kind = ('16 MB flash image, app0 at 0x10000' if base else 'bare app image')
    desc = app_desc(data, base)
    print(f'{a.image}: {kind}, {len(data)} bytes')
    print(f'  esp_app_desc at {base + DESC_OFF:#x}: {desc["project"]} {desc["version"]}, '
          f'built {desc["built"]}, idf {desc["idf"]}')
    for line in _describe(ver):
        print('  ' + line)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd')
    sub.add_parser('versions', help='list every known version and validate every version file')
    ip = sub.add_parser('identify', help='say which version an image is')
    ip.add_argument('image', help='a stock app image, or a 16 MB flash image (app0 at 0x10000)')
    a = ap.parse_args(argv)
    try:
        return _do_identify(a) if a.cmd == 'identify' else _do_versions(a)
    except StockVerError as e:
        print(f'{getattr(a, "image", "tools/stockver.d")}: {e}', file=sys.stderr)
        return 2
    except OSError as e:
        print(e, file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
