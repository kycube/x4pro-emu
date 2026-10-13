"""The ESP32-S3 AES accelerator and the GDMA path it takes, against the `cryptography` package's AES.

Espressif's QEMU already models the AES block (hw/misc/esp_aes.c: typical mode and DMA-AES through
the GDMA, ECB/CBC/OFB/CTR/CFB8/CFB128, 128/256-bit keys). What the stock firmware tripped over on
2026-10-12 was the GDMA's reach: its address space held internal DRAM alone, and ESP-IDF 6's DMA-AES
hands it PSRAM buffers at their data-cache addresses (0x3C95D9E8 when the stock 7.6.8 saves a Lua
app's ctx.state), so the read failed, AES_STATE never reached DONE and core 1 spun on 0x6003A04C
(qemu-patches/0019). Comparing every block mode with a host AES then found two model errors, fixed
in 0020: OFB encryption left the last ciphertext block in IV memory instead of the last keystream
block, and CTR's keystream carried into bit 32 under INC_SEL 0 (INC32). These tests drive the
registers over qtest -- no guest code runs, the CPU is stopped -- and compare every result:

  * typical mode: the FIPS-197 appendix C vectors and random blocks, both key sizes, both directions;
  * DMA mode: every block mode the model offers, both key sizes, both directions, with the buffers
    in internal DRAM and in PSRAM (through an MMU page pair that is not contiguous in PSRAM, so one
    transfer crosses a page boundary), split over several descriptors; the IV/counter the block
    leaves behind is what a continuing call needs (ESP-IDF reads it back for CBC/OFB/CFB/CTR), and
    the receive descriptors come back owned by the CPU with suc_eof on the last one.
"""
import os
import random
import subprocess

import pytest
from conftest import QEMU

cryptography = pytest.importorskip('cryptography')
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes   # noqa: E402
try:                                         # CFB, CFB8 and OFB moved to `decrepit` in cryptography 47
    from cryptography.hazmat.decrepit.ciphers import modes as old_modes  # noqa: E402
except ImportError:
    old_modes = modes

AES = 0x6003A000
KEY, TEXT_IN, TEXT_OUT, MODE, TRIGGER, STATE, IV = 0x00, 0x20, 0x30, 0x40, 0x48, 0x4C, 0x50
DMA_ENABLE, BLOCK_MODE, BLOCK_NUM, INC_SEL, DMA_EXIT = 0x90, 0x94, 0x98, 0x9C, 0xB8
IDLE, DONE = 0, 2
BLK_ECB, BLK_CBC, BLK_OFB, BLK_CTR, BLK_CFB8, BLK_CFB128 = range(6)   # AES_BLOCK_MODE

GDMA = 0x6003F000
GDMA_PERI_AES = 6
CH = 2                                       # any channel; the AES finds it by PERI_SEL
IN_REGS, OUT_REGS = GDMA + 0xC0 * CH, GDMA + 0xC0 * CH + 0x60
CONF0, CONF1, LINK, PERI_SEL = 0x00, 0x04, 0x20, 0x48
OUT_AUTO_WRBACK, CHECK_OWNER = 1 << 2, 1 << 12
OUT_LINK_START, IN_LINK_START = 1 << 21, 1 << 22

MMU_TABLE = 0x600C5000
MMU_PSRAM = 1 << 15
DCACHE = 0x3C000000
PAGE = 0x10000

DESC = 0x3FCA0000                            # descriptors: internal DRAM (the 20-bit link field)
DRAM_SRC, DRAM_DST = 0x3FCA1000, 0x3FCA4000
# PSRAM: dcache pages 0x80 and 0x81 mapped to PSRAM pages 3 and 9, buffers straddling the boundary
PSRAM_SRC, PSRAM_DST = DCACHE + 0x81 * PAGE - 0x50, DCACHE + 0x81 * PAGE + 0x2000 - 0x30


