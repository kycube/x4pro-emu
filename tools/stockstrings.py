#!/usr/bin/env python3
r"""Read and relabel the UI string packs of a stock `xteink_app` image (a data-only edit).

  stockstrings.py list IMAGE [--pack P] [--lang en] [--grep TEXT]   every group of a pack
  stockstrings.py get IMAGE GROUP [--pack P] [--lang en]            one group
  stockstrings.py set IMAGE GROUP LANG "new text" [--pack P]        relabel, re-checksum, verify
  stockstrings.py compact IMAGE [--pack P]                          repack the blob, free its tail
  stockstrings.py restore IMAGE --from ORIGINAL [--pack P]          copy the pack region back

IMAGE is a bare app image (`images/device/stock-app0-7.2.4.bin`, 5,503,680 bytes, ESP magic 0xE9 at
0) or a whole 16 MB flash image whose app0 slot starts at 0x10000; `stockdev.app_base` tells them
apart and `stockdev.image_layout` / `refresh_image` / `verify_image` do the ESP image arithmetic, so
every writing command leaves the checksum byte and the appended SHA-256 the bootloader verifies
correct (`-o/--out FILE` writes elsewhere instead of editing IMAGE in place). Languages are named
`zh-CN`, `en`, `zh-TW`, `ja` or 0..3.

THE PACK FORMAT (docs/stock-firmware.md §6)
    UI text is not referenced by pointer from code: it lives in DROM in packs of the shape

        u16 group_count; u16 lang_count = 4; u8 lang_ids[4] = {0,1,2,3};   // zh-CN, en, zh-TW, ja
        u16 blob_size; u16 offsets[group_count][4]; char blob[blob_size];  // offset 0 = "" (missing)

    The group index is the label id; an offset is a byte index into `blob` of a NUL-terminated UTF-8
    string. Identical strings share one offset (the packs are fully deduplicated: the label pack's
    936 slots use 752 distinct offsets), so overwriting a string in place changes **every** slot that
    points at it -- see `set` below.

    **Where the packs are is per version** (`tools/stockver.py`): the numbers below live in
    `tools/stockver.d/<version>.json` (`packs`, `lang_count`, `known_groups`), one file per stock
    version, and an image picks its own by what its `esp_app_desc` says (project + version, with
    `app_elf_sha256` as the identity of the build). A version with no file -- or one whose file does
    not carry the pack asked for -- is refused, never guessed. Stock 7.2.4
    (`tools/stockver.d/7.2.4.json`) as one version among several:

    | pack     | header VA (file) | groups | offsets VA | blob VA (size)         | content |
    |----------|------------------|--------|------------|------------------------|---------|
    | `counts` | 0x3c3f7a08 (0x77a08) | 18  | 0x3c3f7a12 | 0x3c3f7aa2 (689 B)     | count formats (`%d books`) |
    | `labels` | 0x3c3f7d54 (0x77d54) | 234 | 0x3c3f7d5e | 0x3c3f84ae (13,528 B)  | every menu/settings/panel label |
    | `toasts` | none (headerless)    | 688 | 0x3c490124 | 0x3c4916a4 (63,373 B)  | toasts, dialogs, buttons |

    The **toast pack has no header**: its `u16 offsets[688][4]` array starts at 0x3c490124, right
    after the `0xff` that ends the `XTZB` table at 0x3c48fe94, and its blob follows the array at
    0x3c4916a4 with the same conventions (blob[0] = NUL, offset 0 = missing). It was found by
    walking back from the blob the doc names: group 0 = 取消 / Cancel / 取消 / キャンセル, group 3 =
    "区域设置成功\nRegion set successfully". Since no `blob_size` is stored, its blob ends at the last
    string (0x3c4a0e31, where the `user_config` string of the ordinary string table begins) and it
    cannot grow; its 63,373 bytes also put its highest offset (0xf76e) close to the u16 ceiling.

    File offset = VA - segment load address + segment file offset; the load addresses come from the
    image header (`stockdev.image_layout`'s segment payload offsets, minus the 8-byte segment
    header). Every pack is validated before use (lang_count 4, lang ids 0,1,2,3, group count as
    above, every offset inside the blob, every string NUL-terminated), so another firmware version --
    which puts the packs somewhere else -- is refused rather than corrupted.

WHAT `set` DOES, EXACTLY (and the padding budget)
    1. **In place** when the new UTF-8 bytes are no longer than the old string and no other slot
       shares the old offset: the bytes are written over the old ones and the rest of the old slot,
       up to and including its NUL, is NUL-padded. The offset table does not move; nothing else in
       the image changes. This is the safe case and the only one that needs no free space.
    2. **Appended** otherwise (a longer string, or a shared one that must not drag its neighbours
       along, or `--append`): the text is written into the pack's free tail and the group's u16
       offset is repointed at it. `blob_size` grows if the text goes past the old blob end, and the
       old bytes are left where they are (dead space; `compact` reclaims it). Refused when the free
       tail is too small or when the new offset would exceed 65,535.

    **The free tail is empty in the stock image.** docs/stock-firmware.md §6 expects "~1 KB of
    padding" after the label blob before the bitmaps at 0x3c3fc020; there is none. The blob ends at
    0x3c3fb986 and the next four bytes are already live data: 0x3c3fb986 holds two alignment zeros
    and the (u32 offset, u32 length) directory of the DROM bitmap block starts at 0x3c3fb988. The
    other two packs have no slack at all (the count blob ends one byte before the label header, the
    toast blob ends where the next string table begins), and no pack has a single dead byte inside
    its blob. So on a stock image the append budget is **2 bytes for `labels`, 1 for `counts`, 0 for
    `toasts`** -- i.e. every longer label is refused until space is made:

    `compact` makes the space. It rewrites the blob with the same strings, merging any string that
    is a **suffix** of another into it (a NUL-terminated reader cannot tell), keeps `blob_size` and
    the whole region byte-length unchanged, NUL-fills what it frees at the end of the blob and
    repoints every offset. On the stock label pack that frees **497 bytes** (752 strings, 691 of
    which need storage), which is what makes "USB Mode" -> "File Transfer" possible. It is a bigger
    edit than a relabel -- every offset in the pack changes -- so run the emulator afterwards
    (`tests/test_stockstrings.py::test_stock_relabelled_menu` boots a relabelled image and compares
    the nav menu with tests/golden/stock-menu.png).

    Whether the reader bounds-checks against `blob_size` is unknown, so an offset is never pointed
    outside the blob region and `blob_size` is never grown past the free tail.

SAFETY
    A patched image made from the device dump still carries the owner's WiFi credentials in its NVS
    (a flash image) and is still the owner's app: CLAUDE.md rule 6 -- keep it in a scratch directory,
    never under `images/` or in the repository, and never on the real device. Emulator first:

    .venv/bin/python tools/nvsedit.py SCRATCH/flash.bin set-u8 user_config net_en 0
    .venv/bin/python tools/stockstrings.py SCRATCH/flash.bin set 11 en "Transfer"
    .venv/bin/python tools/x4emu --name st1 run --flash SCRATCH/flash.bin --sd SCRATCH/sd.img \
        --boot-hold-power

`parse_pack`, `read_pack`, `set_string`, `compact_pack` and `restore_pack` are importable; each takes
a `bytearray` of the whole image and returns a report dict, and each raises `StockStringsError` on
anything that is not a stock pack of a version `tools/stockver.d/` describes.
"""
import argparse, os, struct, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import stockver                                                                 # noqa: E402
from stockver import PackSpec                                                   # noqa: E402,F401
from stockdev import (app_base, image_layout, refresh_image, verify_image,      # noqa: E402
                      StockDevError)

