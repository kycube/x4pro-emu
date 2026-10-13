#!/usr/bin/env python3
r"""Build, inspect and install `.xtapp` Lua app containers for the stock `xteink_app`.

  xtapp.py new NAME OUT_DIR [--display-name T] [--author A]   scaffold manifest.json + index.lua
  xtapp.py pack APP_DIR [-o OUT] [--format classic|licorice]  write APP_DIR/app.xtapp
  xtapp.py info FILE.xtapp                                    header + manifest of a container
  xtapp.py install SD.img APP_DIR [--as DIR] [--format ...]   copy the app onto a card image

Two container generations share the `.xtapp` name and the `XTAP` magic:

* **classic** (stock `xteink_app` 7.2.4-7.6.8): a plain 0x80-byte header + a plain-JSON manifest,
  no signature, no key. This is the default and everything below describes it.
* **licorice** (XTOS Licorice 261013, the device's current firmware): a mandatory-encrypted,
  device-key-wrapped package its installer accepts from All Files. `--format licorice` builds one;
  the format, the ECDH/HKDF/AES-KW key wrap and what is proven live in **docs/licorice-apps.md**.
  A package this tool builds installs through Licorice's own UI (verified in the emulator: the
  "Installed" toast, the app at `/sdcard/XTApps/<app_id>/app.xtapp`); it does not yet carry a
  runnable code section (that launch-time layer is still being reversed, docs/licorice-apps.md).

A "Lua app" is a directory on the card, `/sdcard/XTApps/<dir>/`, holding `manifest.json`,
`index.lua` (the script that runs) and `app.xtapp` (this container). The stock lists it under the
nav menu's **Lua Apps** row (7.5.4 / 7.6.8: **Mini Apps**); the page behind it is 0x09,
`AppsPagePresenter`. In 7.2.4 that row only exists once the app image carries the `lua-apps-row`
patch (`tools/stockpatch.py`); 7.5.4 and 7.6.8 show it by themselves as soon as the card holds an
app (docs/stock-7.6.8.md). The whole workflow is `docs/lua-apps.md`.

THE CONTAINER (`app.xtapp`), as the firmware reads it (7.2.4's FUN_ addresses)
    `FUN_421c37e8` enumerates `/sdcard/XTApps`, builds `%s/%.190s/app.xtapp` per directory and
    **drops any directory whose container fails to open**, which is why a plain
    `manifest.json` + `index.lua` directory never appears in the list. `FUN_420664d8` opens it:

        offset  size  value
        0x00     4    "XTAP"                       magic
        0x04     2    1                            version   (must be 1)
        0x06     2    0x80                         header size (must be 0x80, and the header read
                                                   must return exactly 0x80 bytes)
        0x0c     4    flags                        read, not checked here (0)
        0x10     4    manifest offset              must be 0x80
        0x14     4    manifest length              1..0x2000, and offset+length must fit the file
        0x30     4    (only looked at when 0x34 is non-zero)
        0x34     4    0                            **must be 0**: non-zero = encrypted/wrapped
        rest          zero
        0x80   len    the manifest, as plain UTF-8 JSON

    There is no signature and no DEK: an unwrapped, unsigned app loads (the wrapped
    `xteink.xtapp.appdek.wrap.v2` form exists in the strings but is not required). The JSON is
    written **compact** (`{"a":1}`, no spaces), which is what the reference container holds; nothing
    in the firmware depends on that, but it keeps `pack` reproducible byte for byte.

THE MANIFEST
    `app_id` and `display_name` are required (`display_name` is the row text on the Lua Apps page).
    `permissions` is the one field the firmware validates: `FUN_4206645c` accepts **only**
    `sys.battery` and `sys.status` and rejects the whole app on anything else -- a `"lockscreen"`
    entry is what made the first attempt vanish from the list. Other keys seen in the string table
    (`author`, `description`, `app_icon_s`, `app_icon_l`, `splash`, `display`, `interval_sec`,
    `preload_assets`, `lockscreen`) are carried through untouched; none is required and only
    `app_id`, `display_name`, `author`, `display` and `permissions` are known to be read.
    `entry` names the script; the host actually builds `<dir>/index.lua` (or `lockscreen.lua` on the
    lock-screen route, which does not work: docs/lua-apps.md).

EXAMPLE
    .venv/bin/python tools/xtapp.py new clock /tmp/apps/clock
    $EDITOR /tmp/apps/clock/index.lua
    .venv/bin/python tools/xtapp.py pack /tmp/apps/clock
    .venv/bin/python tools/xtapp.py install /tmp/sd.img /tmp/apps/clock   # -> /XTApps/clock/
    .venv/bin/python tools/stockpatch.py apply /tmp/flash.bin lua-apps-row  # 7.2.4 only needs it

`install` uses mtools on the FAT32 partition at 1 MiB (`tools/mksd.py`'s layout, the device's own):
it creates `/XTApps/<app_id>/`, packs a fresh `app.xtapp` (into a temporary file -- APP_DIR itself
is never written to) and copies every file of the directory, recursively, into it. Nothing else on
the card is touched.

`pack()`, `read_container()`, `validate_manifest()`, `install()` and `scaffold()` are importable;
they raise `XtappError` with a message that says which rule was broken. The Licorice helpers
(`pack_licorice()`, `build_licorice_container()`, `read_licorice_container()`, `licorice_wrap_dek()`,
`licorice_device_pubkey()`, `licorice_section_hmac()`, `validate_manifest_licorice()`) are importable
too; the key-wrap ones need the `cryptography` package (P-256 / HKDF / AES-KW).
"""
import argparse, collections, hashlib, hmac, json, os, re, shutil, struct, subprocess, sys, tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mksd import PART_START                                                    # noqa: E402