class QTest:
    """qemu-system-xtensa -machine xteink-x4pro under the qtest accelerator, spoken to over stdio."""

    def __init__(self, stderr_path):
        if not os.path.exists(QEMU):
            pytest.skip(f'{QEMU} not built')
        self.err = open(stderr_path, 'wb')
        self.p = subprocess.Popen(
            [QEMU, '-machine', 'xteink-x4pro', '-m', '8M',
             '-global', 'driver=ssi_psram,property=is_octal,value=true',
             '-display', 'none', '-monitor', 'none', '-serial', 'null',
             '-accel', 'qtest', '-qtest', 'stdio', '-qtest-log', '/dev/null'],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.err, text=True, bufsize=1)

    def cmd(self, line):
        self.p.stdin.write(line + '\n')
        self.p.stdin.flush()
        while True:
            r = self.p.stdout.readline()
            if not r:
                raise AssertionError(f'qtest: QEMU exited on {line!r}')
            if r.startswith('OK') or r.startswith('FAIL') or r.startswith('ERR'):
                break
        assert r.startswith('OK'), f'qtest {line!r} -> {r.strip()}'
        return r[2:].strip()

    def wl(self, addr, val):
        self.cmd(f'writel 0x{addr:x} 0x{val:x}')

    def rl(self, addr):
        return int(self.cmd(f'readl 0x{addr:x}'), 16)

    def write(self, addr, data):
        if data:
            self.cmd(f'write 0x{addr:x} {len(data)} 0x{data.hex()}')

    def read(self, addr, n):
        return bytes.fromhex(self.cmd(f'read 0x{addr:x} {n}')[2:])

    def close(self):
        # A qtest QEMU does not exit when its stdin closes (it used to sit out the whole 10 s wait
        # here): ask it to quit, and kill it if it does not within a few seconds.
        try:
            self.p.stdin.close()
        except Exception:
            pass
        self.p.terminate()
        try:
            self.p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.p.kill()
            self.p.wait(timeout=5)
        self.err.close()


@pytest.fixture(scope='module')
def qt(tmp_path_factory):
    err = tmp_path_factory.mktemp('aes') / 'stderr.log'
    q = QTest(err)
    q.wl(MMU_TABLE + 4 * 0x80, MMU_PSRAM | 3)
    q.wl(MMU_TABLE + 4 * 0x81, MMU_PSRAM | 9)
    yield q
    q.close()
    log = err.read_text(errors='replace')
    assert '[AES]' not in log and 'GDMA' not in log, log


def host(key, block_mode, iv):
    m = {BLK_ECB: lambda: modes.ECB(), BLK_CBC: lambda: modes.CBC(iv), BLK_OFB: lambda: old_modes.OFB(iv),
         BLK_CTR: lambda: modes.CTR(iv), BLK_CFB8: lambda: old_modes.CFB8(iv),
         BLK_CFB128: lambda: old_modes.CFB(iv)}
    return Cipher(algorithms.AES(key), m[block_mode]())


def mode_reg(key, encrypt):
    return (0 if len(key) == 16 else 2) + (0 if encrypt else 4)


def set_key(q, key):
    k = key.ljust(32, b'\0')
    for i in range(8):
        q.wl(AES + KEY + 4 * i, int.from_bytes(k[4 * i:4 * i + 4], 'little'))


# ---------------------------------------------------------------- typical mode

def typical(q, key, block, encrypt):
    set_key(q, key)
    q.wl(AES + MODE, mode_reg(key, encrypt))
    q.wl(AES + DMA_ENABLE, 0)
    for i in range(4):
        q.wl(AES + TEXT_IN + 4 * i, int.from_bytes(block[4 * i:4 * i + 4], 'little'))
    q.wl(AES + TRIGGER, 1)
    assert q.rl(AES + STATE) == IDLE
    return b''.join(q.rl(AES + TEXT_OUT + 4 * i).to_bytes(4, 'little') for i in range(4))


@pytest.mark.parametrize('key, pt, ct', [
    ('000102030405060708090a0b0c0d0e0f', '00112233445566778899aabbccddeeff',
     '69c4e0d86a7b0430d8cdb78070b4c55a'),                                   # FIPS-197 C.1
    ('000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f',
     '00112233445566778899aabbccddeeff', '8ea2b7ca516745bfeafc49904b496089'),  # FIPS-197 C.3
])
def test_typical_mode_fips197(qt, key, pt, ct):
    key, pt, ct = bytes.fromhex(key), bytes.fromhex(pt), bytes.fromhex(ct)
    assert typical(qt, key, pt, True) == ct
    assert typical(qt, key, ct, False) == pt