LANG_NAMES = ('zh-CN', 'en', 'zh-TW', 'ja')     # lang_ids 0..3, in the packs' own order
U16_MAX = 0xFFFF


class StockStringsError(ValueError):
    """The image is not a stock app of a known version, or a pack is not where/what its version
    file says it should be."""


def version_of(data, base=None):
    """(base, stockver.Version) for an image, with this tool's wording on a refusal."""
    try:
        return stockver.identify(data, base)
    except stockver.UnknownVersion as e:
        ref = stockver.baseline()
        tail = (f' -- not stock {ref.label} and not any other version in tools/stockver.d/: the '
                f'string packs sit at different addresses in every build, so reading here would '
                f'read something else entirely' if ref else '')
        raise StockStringsError(f'{e}{tail}') from None
    except stockver.StockVerError as e:
        raise StockStringsError(str(e)) from None


def _baseline():
    """The baseline version (tools/stockver.d/7.2.4.json), for the CLI's `--pack` choices, the help
    epilog and the module-level defaults. The image at hand always decides for itself."""
    return stockver.baseline()


_REF = _baseline()
PACKS = dict(_REF.packs) if _REF else {}        # the baseline version's packs
PACK_NAMES = tuple(PACKS) or ('counts', 'labels', 'toasts')
LANG_COUNT = (_REF.lang_count if _REF and _REF.lang_count else len(LANG_NAMES))
LANGS = LANG_NAMES[:LANG_COUNT]
# a handful of the label pack's group ids, printed by `list` and useful in messages
KNOWN_GROUPS = dict(_REF.known_groups) if _REF else {}