MAGIC = b'XTAP'
VERSION = 1
HEADER_SIZE = 0x80
MANIFEST_OFF = 0x80
MANIFEST_MAX = 0x2000                # FUN_420664d8: 0x1fff < len-1 is rejected, so 1..0x2000
PART_OFFSET = PART_START * 512       # tools/mksd.py puts the FAT32 partition at 1 MiB
APPS_DIR = 'XTApps'
REQUIRED = ('app_id', 'display_name')
PERMISSIONS = ('sys.battery', 'sys.status')     # FUN_4206645c accepts nothing else
ENTRY_NAMES = ('index.lua', 'lockscreen.lua')   # what the script host opens (0x4212a92a)
MAX_DIR = 190                        # the `%s/%.190s/app.xtapp` format truncates past this

# ---- XTOS Licorice (261013) mini-app package (docs/licorice-apps.md; v261013 FUN_ addresses) ----
# The installer (FUN_42108f68) requires a P256-ECDH-HKDF-SHA256-AESKW wrapped DEK in the manifest.
# The "device key" is NOT device-unique: it is the firmware scalar d at DROM LICORICE_SCALAR_VA
# (FUN_421d7118 reads 0x20 bytes, mbedtls big-endian), so any firmware reader can wrap a DEK the
# device will unwrap -- confirmed by a successful install in the emulator (the "DEK1" cache entry at
# RAM 0x3fc9e0f8 appears and the package reaches the "Installed" toast). Only the device PUBLIC key
# Q = d*G is needed to wrap; d itself stays on the device, so nothing secret is embedded here --
# `licorice_device_pubkey()` derives Q from the firmware image at build time.
LICORICE_ALG = 'P256-ECDH-HKDF-SHA256-AESKW'      # dek_wrap_alg (checked verbatim, FUN_4210a908)
LICORICE_KEY_ID = '36036010c7bd05af'              # dek_wrap_key_id (checked verbatim)
LICORICE_SCALAR_VA = 0x3c32c674                   # DROM: the ECDH scalar d (FUN_421d7118)
LICORICE_IMAGE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                              'images', 'device', 'stock-app1-v261013.bin')     # gitignored
LICORICE_WRAP_SALT = ('xteink.xtapp.wrap.' + LICORICE_KEY_ID).encode()   # HKDF-Extract salt (FUN_4210a908)
LICORICE_WRAP_INFO = b'xteink.xtapp.appdek.wrap.v2'                      # HKDF-Expand info, then a 0x01 block
LICORICE_PERMISSIONS = ('sys.battery', 'sys.status', 'net.http', 'net.https', 'storage.app',
                        'storage.user_file', 'device.status', 'device.keep_awake', 'bluetooth')
LICORICE_VERSIONS = ('1.0', '0.8', '0.7')         # FUN_42108590 accepts major 1 minor 0, or 0.7/0.8
_P256_N = 0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551


class XtappError(ValueError):
    """A manifest the firmware would reject, or a container that is not the 0x80-byte XTAP form."""


# ---------------------------------------------------------------- manifest + container
def load_manifest(path):
    """Read a manifest.json, keeping its key order (so `pack` is reproducible)."""
    try:
        raw = open(path, 'rb').read()
    except OSError as e:
        raise XtappError(f'{e}') from None
    try:
        obj = json.loads(raw.decode('utf-8'), object_pairs_hook=collections.OrderedDict)
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise XtappError(f'{path}: not valid UTF-8 JSON ({e})') from None
    if not isinstance(obj, dict):
        raise XtappError(f'{path}: the manifest must be a JSON object, not a {type(obj).__name__}')
    return obj


def validate_manifest(obj, where='manifest.json'):
    """Raise XtappError on anything the stock's loader would reject. Returns the manifest."""
    for key in REQUIRED:
        if key not in obj:
            raise XtappError(f'{where}: no "{key}" -- {" and ".join(REQUIRED)} are required '
                             f'("display_name" is the row text on the Lua Apps page)')
        if not isinstance(obj[key], str) or not obj[key].strip():
            raise XtappError(f'{where}: "{key}" must be a non-empty string, got {obj[key]!r}')
    app_id = obj['app_id']
    if '/' in app_id or '\\' in app_id or app_id in ('.', '..'):
        raise XtappError(f'{where}: "app_id" {app_id!r} is a directory name under /sdcard/XTApps: '
                         f'no slashes')
    if len(app_id.encode('utf-8')) > MAX_DIR:
        raise XtappError(f'{where}: "app_id" is {len(app_id.encode("utf-8"))} bytes; the firmware '
                         f'builds "%s/%.{MAX_DIR}s/app.xtapp" and would truncate it')
    perms = obj.get('permissions', [])
    if not isinstance(perms, list):
        raise XtappError(f'{where}: "permissions" must be a list, got {type(perms).__name__}')
    for p in perms:
        if p not in PERMISSIONS:
            raise XtappError(
                f'{where}: permission {p!r} is not allowed. The stock (FUN_4206645c) accepts only '
                f'{" and ".join(PERMISSIONS)} and rejects the whole app -- it disappears from the '
                f'Lua Apps list with no message on the console. Drop it (a "lockscreen" entry is '
                f'the usual mistake; the lock-screen route does not work anyway, docs/lua-apps.md)')
    entry = obj.get('entry')
    if entry is not None and (not isinstance(entry, str) or not entry):
        raise XtappError(f'{where}: "entry" must be a string naming the script')
    return obj