@pytest.mark.parametrize('klen', [16, 32])
def test_typical_mode_random_blocks(qt, klen):
    rng = random.Random(klen)
    for _ in range(8):
        key, block = rng.randbytes(klen), rng.randbytes(16)
        enc = host(key, BLK_ECB, None).encryptor().update(block)
        assert typical(qt, key, block, True) == enc
        assert typical(qt, key, enc, False) == block


# ---------------------------------------------------------------- DMA mode

def desc_word(size, length, eof, owner=1):
    return size | (length << 12) | (eof << 30) | (owner << 31)


def chain(q, at, buf, sizes, fill):
    """Descriptors at `at` covering `buf` in pieces of `sizes`; `fill` sets length = size (transmit)."""
    addrs = [at + 12 * i for i in range(len(sizes))]
    off = 0
    for i, (a, n) in enumerate(zip(addrs, sizes)):
        last = i == len(sizes) - 1
        nxt = 0 if last else addrs[i + 1]
        w = desc_word(n, n if fill else 0, int(last and fill))
        q.write(a, w.to_bytes(4, 'little') + (buf + off).to_bytes(4, 'little') + nxt.to_bytes(4, 'little'))
        off += n
    return addrs


def split(total, rng, parts):
    cuts = sorted(rng.sample(range(1, total), parts - 1)) if parts > 1 and total > parts else []
    edges = [0] + cuts + [total]
    return [b - a for a, b in zip(edges, edges[1:])]


