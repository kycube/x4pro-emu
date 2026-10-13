"""The two emulated cores and the order in which each sees the other's stores (docs/smp.md).

QEMU runs the ESP32-S3's two LX7 cores as two host threads (MTTCG). Until qemu-patches' "TCG memory
order TSO" the Xtensa target told TCG that guest loads and stores need no ordering, so on a weakly
ordered host (arm64, the desk Mac) two plain stores of one core could reach the other core in either
order. ESP-IDF's spinlocks depend on that order (a plain store releases them), and the stock firmware
aborted in about half of its boots under load: `assert failed: 0x40384078 <cached disabled>:141`,
spinlock.h:141 `lock->owner == core_owner_id`, both cores holding one queue's PSRAM spinlock.

`test_cores_see_stores_in_program_order` (a few seconds, no device files) is the precise check: a
message-passing litmus, run by the ROM as the "second-stage bootloader" at flash offset 0. The PRO
CPU stores DATA = i and then FLAG = i with no MEMW between them; the APP CPU (started through
SYSTEM_CORE_1_CONTROL_1, the boot address its ROM polls) waits for FLAG == i, MEMW, reads DATA and
counts DATA != i. A compare-and-set on a word in FLAG's line before each round gives the writer that
line, the shape of ESP-IDF's external-RAM compare-and-set. Before the fix the APP CPU saw the new flag
with the old data 78..132 times per million rounds on an M4 Pro; after it, never. On an x86 host the
host's own TSO order hides the bug, so the test passes there either way. The program is compiled with
the pioarduino toolchain (`make build` installs it; the test skips without it) and packed by
`boot_image`, whose output is byte-identical to `esptool elf2image` for this ELF.

`test_stock_boots_without_spinlock_asserts` is the symptom: the owner's stock 7.6.8 dump (redacted
copies) booted on four instances at once, three times each; no console may print "assert failed".
About 35 s and opt-in: `X4EMU_SMP_STRESS=1` (boots per instance: `X4EMU_SMP_BOOTS`).
Without the fix six instances x eight boots asserted 26 times in 48 boots.
"""
import glob, hashlib, os, re, shutil, struct, subprocess, threading, time
import pytest
from conftest import x4emu, ROOT, PY, instance_name

N_ITER = 1000000

LITMUS_C = r'''
#include <stdint.h>
#define N_ITER %(n)du
#define LINE   256u                        /* the words sit in different host cache lines */
typedef int (*printf_t)(const char *fmt, ...);
#define ets_printf ((printf_t)0x4004423c)  /* ROM */
#define REG(a) (*(volatile uint32_t *)(a))
#define SYSTEM_CORE_1_CONTROL_1 0x600C0004u
#define W(n) ((volatile uint32_t *)(0x3FC90000u + (n)))
#define DATA    W(0)
#define FLAG    W(LINE)
#define LOCK    W(LINE + 4)                /* in FLAG's line */
#define ACK     W(2 * LINE)
#define VIOL    W(3 * LINE)
#define DONE    W(4 * LINE)
#define STARTED W(5 * LINE)
static inline void memw(void) { __asm__ __volatile__("memw" ::: "memory"); }
static inline uint32_t cas(volatile uint32_t *a, uint32_t cmp, uint32_t nv)
{
    __asm__ __volatile__("wsr %%2, scompare1\n s32c1i %%0, %%1, 0" : "+r"(nv) : "r"(a), "r"(cmp) : "memory");
    return nv;
}
static void wdt_off(void)
{
    REG(0x6001F064) = 0x50D83AA1; REG(0x6001F048) = 0; REG(0x6001F064) = 0;
    REG(0x60020064) = 0x50D83AA1; REG(0x60020048) = 0; REG(0x60020064) = 0;
    REG(0x600080B0) = 0x50D83AA1; REG(0x60008098) = 0; REG(0x600080B0) = 0;
    REG(0x600080B8) = 0x8F1D312A; REG(0x600080B4) |= 1u << 30; REG(0x600080B8) = 0;
}
void __attribute__((noreturn)) app_main_cpu1(void)
{
    uint32_t viol = 0;
    *STARTED = 1;
    for (uint32_t i = 1; i <= N_ITER; i++) {
        while (*FLAG != i)
            memw();
        memw();
        if (*DATA != i)
            viol++;
        memw();
        *ACK = i;
    }
    *VIOL = viol;
    memw();
    *DONE = 1;
    for (;;)
        __asm__ __volatile__("waiti 0");
}
void __attribute__((noreturn)) call_start_cpu0(void)
{
    wdt_off();
    *DATA = 0; *FLAG = 0; *LOCK = 0; *ACK = 0; *VIOL = 0; *DONE = 0; *STARTED = 0;
    memw();
    ets_printf("\nMP litmus: start, %%u iterations\n", N_ITER);
    REG(SYSTEM_CORE_1_CONTROL_1) = (uint32_t)&app_main_cpu1;
    while (!*STARTED)
        memw();
    for (uint32_t i = 1; i <= N_ITER; i++) {
        cas(LOCK, 0, 1);
        *DATA = i;        /* two plain stores, no MEMW between them */
        *FLAG = i;
        *LOCK = 0;
        while (*ACK != i)
            memw();
    }
    while (!*DONE)
        memw();
    ets_printf("MP litmus: %%u iterations, %%u violations\n", N_ITER, *VIOL);
    for (;;)
        __asm__ __volatile__("waiti 0");
}
'''