def manifest_bytes(obj):
    """The manifest as the container carries it: compact UTF-8 JSON, key order preserved."""
    blob = json.dumps(obj, separators=(',', ':'), ensure_ascii=False).encode('utf-8')
    if not 1 <= len(blob) <= MANIFEST_MAX:
        raise XtappError(f'the manifest is {len(blob)} bytes; the firmware accepts 1..{MANIFEST_MAX}')
    return blob


def build_container(obj, flags=0):
    """The 0x80-byte header + the compact manifest JSON."""
    blob = manifest_bytes(obj)
    head = bytearray(HEADER_SIZE)
    head[0:4] = MAGIC
    struct.pack_into('<HH', head, 4, VERSION, HEADER_SIZE)
    struct.pack_into('<I', head, 0x0c, flags)
    struct.pack_into('<II', head, 0x10, MANIFEST_OFF, len(blob))
    # 0x30 and 0x34 stay zero: 0x34 != 0 means an encrypted/wrapped payload and the loader bails
    return bytes(head) + blob


def read_container(data, where='app.xtapp'):
    """(header dict, manifest object) of a container, checked the way FUN_420664d8 checks it."""
    data = bytes(data)
    if len(data) < HEADER_SIZE:
        raise XtappError(f'{where}: {len(data)} bytes, shorter than the {HEADER_SIZE}-byte header')
    if data[:4] != MAGIC:
        raise XtappError(f'{where}: magic is {data[:4]!r}, expected {MAGIC!r}')
    version, header_size = struct.unpack_from('<HH', data, 4)
    flags, = struct.unpack_from('<I', data, 0x0c)
    off, length = struct.unpack_from('<II', data, 0x10)
    word30, word34 = struct.unpack_from('<II', data, 0x30)
    head = {'magic': MAGIC.decode(), 'version': version, 'header_size': header_size, 'flags': flags,
            'manifest_offset': off, 'manifest_length': length, 'word_0x30': word30,
            'word_0x34': word34, 'file_bytes': len(data)}
    if version != VERSION or header_size != HEADER_SIZE:
        raise XtappError(f'{where}: version {version} / header size {header_size:#x}, expected '
                         f'{VERSION} / {HEADER_SIZE:#x}')
    if off != MANIFEST_OFF:
        raise XtappError(f'{where}: manifest offset {off:#x}, expected {MANIFEST_OFF:#x}')
    if not 1 <= length <= MANIFEST_MAX:
        raise XtappError(f'{where}: manifest length {length}, expected 1..{MANIFEST_MAX}')
    if off + length > len(data):
        raise XtappError(f'{where}: manifest {off:#x}+{length} runs past the {len(data)}-byte file')
    if word34 != 0:
        raise XtappError(f'{where}: the word at 0x34 is {word34:#x}, not 0 -- that marks an '
                         f'encrypted/wrapped payload and the loader refuses it')
    try:
        obj = json.loads(data[off:off + length].decode('utf-8'),
                         object_pairs_hook=collections.OrderedDict)
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise XtappError(f'{where}: the manifest is not valid UTF-8 JSON ({e})') from None
    return head, obj


# ---------------------------------------------------------------- Licorice (261013) key wrap
def _crypto():
    """The `cryptography` pieces the Licorice wrap needs, or an XtappError naming the package."""
    try:
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
        from cryptography.hazmat.primitives.keywrap import aes_key_wrap, aes_key_unwrap
    except ImportError:
        raise XtappError('the Licorice format needs the `cryptography` package for P-256/HKDF/'
                         'AES-KW (pip install cryptography)') from None
    return ec, Encoding, PublicFormat, aes_key_wrap, aes_key_unwrap


def _esp_segment_read(image, va, length):
    """`length` bytes at virtual address `va` from an ESP app image (its segment table)."""
    try:
        data = open(image, 'rb').read()
    except OSError as e:
        raise XtappError(f'{e}') from None
    if len(data) < 24 or data[0] != 0xE9:
        raise XtappError(f'{image}: not an ESP app image (magic {data[:1].hex()})')
    off = 24
    for _ in range(data[1]):
        la, ln = struct.unpack_from('<II', data, off)
        if la <= va < la + ln:
            return data[off + 8 + va - la: off + 8 + va - la + length]
        off += 8 + ln
    raise XtappError(f'{image}: virtual address {va:#x} is not in any segment')


def licorice_device_pubkey(image=LICORICE_IMAGE):
    """The device ECDH public key Q = d*G, uncompressed SEC1 (65 bytes). `d` is the firmware scalar
    at LICORICE_SCALAR_VA, read big-endian (mbedtls convention; confirmed by a successful install).
    Only Q is needed to wrap a DEK, so no secret key material leaves the firmware image."""
    ec, Encoding, PublicFormat, _, _ = _crypto()
    d = int.from_bytes(_esp_segment_read(image, LICORICE_SCALAR_VA, 32), 'big') % _P256_N
    pub = ec.derive_private_key(d, ec.SECP256R1()).public_key()
    return pub.public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)