# ---------------------------------------------------------------- VA -> file offset
def segment_map(data, base):
    """[(load VA, file offset of the payload, length)] of the app image at `base`, from the header's
    segment table (`image_layout` parses it; the load address is the u32 in front of the payload)."""
    lay = image_layout(data, base)
    return [(struct.unpack_from('<I', data, off - 8)[0], off, length)
            for off, length in lay['segments']]


def va_to_file(segs, va, what='address', label='a stock app'):
    for load, off, length in segs:
        if load <= va < load + length:
            return off + va - load
    raise StockStringsError(f'{what} {va:#x} is in no segment of this image: not {label} as its '
                            f'version file describes it')


# ---------------------------------------------------------------- the pack itself
class Pack:
    """One parsed pack: file offsets, the offset table and the blob, plus its free tail.

    `offsets` is a flat list of group_count * 4 u16 blob indexes (offset 0 = missing). `free_start`
    is the first blob byte no live string uses at the end of the blob; the tail runs from there to
    `free_end` (the blob end plus the zero padding that follows it, never past `spec.limit_va`)."""

    def __init__(self, data, base, spec, ver):
        self.spec, self.base, self.data, self.ver = spec, base, data, ver
        self.lang_count = ver.need_lang_count()
        self.langs = LANG_NAMES[:self.lang_count]
        lc, label = self.lang_count, ver.label
        segs = segment_map(data, base)
        self.segs = segs
        if spec.header_va is not None:
            self.header_off = va_to_file(segs, spec.header_va, f'{spec.name} pack header', label)
            groups, langs = struct.unpack_from('<HH', data, self.header_off)
            ids = bytes(data[self.header_off + 4:self.header_off + 8])
            self.blob_size = struct.unpack_from('<H', data, self.header_off + 8)[0]
            if langs != lc or ids[:lc] != bytes(range(lc)) or groups != spec.groups:
                raise StockStringsError(
                    f'the {spec.name} pack at {spec.header_va:#x} reads group_count={groups} '
                    f'lang_count={langs} lang_ids={list(ids)}, expected {spec.groups}, '
                    f'{lc}, {list(range(lc))}: this is not stock {label} as {ver.file} '
                    f'describes it')
            self.groups = groups
            self.offsets_off = self.header_off + 10
        else:
            self.header_off = None
            self.groups, self.blob_size = spec.groups, spec.blob_size
            self.offsets_off = va_to_file(segs, spec.offsets_va, f'{spec.name} pack offsets', label)
        self.blob_off = (self.offsets_off + self.groups * lc * 2 if spec.header_va is not None
                         else va_to_file(segs, spec.blob_va, f'{spec.name} pack blob', label))
        self.start_off = self.header_off if self.header_off is not None else self.offsets_off
        self.limit_off = va_to_file(segs, spec.limit_va, f'{spec.name} pack limit', label)
        self.reload()

    # --- reading -----------------------------------------------------------------------------
    def reload(self):
        d, n = self.data, self.groups * self.lang_count
        if self.header_off is not None:
            self.blob_size = struct.unpack_from('<H', d, self.header_off + 8)[0]
        self.offsets = list(struct.unpack_from(f'<{n}H', d, self.offsets_off))
        if self.blob_off + self.blob_size > self.limit_off:
            raise StockStringsError(f'the {self.spec.name} blob ({self.blob_size} bytes at '
                                    f'{self.blob_off:#x}) runs into the data at '
                                    f'{self.spec.limit_va:#x}')
        self.blob = d[self.blob_off:self.blob_off + self.blob_size]
        if self.blob[:1] != b'\0':
            raise StockStringsError(f'the {self.spec.name} blob does not start with the NUL that '
                                    f'offset 0 (missing string) points at')
        used = bytearray(self.blob_size)
        for off in set(self.offsets):
            if off == 0:
                continue
            if off >= self.blob_size:
                raise StockStringsError(f'{self.spec.name}: offset {off} is past the blob '
                                        f'({self.blob_size} bytes)')
            end = self.blob.find(b'\0', off)
            if end < 0:
                raise StockStringsError(f'{self.spec.name}: the string at offset {off} is not '
                                        f'NUL-terminated inside the blob')
            for i in range(off, end + 1):
                used[i] = 1
        self.used = used
        live_end = max((i + 1 for i in range(self.blob_size) if used[i]), default=1)
        self.free_start = live_end
        pad = 0
        while (self.blob_off + self.blob_size + pad < self.limit_off
               and self.data[self.blob_off + self.blob_size + pad] == 0):
            pad += 1
        self.pad = pad                       # zero bytes between the blob end and the next live data
        self.free_end = self.blob_size + pad
        self.region_end = self.blob_off + self.free_end

    def string(self, off):
        if off == 0:
            return ''
        return bytes(self.blob[off:self.blob.find(b'\0', off)]).decode('utf-8', 'replace')

    def slot(self, group, lang):
        if not 0 <= group < self.groups:
            raise StockStringsError(f'{self.spec.name}: group {group} is out of range '
                                    f'(0..{self.groups - 1})')
        return group * self.lang_count + lang

    def group_strings(self, group):
        return [self.string(self.offsets[self.slot(group, l)]) for l in range(self.lang_count)]

    def sharers(self, off):
        """Every (group, lang) whose offset is `off` -- the slots an in-place overwrite would hit."""
        return [(i // self.lang_count, i % self.lang_count)
                for i, o in enumerate(self.offsets) if o == off]

    # --- writing -----------------------------------------------------------------------------
    def write_blob(self, blob, blob_size=None):
        """Put `blob` back into the image (and its size into the header, when there is one)."""
        size = blob_size if blob_size is not None else len(blob)
        if self.blob_off + size > self.limit_off:
            raise StockStringsError(f'{self.spec.name}: a {size}-byte blob would run into the data '
                                    f'at {self.spec.limit_va:#x}')
        self.data[self.blob_off:self.blob_off + len(blob)] = blob
        if self.header_off is not None:
            struct.pack_into('<H', self.data, self.header_off + 8, size)
        elif size != self.blob_size:
            raise StockStringsError(f'{self.spec.name} has no blob_size field: its blob cannot grow')

    def write_offsets(self, offsets):
        struct.pack_into(f'<{len(offsets)}H', self.data, self.offsets_off, *offsets)


def open_image(path):
    data = bytearray(open(path, 'rb').read())
    try:
        base = app_base(data)
    except StockDevError as e:
        raise StockStringsError(str(e))
    return data, base


def parse_pack(data, base, name):
    """One pack of the image at `base`, using the addresses of *its* version's file."""
    _base, ver = version_of(data, base)
    if name not in ver.packs and name not in PACK_NAMES:
        raise StockStringsError(f'unknown pack {name!r}; have '
                                f'{", ".join(ver.packs or PACK_NAMES)}')
    try:
        return Pack(data, base, ver.need_pack(name), ver)
    except stockver.StockVerError as e:
        raise StockStringsError(str(e)) from None
    except StockDevError as e:
        raise StockStringsError(str(e))


def read_pack(data, base, name):
    """[{'group', 'strings': [4], 'offsets': [4]}] plus the pack's own numbers, for scripts."""
    p = parse_pack(data, base, name)
    return {'pack': name, 'groups': p.groups, 'blob_size': p.blob_size, 'free': p.free_end - p.free_start,
            'entries': [{'group': g,
                         'offsets': p.offsets[g * p.lang_count:(g + 1) * p.lang_count],
                         'strings': p.group_strings(g)} for g in range(p.groups)]}


def lang_index(name, langs=LANGS):
    """`en` / `0` -> the language index. `langs` is the pack's own list of languages (the baseline
    version's four when it is left out)."""
    s = str(name).strip()
    if s.isdigit() and 0 <= int(s) < len(langs):
        return int(s)
    for i, l in enumerate(langs):
        if s.lower() == l.lower():
            return i
    raise StockStringsError(f'unknown language {name!r}; have {", ".join(langs)} '
                            f'(or 0..{len(langs) - 1})')


# ---------------------------------------------------------------- set / compact / restore
def set_string(data, base, name, group, lang, text, append=False, shared=False):
    """Write `text` into one slot of a pack. Returns a report; `data` is edited in place and its
    checksum + SHA-256 refreshed. `append` forces the appended path even when the text would fit;
    `shared` allows an in-place overwrite that changes every slot sharing the old string."""
    p = parse_pack(data, base, name)
    lang = lang_index(lang, p.langs)
    new = text.encode('utf-8')
    if b'\0' in new:
        raise StockStringsError('a label cannot contain a NUL byte')
    slot = p.slot(group, lang)
    old_off = p.offsets[slot]
    old = p.string(old_off)
    old_len = len(old.encode('utf-8')) if old_off else -1
    others = [s for s in p.sharers(old_off) if s != (group, lang)] if old_off else []
    rep = {'pack': name, 'group': group, 'lang': p.langs[lang], 'old': old, 'new': text,
           'old_offset': old_off, 'shared_with': [(g, p.langs[l]) for g, l in others]}

    fits = old_off != 0 and len(new) <= old_len and (shared or not others)
    if fits and not append:
        blob = bytearray(p.blob)
        blob[old_off:old_off + old_len + 1] = new + b'\0' * (old_len + 1 - len(new))
        p.write_blob(bytes(blob), p.blob_size)
        rep.update(mode='in-place', offset=old_off, blob_size=p.blob_size,
                   padded=old_len - len(new))
    else:
        need = len(new) + 1
        free = p.free_end - p.free_start
        if need > free:
            why = ('the text is longer than the old string' if len(new) > old_len else
                   'the old string is shared with ' + ', '.join(f'{g}/{p.langs[l]}' for g, l in others)
                   if others else 'an appended write was asked for')
            raise StockStringsError(
                f'{name}: {why}, so it has to be appended, but the pack\'s free tail is {free} '
                f'byte(s) and {need} are needed. Free space with `stockstrings.py IMAGE compact '
                f'--pack {name}`' + (' (frees 497 bytes in the stock label pack)' if name == 'labels'
                                     else '') +
                (f', or overwrite all {len(others) + 1} slots that share the string with --shared'
                 if others and len(new) <= old_len else ''))
        off = p.free_start
        if off > U16_MAX:
            raise StockStringsError(f'{name}: the free tail starts at offset {off}, past the u16 '
                                    f'ceiling {U16_MAX}')
        blob = bytearray(p.blob) + bytes(p.pad)
        blob[off:off + need] = new + b'\0'
        size = max(p.blob_size, off + need)
        p.write_blob(bytes(blob[:max(size, p.blob_size)]), size)
        offsets = list(p.offsets)
        offsets[slot] = off
        p.write_offsets(offsets)
        rep.update(mode='appended', offset=off, blob_size=size,
                   grew=size - p.blob_size, free_left=p.free_end - (off + need))

    lay = refresh_image(data, base)
    ok_c, ok_h = verify_image(data, base)
    if not (ok_c and ok_h):
        raise StockStringsError(f'the re-checksummed image does not verify (checksum_ok={ok_c}, '
                                f'hash_ok={ok_h}) -- refusing to write it')
    after = parse_pack(data, base, name)                 # re-parse: the pack must still be valid
    if after.string(after.offsets[slot]) != text:
        raise StockStringsError(f'{name}: the pack does not read back as {text!r} -- refusing')
    rep.update(checksum=data[lay['checksum_off']], checksum_ok=ok_c, hash_ok=ok_h,
               free_after=after.free_end - after.free_start)
    return rep


def compact_pack(data, base, name):
    """Rewrite a pack's blob with the same strings, merging every string that is a suffix of another
    into it, and repoint every offset. The blob keeps its size (the freed bytes become NULs at its
    end and are what `set` appends into); nothing outside the pack moves."""
    p = parse_pack(data, base, name)
    before_free = p.free_end - p.free_start
    strings = {}                                          # offset -> bytes
    for off in set(p.offsets):
        if off:
            strings[off] = bytes(p.blob[off:p.blob.find(b'\0', off)])
    uniq = sorted(set(strings.values()), key=lambda v: (-len(v), v))
    blob, placed, stored = bytearray(b'\0'), {}, 0        # blob[0] = the NUL offset 0 points at
    for v in uniq:
        hit = next((h for h in placed if h.endswith(v)), None)
        if hit is not None:                               # a suffix of a string already in the blob
            placed[v] = placed[hit] + len(hit) - len(v)
            continue
        placed[v] = len(blob)
        blob += v + b'\0'
        stored += 1
    if len(blob) > p.blob_size:
        raise StockStringsError(f'{name}: the repacked blob is {len(blob)} bytes, larger than the '
                                f'{p.blob_size} it has to fit in')
    if len(blob) - 1 > U16_MAX:
        raise StockStringsError(f'{name}: the repacked blob does not fit u16 offsets')
    offsets = [placed[strings[o]] if o else 0 for o in p.offsets]
    was = [p.string(o) for o in p.offsets]
    p.write_blob(bytes(blob) + b'\0' * (p.blob_size - len(blob)), p.blob_size)
    p.write_offsets(offsets)
    lay = refresh_image(data, base)
    ok_c, ok_h = verify_image(data, base)
    after = parse_pack(data, base, name)
    now = [after.string(o) for o in after.offsets]
    if now != was:
        bad = next(i for i, (a, b) in enumerate(zip(was, now)) if a != b)
        raise StockStringsError(f'{name}: compaction changed group {bad // p.lang_count} '
                                f'{p.langs[bad % p.lang_count]} from {was[bad]!r} to {now[bad]!r}')
    if not (ok_c and ok_h):
        raise StockStringsError(f'the re-checksummed image does not verify (checksum_ok={ok_c}, '
                                f'hash_ok={ok_h}) -- refusing to write it')
    return {'pack': name, 'strings': len(uniq), 'stored': stored,
            'blob_size': p.blob_size, 'free_before': before_free,
            'free_after': after.free_end - after.free_start,
            'freed': (after.free_end - after.free_start) - before_free,
            'checksum': data[lay['checksum_off']], 'checksum_ok': ok_c, 'hash_ok': ok_h}


def restore_pack(data, base, orig, orig_base, name):
    """Copy a pack's whole region (header + offsets + blob + the padding after it) from `orig`."""
    now = parse_pack(data, base, name)
    was = parse_pack(orig, orig_base, name)               # both must parse as stock packs
    lo, hi = was.start_off, was.region_end
    if (now.start_off, now.region_end) != (lo - orig_base + base, hi - orig_base + base):
        raise StockStringsError(f'{name}: the pack region of the two images does not line up')
    changed = bytes(data[now.start_off:now.region_end]) != bytes(orig[lo:hi])
    data[now.start_off:now.region_end] = orig[lo:hi]
    lay = refresh_image(data, base)
    ok_c, ok_h = verify_image(data, base)
    after = parse_pack(data, base, name)
    if [after.string(o) for o in after.offsets] != [was.string(o) for o in was.offsets]:
        raise StockStringsError(f'{name}: the restored pack does not read like the original')
    return {'pack': name, 'bytes': hi - lo, 'from': f'{lo:#x}', 'to': f'{now.start_off:#x}',
            'changed': changed, 'checksum': data[lay['checksum_off']], 'checksum_ok': ok_c,
            'hash_ok': ok_h}


# ---------------------------------------------------------------- CLI
def kind_of(data, base, ver=None):
    return ('16 MB flash image, app0 at 0x10000' if base else 'bare app image') + \
        f', {len(data)} bytes' + (f', {ver.label}' if ver else '')


def print_group(p, g, lang=None, prefix=''):
    ss = p.group_strings(g)
    known = p.ver.known_groups
    note = f'  [{known[g]}]' if p.spec.name == 'labels' and g in known else ''
    if lang is None:
        print(f'{prefix}{g:4}  ' + ' | '.join(f'{p.langs[i]}: {s}' for i, s in enumerate(ss)) + note)
    else:
        print(f'{prefix}{g:4}  {ss[lang]}{note}')


def write_out(path, data, out):
    dst = out or path
    with open(dst, 'wb') as f:
        f.write(data)
    return dst


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0],
                                 epilog='packs: ' + ', '.join(f'{n} ({p.groups} groups, {p.what})'
                                                              for n, p in PACKS.items()),
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('image', help='a stock app image of a known version (tools/stockver.py '
                                  'versions), or a 16 MB flash image (app0 at 0x10000)')
    sub = ap.add_subparsers(dest='cmd', required=True)

    def add_pack(p, choices=PACK_NAMES):
        p.add_argument('--pack', default='labels', choices=choices,
                       help='which string pack (default labels)')
        return p

    lp = add_pack(sub.add_parser('list', help='print every group of a pack'), PACK_NAMES + ('all',))
    lp.add_argument('--lang', help='print only this language (zh-CN, en, zh-TW, ja or 0..3)')
    lp.add_argument('--grep', help='only groups where some language contains this text (case-insensitive)')

    gp = add_pack(sub.add_parser('get', help='print one group'))
    gp.add_argument('group', type=int)
    gp.add_argument('--lang')

    sp = add_pack(sub.add_parser('set', help='relabel one group in one language'))
    sp.add_argument('group', type=int)
    sp.add_argument('lang')
    sp.add_argument('text')
    sp.add_argument('--append', action='store_true',
                    help='append into the free tail even when the text would fit in place')
    sp.add_argument('--shared', action='store_true',
                    help='allow an in-place overwrite that changes every slot sharing the string')
    sp.add_argument('-o', '--out', help='write here instead of editing IMAGE in place')

    cp = add_pack(sub.add_parser('compact', help='repack the blob (suffix merge) to free its tail'))
    cp.add_argument('-o', '--out')

    rp = sub.add_parser('restore', help='copy a pack region back from an untouched image')
    rp.add_argument('--from', dest='orig', required=True, help='the original image')
    rp.add_argument('--pack', default='all', choices=PACK_NAMES + ('all',))
    rp.add_argument('-o', '--out')

    a = ap.parse_args(argv)
    try:
        data, base = open_image(a.image)
        base, ver = version_of(data, base)          # refuses an unknown build before any read
        packs = tuple(ver.packs) if getattr(a, 'pack', None) == 'all' else (a.pack,)

        if a.cmd == 'list':
            lang = lang_index(a.lang) if a.lang else None
            for name in packs:
                p = parse_pack(data, base, name)
                print(f'{a.image}: {kind_of(data, base, ver)}\n'
                      f'{name} pack: {p.groups} groups x {p.lang_count} languages '
                      f'({", ".join(p.langs)}), blob {p.blob_size} bytes at file {p.blob_off:#x}'
                      f', free tail {p.free_end - p.free_start} bytes  -- {p.spec.what}')
                for g in range(p.groups):
                    ss = p.group_strings(g)
                    if a.grep and not any(a.grep.lower() in s.lower() for s in ss):
                        continue
                    print_group(p, g, lang, prefix='  ')
        elif a.cmd == 'get':
            p = parse_pack(data, base, a.pack)
            print_group(p, a.group, lang_index(a.lang) if a.lang else None)
        elif a.cmd == 'set':
            r = set_string(data, base, a.pack, a.group, a.lang, a.text, a.append, a.shared)
            dst = write_out(a.image, data, a.out)
            print(f'{a.image}: {kind_of(data, base, ver)}\n'
                  f'  {a.pack} group {r["group"]} {r["lang"]}: {r["old"]!r} -> {r["new"]!r} '
                  f'({r["mode"]}, offset {r["old_offset"]} -> {r["offset"]}, blob {r["blob_size"]} '
                  f'bytes, free tail {r["free_after"]})\n'
                  f'  checksum {r["checksum"]:#04x} ({"valid" if r["checksum_ok"] else "INVALID"}), '
                  f'sha-256 {"valid" if r["hash_ok"] else "INVALID"}\n'
                  f'  wrote {dst}')
            if r['shared_with']:
                print(f'  note: the old string was shared with '
                      f'{", ".join(f"{g}/{l}" for g, l in r["shared_with"])}'
                      f' ({"they keep it" if r["mode"] == "appended" else "they changed too"})')
        elif a.cmd == 'compact':
            for name in packs:
                r = compact_pack(data, base, name)
                print(f'{a.image}: {name} pack compacted: {r["strings"]} strings ({r["stored"]} stored, the rest suffixes), blob '
                      f'{r["blob_size"]} bytes, free tail {r["free_before"]} -> {r["free_after"]} '
                      f'(+{r["freed"]}), checksum {r["checksum"]:#04x} '
                      f'({"valid" if r["checksum_ok"] and r["hash_ok"] else "INVALID"})')
            print(f'  wrote {write_out(a.image, data, a.out)}')
        elif a.cmd == 'restore':
            orig, orig_base = open_image(a.orig)
            for name in packs:
                r = restore_pack(data, base, orig, orig_base, name)
                print(f'{a.image}: {name} pack restored from {a.orig} '
                      f'({r["bytes"]} bytes at {r["to"]}, '
                      f'{"changed" if r["changed"] else "was already identical"})')
            print(f'  wrote {write_out(a.image, data, a.out)}')
    except StockStringsError as e:
        print(f'{a.image}: {e}', file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