# where the stock's own second-stage bootloader loads (console: load:0x3fce2820 / load:0x403c8700)
LITMUS_LD = '''ENTRY(call_start_cpu0)
MEMORY { iram (RWX) : org = 0x403C8700, len = 0x4000
         dram (RW)  : org = 0x3FCE2820, len = 0x2000 }
SECTIONS { .text : { *(.literal .text .literal.* .text.*) } > iram
           .rodata : { *(.rodata .rodata.* .data .data.* .sdata .sdata.*) } > dram
           .bss (NOLOAD) : { *(.bss .bss.* .sbss .sbss.* COMMON) } > dram }
'''


def toolchain():
    """(gcc, dynconfig) of the pioarduino Xtensa toolchain, or None."""
    for gcc in sorted(glob.glob(os.path.expanduser('~/.platformio/packages/toolchain-xtensa-esp-elf*/bin/xtensa-esp-elf-gcc'))):
        lib = glob.glob(os.path.join(os.path.dirname(os.path.dirname(gcc)), 'lib', 'xtensa_esp32s3.*'))
        if lib:
            return gcc, lib[0]
    return None


def elf_sections(elf):
    """Entry point and the allocated PROGBITS sections of a 32-bit LE ELF, adjacent ones merged."""
    assert elf[:4] == b'\x7fELF' and elf[4] == 1 and elf[5] == 1
    entry, = struct.unpack_from('<I', elf, 24)
    shoff, = struct.unpack_from('<I', elf, 32)
    shentsize, shnum = struct.unpack_from('<HH', elf, 46)
    secs = []
    for i in range(shnum):
        _, sh_type, flags, addr, off, size = struct.unpack_from('<IIIIII', elf, shoff + i * shentsize)
        if sh_type == 1 and flags & 2 and size:        # SHT_PROGBITS, SHF_ALLOC
            secs.append((addr, elf[off:off + size]))
    segs = []
    for addr, data in sorted(secs):
        if segs and segs[-1][0] + len(segs[-1][1]) == addr:
            segs[-1][1] += data
        else:
            segs.append([addr, bytearray(data)])
    return entry, segs


def boot_image(entry, segs):
    """The ROM's image format: header (0xE9, segment count, DIO, 16 MB / 40 MHz, entry), the extended
    header (chip id 9 = ESP32-S3, hash appended), segments (address, length, data), the XOR checksum
    seeded 0xEF in the last byte of a 16-byte boundary, then the SHA-256 of all of it."""
    out = bytearray(struct.pack('<BBBBI', 0xE9, len(segs), 2, 4 << 4, entry))
    out += struct.pack('<B3sHBHH4sB', 0xEE, b'\0\0\0', 9, 0, 0, 0xFFFF, b'\0' * 4, 1)
    chk = 0xEF
    for addr, data in segs:
        data = bytes(data) + b'\0' * (-len(data) % 4)
        out += struct.pack('<II', addr, len(data)) + data
        for b in data:
            chk ^= b
    out += b'\0' * (15 - len(out) % 16) + bytes([chk])
    return bytes(out + hashlib.sha256(out).digest())


def litmus_flash(tmp_path, n_iter=N_ITER):
    tc = toolchain()
    if tc is None:
        pytest.skip('pioarduino Xtensa toolchain not installed (make build installs it)')
    gcc, dyn = tc
    (tmp_path / 'mp.c').write_text(LITMUS_C % {'n': n_iter})
    (tmp_path / 'mp.ld').write_text(LITMUS_LD)
    elf = tmp_path / 'mp.elf'
    subprocess.run([gcc, f'-mdynconfig={dyn}', '-O2', '-mlongcalls', '-mno-serialize-volatile',
                    '-mtext-section-literals', '-ffreestanding', '-nostdlib',
                    '-Wl,-T,' + str(tmp_path / 'mp.ld'), '-o', str(elf), str(tmp_path / 'mp.c')],
                   check=True, capture_output=True)
    img = boot_image(*elf_sections(elf.read_bytes()))
    flash = bytearray(b'\xff' * (16 << 20))
    flash[:len(img)] = img
    path = tmp_path / 'litmus.bin'
    path.write_bytes(flash)
    return str(path)