def licorice_kek(shared_x):
    """The 16-byte AES key-wrap KEK from an ECDH shared X (FUN_4210a908): HKDF-SHA256 built from two
    HMACs -- extract with the wrap-salt string, expand with the appdek-wrap info and a 0x01 block."""
    prk = hmac.new(LICORICE_WRAP_SALT, bytes(shared_x), hashlib.sha256).digest()
    okm = hmac.new(prk, LICORICE_WRAP_INFO + b'\x01', hashlib.sha256).digest()
    return okm[:16]


def licorice_wrap_dek(pubkey, dek=None):
    """Wrap a 16-byte DEK for the device public key `pubkey` (from `licorice_device_pubkey`). Picks an
    ephemeral keypair E, derives shared = (e*Q).x, and AES-KW-wraps the DEK under `licorice_kek`.
    Returns (dek, ephemeral_pub[65], wrapped_dek_pack[24]) -- the manifest carries the last two hex."""
    ec, Encoding, PublicFormat, aes_key_wrap, _ = _crypto()
    dek = bytes(dek) if dek is not None else os.urandom(16)
    if len(dek) != 16:
        raise XtappError(f'the DEK must be 16 bytes (AES-128), got {len(dek)}')
    Q = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), bytes(pubkey))
    e = ec.generate_private_key(ec.SECP256R1())
    E = e.public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
    return dek, E, aes_key_wrap(licorice_kek(e.exchange(ec.ECDH(), Q)), dek)


def licorice_unwrap_dek(scalar, ephemeral_pub, wrapped):
    """The device side (FUN_4210a908), for round-trip tests: recover the DEK from the private scalar
    `d`, the ephemeral public key and the wrapped pack. `scalar` is an int (big-endian d)."""
    ec, _, _, _, aes_key_unwrap = _crypto()
    d = ec.derive_private_key(int(scalar) % _P256_N, ec.SECP256R1())
    E = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), bytes(ephemeral_pub))
    return aes_key_unwrap(licorice_kek(d.exchange(ec.ECDH(), E)), bytes(wrapped))


def licorice_section_hmac(dek, data):
    """A section's integrity tag = HMAC-SHA256(DEK, section bytes) (FUN_4210a630), what the manifest
    carries as hmac_code / hmac_assets / hmac_data / hmac_fonts."""
    return hmac.new(bytes(dek), bytes(data), hashlib.sha256).digest()


def validate_manifest_licorice(obj, where='manifest.json'):
    """Raise XtappError on anything Licorice's loader (FUN_42108648 / FUN_42108590) would reject.
    Stricter than classic: an ASCII-letter app_id, a known version, and a wider permission set."""
    for key in REQUIRED:
        if key not in obj:
            raise XtappError(f'{where}: no "{key}" -- {" and ".join(REQUIRED)} are required')
        if not isinstance(obj[key], str) or not obj[key].strip():
            raise XtappError(f'{where}: "{key}" must be a non-empty string, got {obj[key]!r}')
    app_id = obj['app_id']
    if not re.fullmatch(r'[A-Za-z]+', app_id):
        raise XtappError(
            f'{where}: Licorice\'s manifest validator (FUN_42108590) walks the app_id and rejects a '
            f'non-ASCII-letter character; {app_id!r} is not all letters (a hyphen or digit fails). '
            f'"probe" installs; "gull-rock" (hyphen) does not. Use an all-letter app_id')
    if len(app_id.encode('utf-8')) > MAX_DIR:
        raise XtappError(f'{where}: "app_id" is too long for /sdcard/XTApps/<app_id>')
    ver = obj.get('version', '0.8')
    if ver not in LICORICE_VERSIONS:
        raise XtappError(f'{where}: "version" {ver!r} -- Licorice accepts {LICORICE_VERSIONS} '
                         f'(FUN_42108590 parses "%d.%d" and wants 1.0, 0.8 or 0.7)')
    perms = obj.get('permissions', [])
    if not isinstance(perms, list):
        raise XtappError(f'{where}: "permissions" must be a list, got {type(perms).__name__}')
    for p in perms:
        if p not in LICORICE_PERMISSIONS:
            raise XtappError(f'{where}: permission {p!r} is not one Licorice knows '
                             f'({", ".join(LICORICE_PERMISSIONS)}); an unknown one sets the reject bit')
    for field in ('dek_wrap_alg', 'dek_wrap_key_id', 'dek_wrap_ephemeral_pub', 'wrapped_dek_pack'):
        if field in obj:
            raise XtappError(f'{where}: drop {field!r} -- `pack --format licorice` wraps a fresh DEK '
                             f'and writes the dek_wrap_* fields itself')
    return obj