def dma(q, key, block_mode, encrypt, iv, data, src, dst, rng, inc_sel=1):
    """One DMA-AES operation the way ESP-IDF's esp_aes_dma_core.c runs it; returns (out, iv after)."""
    n = len(data)
    assert n % 16 == 0
    q.write(src, data)
    q.write(dst, b'\xee' * n)
    out_desc = chain(q, DESC, src, split(n, rng, 3), fill=True)
    in_desc = chain(q, DESC + 0x100, dst, split(n, rng, 2), fill=False)

    q.wl(OUT_REGS + PERI_SEL, GDMA_PERI_AES)
    q.wl(IN_REGS + PERI_SEL, GDMA_PERI_AES)
    q.wl(OUT_REGS + CONF0, OUT_AUTO_WRBACK)
    q.wl(OUT_REGS + CONF1, CHECK_OWNER)
    q.wl(IN_REGS + CONF1, CHECK_OWNER)
    q.wl(OUT_REGS + LINK, (out_desc[0] & 0xFFFFF) | OUT_LINK_START)
    q.wl(IN_REGS + LINK, (in_desc[0] & 0xFFFFF) | IN_LINK_START)

    set_key(q, key)
    q.wl(AES + MODE, mode_reg(key, encrypt))
    q.wl(AES + DMA_ENABLE, 1)
    q.wl(AES + BLOCK_MODE, block_mode)
    q.wl(AES + INC_SEL, inc_sel)
    for i in range(4):
        q.wl(AES + IV + 4 * i, int.from_bytes(iv[4 * i:4 * i + 4], 'little'))
    q.wl(AES + BLOCK_NUM, n // 16)
    q.wl(AES + TRIGGER, 1)
    assert q.rl(AES + STATE) == DONE, 'DMA-AES did not complete (the GDMA could not reach a buffer?)'

    out = q.read(dst, n)
    iv_after = b''.join(q.rl(AES + IV + 4 * i).to_bytes(4, 'little') for i in range(4))
    q.wl(AES + DMA_EXIT, 1)
    assert q.rl(AES + STATE) == IDLE
    q.wl(AES + DMA_ENABLE, 0)

    for i, a in enumerate(in_desc):
        w = int.from_bytes(q.read(a, 4), 'little')
        assert w >> 31 == 0, f'receive descriptor {i} still owned by the GDMA'
        assert (w >> 30) & 1 == (i == len(in_desc) - 1), f'suc_eof on receive descriptor {i}'
    for i, a in enumerate(out_desc):
        assert int.from_bytes(q.read(a, 4), 'little') >> 31 == 0, f'transmit descriptor {i} not written back'
    return out, iv_after


def expected_iv(block_mode, encrypt, iv, data, out):
    """The IV register after the operation: what the next call in the same stream starts from."""
    ct = out if encrypt else data
    if block_mode == BLK_CBC or block_mode == BLK_CFB128:
        return ct[-16:]
    if block_mode == BLK_CFB8:
        return (iv + ct)[-16:]
    if block_mode == BLK_OFB:
        return bytes(a ^ b for a, b in zip(out[-16:], data[-16:]))    # the last keystream block
    if block_mode == BLK_CTR:
        return ((int.from_bytes(iv, 'big') + len(data) // 16) % (1 << 128)).to_bytes(16, 'big')
    return None


@pytest.mark.parametrize('where', ['dram', 'psram'])
@pytest.mark.parametrize('klen', [16, 32])
@pytest.mark.parametrize('block_mode', [BLK_ECB, BLK_CBC, BLK_OFB, BLK_CTR, BLK_CFB8, BLK_CFB128],
                         ids=['ecb', 'cbc', 'ofb', 'ctr', 'cfb8', 'cfb128'])
def test_dma_mode_matches_host_aes(qt, block_mode, klen, where):
    rng = random.Random(f'{block_mode}-{klen}-{where}')
    src, dst = (DRAM_SRC, DRAM_DST) if where == 'dram' else (PSRAM_SRC, PSRAM_DST)
    for encrypt in (True, False):
        key, iv, data = rng.randbytes(klen), rng.randbytes(16), rng.randbytes(16 * rng.randint(3, 9))
        c = host(key, block_mode, iv if block_mode != BLK_ECB else None)
        want = (c.encryptor() if encrypt else c.decryptor()).update(data)
        out, iv_after = dma(qt, key, block_mode, encrypt, iv, data, src, dst, rng)
        assert out == want, f'{"enc" if encrypt else "dec"}: output differs from the host AES'
        exp = expected_iv(block_mode, encrypt, iv, data, out)
        if exp is not None:
            assert iv_after == exp, f'{"enc" if encrypt else "dec"}: IV left for the next call'


def test_dma_stream_continues_from_the_iv_it_leaves(qt):
    """ESP-IDF splits a long stream into calls and continues each from the IV read back: two DMA
    operations equal one host operation over the concatenation, in every chaining mode."""
    rng = random.Random(7)
    for block_mode in (BLK_CBC, BLK_OFB, BLK_CTR, BLK_CFB8, BLK_CFB128):
        for encrypt in (True, False):
            key, iv = rng.randbytes(32), rng.randbytes(16)
            a, b = rng.randbytes(48), rng.randbytes(80)
            c = host(key, block_mode, iv)
            want = (c.encryptor() if encrypt else c.decryptor()).update(a + b)
            out1, iv1 = dma(qt, key, block_mode, encrypt, iv, a, PSRAM_SRC, PSRAM_DST, rng)
            out2, _ = dma(qt, key, block_mode, encrypt, iv1, b, DRAM_SRC, DRAM_DST, rng)
            assert out1 + out2 == want, f'mode {block_mode} {"enc" if encrypt else "dec"}'


def test_dma_ctr_32bit_counter_wraps_without_carry(qt):
    """INC_SEL 0 (what ESP-IDF's GCM uses, and what the stock's ctx.state save runs): only the low
    32 bits of the counter block count, and they wrap without carrying into bit 32."""
    rng = random.Random(32)
    key = rng.randbytes(32)
    iv = bytes.fromhex('00112233445566778899aabb') + (0xFFFFFFFE).to_bytes(4, 'big')
    data = rng.randbytes(64)
    blocks = [iv[:12] + ((0xFFFFFFFE + i) % (1 << 32)).to_bytes(4, 'big') for i in range(4)]
    stream = host(key, BLK_ECB, None).encryptor().update(b''.join(blocks))
    want = bytes(x ^ y for x, y in zip(data, stream))
    for encrypt in (True, False):                          # the stock saves with MODE = AES-256 dec
        out, iv_after = dma(qt, key, BLK_CTR, encrypt, iv, data, PSRAM_SRC, PSRAM_DST, rng, inc_sel=0)
        assert out == want
        assert iv_after == iv[:12] + (2).to_bytes(4, 'big')