def test_cores_see_stores_in_program_order(tmp_path, request):
    flash = litmus_flash(tmp_path)
    name = instance_name(request.node.name, request.node.nodeid)
    x4emu(name, 'stop', check=False)
    x4emu(name, 'run', '--flash', flash)
    try:
        log = os.path.join(ROOT, '.x4emu', name, 'uart0.log')
        t0, m = time.time(), None
        while time.time() - t0 < 120 and m is None:
            time.sleep(0.5)
            try:
                m = re.search(r'MP litmus: (\d+) iterations, (\d+) violations', open(log, errors='replace').read())
            except FileNotFoundError:
                pass
        assert m, f'the litmus printed no result in 120 s:\n{open(log, errors="replace").read()[-2000:]}'
        assert int(m.group(1)) == N_ITER
        assert int(m.group(2)) == 0, (
            f'the APP CPU saw FLAG == i with a stale DATA {m.group(2)} times in {N_ITER} rounds: the two '
            'cores see plain stores out of program order (TCG_GUEST_DEFAULT_MO, docs/smp.md)')
    finally:
        x4emu(name, 'stop', check=False)


STOCK = os.path.join(ROOT, 'images', 'device', 'flash-2026-10-12-a.bin')     # stock 7.6.8
SD_FILES = os.path.join(ROOT, 'images', 'device', 'sd-files')


@pytest.mark.skipif(not os.environ.get('X4EMU_SMP_STRESS'), reason='slow stress run: set X4EMU_SMP_STRESS=1')
def test_stock_boots_without_spinlock_asserts(tmp_path):
    if not (os.path.exists(STOCK) and os.path.isdir(SD_FILES)):
        pytest.skip('device dump / card files not available')
    k, boots = 4, int(os.environ.get('X4EMU_SMP_BOOTS', '3'))
    base = tmp_path / 'stock.bin'
    shutil.copyfile(STOCK, base)
    # the dump holds the owner's WiFi credentials: every boot below runs a copy of this redacted one
    subprocess.run([PY, os.path.join(ROOT, 'tools', 'nvsedit.py'), str(base), 'redact'], check=True, stdout=subprocess.DEVNULL)
    card = tmp_path / 'card.img'
    subprocess.run([PY, os.path.join(ROOT, 'tools', 'mksd.py'), str(card), '--size', '256M', '--src', SD_FILES],
                   check=True, stdout=subprocess.DEVNULL)
    results, lock = [], threading.Lock()

    def worker(j):
        name = instance_name(f'smp-stress{j}')
        for b in range(boots):
            d = tmp_path / f'w{j}'
            d.mkdir(exist_ok=True)
            fl, sd = d / 'flash.bin', d / 'card.img'
            shutil.copyfile(base, fl)
            shutil.copyfile(card, sd)
            x4emu(name, 'stop', check=False)
            x4emu(name, 'run', '--flash', str(fl), '--sd', str(sd), '--boot-hold-power')
            con = os.path.join(ROOT, '.x4emu', name, 'console.log')
            t0, txt, done_at = time.time(), '', None
            while time.time() - t0 < 40:
                time.sleep(0.5)
                txt = open(con, errors='replace').read() if os.path.exists(con) else ''
                if 'assert failed' in txt or 'Guru Meditation' in txt:
                    break
                if done_at is None and 'main_task: Returned from app_main()' in txt:
                    done_at = time.time()
                if done_at and time.time() - done_at > 6:      # the assert came ~0.1 s after it
                    break
            x4emu(name, 'stop', check=False)
            bad = next((l for l in txt.splitlines() if 'assert failed' in l or 'Guru Meditation' in l), None)
            with lock:
                results.append((name, b, done_at is not None, bad))

    threads = [threading.Thread(target=worker, args=(j,)) for j in range(k)]
    for t in threads:
        t.start()
        time.sleep(0.7)
    for t in threads:
        t.join()
    bad = [r for r in results if r[3]]
    assert not bad, f'{len(bad)} of {len(results)} boots aborted: ' + '; '.join(f'{n} boot {b}: {l}' for n, b, _, l in bad)
    assert all(r[2] for r in results), f'a boot never returned from app_main: {results}'