def build_licorice_container(manifest_obj, pubkey, dek=None):
    """A manifest-only Licorice container (feature mask 0: no encrypted code/assets/data/fonts yet).
    Wraps a DEK for `pubkey`, writes the dek_wrap_* fields into the manifest, and lays out the 0x80
    header the installer parses (FUN_42108380). Returns (bytes, dek). The header, confirmed by a live
    install: XTAP magic, version u16 = 1, header-size u16 = 0x80, total-size u32 at 0x08, feature
    mask u32 at 0x0c, manifest offset/length at 0x10/0x14."""
    obj = collections.OrderedDict(manifest_obj)
    dek, E, wrapped = licorice_wrap_dek(pubkey, dek)
    obj['dek_wrap_alg'] = LICORICE_ALG
    obj['dek_wrap_key_id'] = LICORICE_KEY_ID
    obj['dek_wrap_ephemeral_pub'] = E.hex()
    obj['wrapped_dek_pack'] = wrapped.hex()
    blob = json.dumps(obj, separators=(',', ':'), ensure_ascii=False).encode('utf-8')
    if not 1 <= len(blob) <= MANIFEST_MAX:
        raise XtappError(f'the manifest is {len(blob)} bytes; the firmware accepts 1..{MANIFEST_MAX}')
    total = HEADER_SIZE + len(blob)
    head = bytearray(HEADER_SIZE)
    head[0:4] = MAGIC
    struct.pack_into('<HH', head, 4, VERSION, HEADER_SIZE)        # version 1, header size 0x80
    struct.pack_into('<I', head, 0x08, total)                    # total/payload size (FUN_42108380 bound)
    struct.pack_into('<I', head, 0x0c, 0)                        # feature mask: no encrypted sections
    struct.pack_into('<II', head, 0x10, MANIFEST_OFF, len(blob))  # manifest offset + length
    return bytes(head) + blob, dek


def read_licorice_container(data, where='app.xtapp'):
    """(header dict, manifest object) of a Licorice container, the way FUN_42108380 reads the header.
    Does not require the classic word-0x34 == 0 (that offset is a section descriptor here)."""
    data = bytes(data)
    if len(data) < HEADER_SIZE:
        raise XtappError(f'{where}: {len(data)} bytes, shorter than the {HEADER_SIZE}-byte header')
    if data[:4] != MAGIC:
        raise XtappError(f'{where}: magic is {data[:4]!r}, expected {MAGIC!r}')
    version, header_size = struct.unpack_from('<HH', data, 4)
    total, feature_mask = struct.unpack_from('<II', data, 8)
    off, length = struct.unpack_from('<II', data, 0x10)
    head = {'magic': MAGIC.decode(), 'version': version, 'header_size': header_size,
            'total_size': total, 'feature_mask': feature_mask, 'manifest_offset': off,
            'manifest_length': length, 'file_bytes': len(data)}
    if version != VERSION or header_size != HEADER_SIZE:
        raise XtappError(f'{where}: version {version} / header size {header_size:#x}, expected '
                         f'{VERSION} / {HEADER_SIZE:#x}')
    if off != MANIFEST_OFF:
        raise XtappError(f'{where}: manifest offset {off:#x}, expected {MANIFEST_OFF:#x}')
    if not 1 <= length <= MANIFEST_MAX or off + length > len(data):
        raise XtappError(f'{where}: manifest {off:#x}+{length} does not fit the {len(data)}-byte file')
    try:
        obj = json.loads(data[off:off + length].decode('utf-8'),
                         object_pairs_hook=collections.OrderedDict)
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise XtappError(f'{where}: the manifest is not valid UTF-8 JSON ({e})') from None
    head['has_dek_wrap'] = all(k in obj for k in
                               ('dek_wrap_alg', 'dek_wrap_key_id', 'dek_wrap_ephemeral_pub',
                                'wrapped_dek_pack'))
    return head, obj


def is_licorice_container(data):
    """True when a container carries the dek_wrap_* fields (the Licorice form), via its own reader."""
    try:
        head, _ = read_licorice_container(data)
        return head['has_dek_wrap']
    except XtappError:
        return False


def pack_licorice(app_dir, out=None, image=LICORICE_IMAGE):
    """Write a Licorice `app.xtapp` (or `out`) from `<app_dir>/manifest.json`, wrapping a fresh DEK
    for the device key derived from `image`. The package is manifest-only (installable, not yet
    runnable). Returns a report. `app_dir` is only read."""
    mpath = os.path.join(app_dir, 'manifest.json')
    if not os.path.exists(mpath):
        raise XtappError(f'{mpath} not found: a Lua app directory holds manifest.json')
    obj = validate_manifest_licorice(load_manifest(mpath), mpath)
    if not os.path.exists(image):
        raise XtappError(f'{image} not found: the Licorice device key is read from the stock app '
                         f'image (gitignored under images/). Pass --image to point at it')
    data, dek = build_licorice_container(obj, licorice_device_pubkey(image))
    dst = out or os.path.join(app_dir, 'app.xtapp')
    with open(dst, 'wb') as f:
        f.write(data)
    return {'out': dst, 'bytes': len(data), 'manifest_bytes': len(data) - HEADER_SIZE,
            'app_id': obj['app_id'], 'display_name': obj['display_name'],
            'permissions': list(obj.get('permissions', [])), 'dek': dek.hex(),
            'note': 'manifest-only: installs through Licorice (the "Installed" toast), lands at '
                    '/sdcard/XTApps/<app_id>/app.xtapp; no runnable code section yet '
                    '(docs/licorice-apps.md)'}


# ---------------------------------------------------------------- the commands
def pack(app_dir, out=None):
    """Write `<app_dir>/app.xtapp` (or `out`) from `<app_dir>/manifest.json`. Returns a report."""
    mpath = os.path.join(app_dir, 'manifest.json')
    if not os.path.exists(mpath):
        raise XtappError(f'{mpath} not found: a Lua app directory holds manifest.json, index.lua '
                         f'and the app.xtapp built from them (tools/xtapp.py new NAME DIR)')
    obj = validate_manifest(load_manifest(mpath), mpath)
    data = build_container(obj)
    dst = out or os.path.join(app_dir, 'app.xtapp')
    with open(dst, 'wb') as f:
        f.write(data)
    entry = obj.get('entry', 'index.lua')
    warnings = []
    if not any(os.path.exists(os.path.join(app_dir, n)) for n in (entry,) + ENTRY_NAMES):
        warnings.append(f'no {entry} in {app_dir}: the app will list but run nothing')
    if os.path.basename(os.path.abspath(app_dir)) != obj['app_id']:
        warnings.append(f'the directory is {os.path.basename(os.path.abspath(app_dir))!r} but '
                        f'app_id is {obj["app_id"]!r}; `install` uses app_id for the card directory')
    return {'out': dst, 'bytes': len(data), 'manifest_bytes': len(data) - HEADER_SIZE,
            'app_id': obj['app_id'], 'display_name': obj['display_name'],
            'permissions': list(obj.get('permissions', [])), 'warnings': warnings}


def mtools(img, *args, check=True):
    """One mtools command (`mcopy`, `mmd`, `mdir`, ...) on the card image's FAT32 partition
    (also tools/xtfont.py's)."""
    return subprocess.run([args[0], '-i', f'{img}@@{PART_OFFSET}', *args[1:]],
                          capture_output=True, text=True, check=check)


def install(img, app_dir, as_dir=None):
    """Copy `app_dir` into `/XTApps/<app_id>/` on a card image, with an `app.xtapp` packed fresh from
    its manifest. `app_dir` is only read: the container is built in a temporary directory, so an app
    kept under version control does not gain a generated file."""
    if not os.path.isdir(app_dir):
        raise XtappError(f'{app_dir} is not a directory')
    if not os.path.exists(img):
        raise XtappError(f'{img} not found (tools/mksd.py builds a card image)')
    if not shutil.which('mcopy'):
        raise XtappError('mcopy not found (brew install mtools / apt install mtools)')
    with tempfile.TemporaryDirectory() as tmp:                  # the container is built outside
        rep = pack(app_dir, os.path.join(tmp, 'app.xtapp'))     # app_dir: nothing is written there
        name = as_dir or rep['app_id']
        mtools(img, 'mmd', f'::/{APPS_DIR}', check=False)       # already there: mmd fails, fine
        mtools(img, 'mmd', f'::/{APPS_DIR}/{name}', check=False)
        files = sorted(n for n in os.listdir(app_dir) if not n.startswith('.')
                       and n != 'app.xtapp' and os.path.isfile(os.path.join(app_dir, n)))
        mtools(img, 'mcopy', '-o', rep['out'], *[os.path.join(app_dir, n) for n in files],
               f'::/{APPS_DIR}/{name}/')
    files = sorted(files + ['app.xtapp'])
    for sub in sorted(n for n in os.listdir(app_dir) if os.path.isdir(os.path.join(app_dir, n))
                      and not n.startswith('.')):
        mtools(img, 'mcopy', '-o', '-s', os.path.join(app_dir, sub), f'::/{APPS_DIR}/{name}/')
        files.append(sub + '/')
    listing = mtools(img, 'mdir', '-b', f'::/{APPS_DIR}/{name}', check=False).stdout
    rep.update({'image': img, 'card_dir': f'/{APPS_DIR}/{name}', 'files': files,
                'listing': [ln.strip() for ln in listing.splitlines() if ln.strip()]})
    if not any(ln.lower().endswith('app.xtapp') for ln in rep['listing']):
        raise XtappError(f'{img}: app.xtapp is not in /{APPS_DIR}/{name} after the copy '
                         f'(mtools said: {listing!r})')
    return rep


def install_licorice(img, app_dir, image=LICORICE_IMAGE, as_name=None):
    """Put a Licorice `.xtapp` package onto a card so Licorice can install it from All Files. Unlike
    classic install, this does NOT drop a folder in /XTApps -- Licorice ignores that; it copies the
    built package to the card root as `<app_id>.xtapp`, where it shows up in All Files. The user (or
    `tests/test_licorice_apps.py`) then taps it and confirms "Install this package?"."""
    if not os.path.isdir(app_dir):
        raise XtappError(f'{app_dir} is not a directory')
    if not os.path.exists(img):
        raise XtappError(f'{img} not found (tools/mksd.py builds a card image)')
    if not shutil.which('mcopy'):
        raise XtappError('mcopy not found (brew install mtools / apt install mtools)')
    with tempfile.TemporaryDirectory() as tmp:
        rep = pack_licorice(app_dir, os.path.join(tmp, 'app.xtapp'), image)
        name = (as_name or rep['app_id']) + '.xtapp'
        mtools(img, 'mcopy', '-o', rep['out'], f'::/{name}')
    listing = mtools(img, 'mdir', '-b', '::/', check=False).stdout
    if not any(ln.strip().lower().endswith(name.lower()) for ln in listing.splitlines()):
        raise XtappError(f'{img}: {name} is not on the card root after the copy')
    rep.update({'image': img, 'card_path': f'/{name}'})
    return rep


TEMPLATE = '''\
-- {display_name} -- a Lua screen for the stock xteink_app.
--
-- Lives on the card as /sdcard/XTApps/{app_id}/index.lua, next to manifest.json and the app.xtapp
-- container that `tools/xtapp.py pack` builds. It shows up under the nav menu's "Mini Apps" row,
-- which 7.5.4 and 7.6.8 show by themselves once the card holds an app; 7.2.4 calls it "Lua Apps"
-- and needs an app image patched with `tools/stockpatch.py apply IMAGE lua-apps-row`.
--
-- EVERYTHING BELOW MARKED "confirmed" WAS SEEN RUNNING IN THE EMULATOR, on 7.2.4 (docs/lua-apps.md).
--
-- Lua 5.5, one sandboxed state per app. The globals it has: _G, _VERSION, assert, coroutine, error,
-- getmetatable, ipairs, math, next, pairs, pcall, rawequal, rawget, rawlen, rawset, require, select,
-- setmetatable, string, table, tonumber, tostring, type, utf8, warn, xpcall -- and `g` and `ctx`.
-- There is NO `print` output: nothing you print reaches the console. Use ctx.log.info.
--
-- CALLBACKS (define them as globals; all confirmed to fire)
--   on_load()        once, after the script is loaded
--   on_enter()       when the page becomes visible
--   on_draw()        paint here, using `g`; called whenever the page is drawn
--   on_tick()        periodic (ctx.set_tick_rate(ms) asks for a rate)
--   on_input(ev)     a touch; `ev` is a table (fields not fully mapped -- print them with ctx.log)
--   on_leave(), on_unload()   the other end of the lifecycle (registered; not exercised)
--
-- DRAWING -- the global `g` (confirmed: methods called with a colon)
--   g:clear(0)                blank the WHOLE frame to white -- status bar and footer included
--   g:clear()                 ... with no argument it paints the frame BLACK
--   (no clear at all leaves the page you were started from on screen, and you paint over it)
--   g:rect(x, y, w, h)        rectangle outline  g:line(x1, y1, x2, y2)
--   g:circle(x, y, r)         circle outline     g:text(x, y, "string")
--   g:image(...)              image blit         g:layer(...)   layer select
--   g:size()                  the screen size (returns 480 first: the portrait width)
--   Coordinates are the portrait frame, 480 wide x 800 tall, origin top left, 1 bpp.
--   g:image / g:layer exist but their arguments are NOT established.
--
-- SERVICES -- the global `ctx` (confirmed present)
--   ctx.invalidate()          mark the page dirty -> on_draw runs again
--   ctx.request_refresh()     ask the display pipeline for a refresh
--   ctx.set_tick_rate(ms)     how often on_tick fires
--   ctx.save()                persist the app's data
--   ctx.quit()                leave the app
--   ctx.display.full_refresh() / .defer_auto_full() / .flush_once()
--   ctx.log.info(msg) / .warn(msg) / .error(msg) -- all three exist and all three are SILENT in
--                            7.2.4: they return without error and reach neither console nor card.
--                            Draw on screen if you need to see something.
--   ctx.sys.millis(), ctx.sys.battery(), ... (uptime_ms, epoch_sec, clock, charging, power,
--                            status, radio, network, has_key, has_bt_keyboard, has_bt_gamepad)
--   ctx.system.set_as_lockscreen_app(), ctx.system.quit()
--   ctx.screen, ctx.input, ctx.state, ctx.assets, ctx.data, ctx.fonts, ctx.i18n, ctx.layers,
--   ctx.lock, ctx.perf, ctx.gc -- present as tables; their members are NOT established.
--   Call them as ctx.log.info("x") (a dot, not a colon) -- ctx.* are plain function tables.

local taps = 0

function on_load()
  ctx.log.info('{app_id}: on_load')
end

function on_enter()
  ctx.log.info('{app_id}: on_enter')
end

function on_input(ev)
  taps = taps + 1
  ctx.invalidate()          -- ask for a repaint; on_draw follows
  return true               -- claim the event
end

function on_draw()
  g:clear(0)                -- white; g:clear() with no argument paints the frame black
  g:rect(20, 60, 440, 120)
  g:text(40, 110, '{display_name}')
  g:text(40, 150, 'taps: ' .. taps)
  g:line(20, 200, 460, 200)
  g:circle(240, 320, 90)
end
'''

MANIFEST_TEMPLATE = collections.OrderedDict((
    ('app_id', None), ('display_name', None), ('version', '1.0'), ('author', None),
    ('entry', 'index.lua'), ('permissions', list(PERMISSIONS))))


def scaffold(name, out_dir, display_name=None, author='x4pro-emu'):
    """Write a minimal manifest.json + an index.lua template into `out_dir`. Returns a report."""
    obj = collections.OrderedDict(MANIFEST_TEMPLATE)
    obj['app_id'] = name
    obj['display_name'] = display_name or name.replace('-', ' ').replace('_', ' ').title()
    obj['author'] = author
    validate_manifest(obj, 'the scaffold')
    os.makedirs(out_dir, exist_ok=True)
    written = []
    for fname, text in (('manifest.json', json.dumps(obj, indent=2, ensure_ascii=False) + '\n'),
                        ('index.lua', TEMPLATE.format(app_id=obj['app_id'],
                                                      display_name=obj['display_name']))):
        path = os.path.join(out_dir, fname)
        if os.path.exists(path):
            raise XtappError(f'{path} exists; scaffold into an empty directory')
        with open(path, 'w') as f:
            f.write(text)
        written.append(path)
    rep = pack(out_dir)
    rep['written'] = written + [rep['out']]
    return rep


# ---------------------------------------------------------------- CLI
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)

    p = sub.add_parser('new', help='scaffold a Lua app directory')
    p.add_argument('name', help='the app_id (also the directory name on the card)')
    p.add_argument('out_dir')
    p.add_argument('--display-name', help='the row text on the Lua Apps page (default: NAME, titled)')
    p.add_argument('--author', default='x4pro-emu')

    p = sub.add_parser('pack', help='build APP_DIR/app.xtapp from APP_DIR/manifest.json')
    p.add_argument('app_dir')
    p.add_argument('-o', '--out', help='write the container here instead of APP_DIR/app.xtapp')
    p.add_argument('--format', choices=('classic', 'licorice'), default='classic',
                   help='classic (7.x, default) or licorice (XTOS 261013, key-wrapped)')
    p.add_argument('--image', default=LICORICE_IMAGE,
                   help='licorice: the stock app image the device key is read from')

    p = sub.add_parser('info', help='print the header and manifest of an .xtapp')
    p.add_argument('file')
    p.add_argument('--json', action='store_true', help='one JSON object instead of the report')

    p = sub.add_parser('install', help='copy an app directory onto a card image')
    p.add_argument('image', help='an SD card image (tools/mksd.py: FAT32 partition at 1 MiB)')
    p.add_argument('app_dir')
    p.add_argument('--as', dest='as_dir', help='card directory name (default: the manifest app_id)')
    p.add_argument('--format', choices=('classic', 'licorice'), default='classic',
                   help='classic (-> /XTApps/<id>/) or licorice (-> card root <id>.xtapp)')
    p.add_argument('--key-image', dest='key_image', default=LICORICE_IMAGE,
                   help='licorice: the stock app image the device key is read from')

    a = ap.parse_args(argv)
    try:
        if a.cmd == 'new':
            rep = scaffold(a.name, a.out_dir, a.display_name, a.author)
            print(f'{a.out_dir}: {rep["display_name"]} ({rep["app_id"]})')
            for w in rep['written']:
                print(f'  wrote {w}')
            print(f'  next: edit index.lua, then `xtapp.py pack {a.out_dir}` and '
                  f'`xtapp.py install SD.img {a.out_dir}` (docs/lua-apps.md)')
        elif a.cmd == 'pack':
            if a.format == 'licorice':
                rep = pack_licorice(a.app_dir, a.out, a.image)
                print(f'{rep["out"]}: {rep["bytes"]} bytes = {HEADER_SIZE:#x} header + '
                      f'{rep["manifest_bytes"]} manifest (licorice, key-wrapped)\n'
                      f'  app_id {rep["app_id"]!r}, display_name {rep["display_name"]!r}, '
                      f'permissions {rep["permissions"] or "none"}\n  note: {rep["note"]}')
            else:
                rep = pack(a.app_dir, a.out)
                print(f'{rep["out"]}: {rep["bytes"]} bytes = {HEADER_SIZE:#x} header + '
                      f'{rep["manifest_bytes"]} manifest\n'
                      f'  app_id {rep["app_id"]!r}, display_name {rep["display_name"]!r}, '
                      f'permissions {rep["permissions"] or "none"}')
                for w in rep['warnings']:
                    print(f'  warning: {w}')
        elif a.cmd == 'info':
            raw = open(a.file, 'rb').read()
            licorice = is_licorice_container(raw)
            reader = read_licorice_container if licorice else read_container
            head, obj = reader(raw, a.file)
            if a.json:
                print(json.dumps({'format': 'licorice' if licorice else 'classic',
                                  'header': head, 'manifest': obj}, indent=2, ensure_ascii=False))
            else:
                keys = (('magic', 'version', 'header_size', 'total_size', 'feature_mask',
                         'manifest_offset', 'manifest_length') if licorice else
                        ('magic', 'version', 'header_size', 'flags', 'manifest_offset',
                         'manifest_length', 'word_0x30', 'word_0x34'))
                print(f'{a.file}: {head["file_bytes"]} bytes '
                      f'({"licorice, key-wrapped" if licorice else "classic"})')
                for k in keys:
                    v = head[k]
                    print(f'  {k:<16} {v if isinstance(v, str) else f"{v:#x} ({v})"}')
                print('  manifest:')
                for line in json.dumps(obj, indent=2, ensure_ascii=False).splitlines():
                    print(f'    {line}')
                try:
                    (validate_manifest_licorice if licorice else validate_manifest)(obj, a.file)
                except XtappError as e:
                    print(f'  warning: {e}')
        elif a.cmd == 'install' and a.format == 'licorice':
            rep = install_licorice(a.image, a.app_dir, a.key_image)
            print(f'{a.image}: {rep["card_path"]} <- {a.app_dir} (licorice)\n'
                  f'  {rep["display_name"]!r} ({rep["app_id"]}); install it from All Files on the '
                  f'device.\n  note: {rep["note"]}')
        else:
            rep = install(a.image, a.app_dir, a.as_dir)
            print(f'{a.image}: {rep["card_dir"]}/ <- {a.app_dir}\n'
                  f'  {rep["display_name"]!r} ({rep["app_id"]}), '
                  f'{", ".join(rep["files"])}\n'
                  f'  on the card: {", ".join(rep["listing"])}')
            for w in rep['warnings']:
                print(f'  warning: {w}')
    except XtappError as e:
        print(f'{e}', file=sys.stderr)
        return 2
    except OSError as e:
        print(f'{e}', file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
