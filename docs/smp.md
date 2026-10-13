# Two cores, one Mac: the `spinlock.h:141` assert and the memory order

Written 2026-10-12 (squad member `sl`). The fix is the QEMU patch
`target/xtensa: TCG memory order TSO, so two cores see stores in program order`
(`target/xtensa/cpu-param.h`); a second patch, `esp32s3.cache: CTRL1 is the bus-shut register, so the
caches read as enabled`, gives the assert messages their strings back. Both were developed on branch `x4pro-sl` of the QEMU
tree and are qemu-patches 0021 and 0022 on `x4pro`; the regression test is `tests/test_smp.py`.

## The symptom

The stock firmware aborted now and then, always the same way, then reset:

```
assert failed: 0x40384078 <cached disabled>:141 (<cached disabled>)      # 7.6.8
assert failed: 0x40384060 <cached disabled>:141 (<cached disabled>)      # 7.2.4
```

(With the cache patch the same assert reads
`assert failed: spinlock_acquire spinlock.h:141 (lock->owner == core_owner_id)`.)
It looked rare and load-dependent: seen twice in one day, and both runs passed when repeated alone.
It is not rare. Six emulators side by side on the M4 Pro: **26 of 48 stock 7.6.8 boots** asserted,
about 0.1 s after `main_task: Returned from app_main()`, every time with the same backtrace. 7.2.4
does the same at boot (below). Linux CI never saw it, and could not have (see "Why only the Mac").

## Which assertion (question 1)

`0x40384078` is the return address of the `call8 __assert_func` (`0x40382420`) in 7.6.8's
`xPortEnterCriticalTimeout` (entry `0x40383f68`, `spinlock_acquire` inlined). The strings the call
passes are in the image: file `spinlock.h`, function `spinlock_acquire`, line 141, expression
`lock->owner == core_owner_id`. The line numbers of all six asserts in the function (106, 141,
142, 145, 146, 84) match the `spinlock.h` PlatformIO ships for IDF 5.5.5
(`~/.platformio/packages/framework-arduinoespressif32-libs/esp32s3/include/esp_hw_support/include/spinlock.h`),
so 6.0.1's file has the same layout there:

```c
lock_set = esp_cpu_compare_and_set(&lock->owner, SPINLOCK_FREE, core_owner_id);
...
exit:
    if (lock_set) {
        assert(lock->owner == core_owner_id);     // 141
        assert(lock->count == 0);                 // 142
```

So 141 fails when the compare-and-set **reported success** and the lock word, read straight back,
holds something else. It is not the timeout path: a timeout lands on 145
(`lock->owner == SPINLOCK_FREE || lock->owner == other_core_owner_id`), and FreeRTOS's queue critical
sections pass `portMUX_NO_TIMEOUT` (-1, the `beqi a2, -1` at `0x40383fca`), so CCOUNT never enters
into it. (docs/log.md's AES entry quotes 145's expression for 141; the image says otherwise.)

`esp_cpu_compare_and_set` is not inlined: 7.6.8 `0x40380820`, 7.2.4 `0x40380738`. For an address in
`0x3C000000..0x3DFFFFFF` (external RAM, where S32C1I cannot be used) it takes ESP-IDF's software path:

```
rsil   a12, 3
s32c1i on external_ram_cas_lock (0x3FC9FC28, DRAM)  0 -> 1    ; failed: return false
memw ; l32i owner ; compare
memw ; s32i new -> owner                                       ; if equal
       s32i 0   -> external_ram_cas_lock                       ; <- no MEMW between the two stores
memw ; wsr.ps
```

and `spinlock_release` frees a lock with a plain `s32i SPINLOCK_FREE` (`0x40383ec8`). Both rely on
the hardware making a core's stores visible to the other core in program order.

## How QEMU runs the two cores (question 2)

- **MTTCG**, one host thread per vCPU: `configs/targets/xtensa-softmmu.mak` has
  `TARGET_SUPPORTS_MTTCG=y`, and `default_mttcg_enabled()` (accel/tcg/tcg-all.c) turns it on unless
  icount is on. `qemu-system-xtensa -accel help` lists only `tcg`; the esp32s3 machine has
  `default_cpus = max_cpus = 2` and realizes both cores on a shared system address space (IROM/DROM
  per CPU). `x4emu run --deterministic` (`-icount 3`) switches to single-threaded round-robin.
- **S32C1I** (translate.c `translate_s32c1i`) is `tcg_gen_atomic_cmpxchg_i32` against SCOMPARE1,
  after `check_atomctl`: a host atomic CAS (`casal` on arm64) for RAM, a stop-the-world exclusive
  step for MMIO. It is atomic across vCPUs; it was never the problem.
- **MEMW** is `tcg_gen_mb(TCG_BAR_SC | TCG_MO_ALL)`, a `dmb ish` on arm64.
- **Plain loads and stores** carry no ordering at all: `target/xtensa/cpu-param.h` had
  `TCG_GUEST_DEFAULT_MO (0)` ("Xtensa processors have a weak memory model"), so `tcg_gen_req_mo()`
  emits no barrier between them. On an x86 host (TSO, `TCG_TARGET_DEFAULT_MO = ALL & ~ST_LD`) that
  changes nothing; on arm64 two guest stores are two plain `str` the host may make visible in either
  order.
- **CCOUNT** is `ccount_base + ticks(QEMU_CLOCK_VIRTUAL - time_base)` per vCPU (op_helper.c
  `update_ccount`): host time while the VM runs, so it keeps running while a vCPU thread is
  descheduled. A spinlock *timeout* could trip from that, but none is involved here.

## The mechanism, with evidence (question 3)

**Caught in the act.** A gdbstub breakpoint on the assert-141 block (`0x40384069`) with five more
instances running as load, then `info registers -a` over QMP:

```
CPU#0 PC=40384069 PRID=cdcd  A3=0000cdcd (own id)  A5=3c6f97e8 (the lock: PSRAM)  A8=0000abab (owner read back)
      AR18=3fc9fc28 (external_ram_cas_lock)  backtrace: idle task -> ISR (0x422aa142) -> xQueueGenericSendFromISR
CPU#1 PC=40383ed1 (vPortExitCritical of another lock); 0x3c6f97e8 still in its older windows
lock at the dump: 3c6f97e8: b33fffff 00000000   (CPU1 had released it again)
```

CPU0's compare-and-set on a queue's spinlock in PSRAM (`xQueueLock`, queue + 84) succeeded, and the
word read back was CPU1's id: both cores held the lock. The interleaving that does that is the
message-passing pattern: CPU1 stores `owner = 0xABAB` and then `external_ram_cas_lock = 0`; the
arm64 host makes the second store visible first; CPU0, spinning on the software lock, takes it,
reads `owner` and still sees `SPINLOCK_FREE`, writes `0xCDCD` and returns true. CPU1's store lands,
and CPU0's read-back sees `0xABAB`. 7.2.4 asserts the same way, from an ISR and from tasks with PSRAM
stacks in `xQueueReceive` (the `test_lua_status` case).

**Isolated on the bare cores.** `tests/test_smp.py` boots a 600-byte program (as the ROM's
second-stage bootloader, flash offset 0) that runs the pattern itself: PRO stores `DATA = i`, then
`FLAG = i`, no MEMW between them; APP waits for `FLAG == i`, MEMW, reads `DATA`. With MO 0 the APP
CPU saw the new flag with stale data **78, 102, 342 times per million rounds** (three runs).

**Which order matters.** The same reproduction with four builds that differ only in
`TCG_GUEST_DEFAULT_MO`:

| `TCG_GUEST_DEFAULT_MO` | stock 7.6.8 boots, 6 instances x 8 | litmus violations / 1 M rounds |
|---|---|---|
| `0` (before) | **26 / 48** asserted | 78, 102, 342 |
| `TCG_MO_LD_LD` | 22 / 48 | 132 |
| `TCG_MO_ST_ST` | 0 / 48 | 0 |
| `TCG_MO_ALL & ~TCG_MO_ST_LD` (TSO, the patch) | **0 / 48** | 0, 0 |

Store-to-store order is the whole story; load order does not touch it.

Stock 7.2.4 asserts the same way (6 of 24 boots, `0x40384060:141`); see "Rates".

## The fix (question 4)

`TCG_GUEST_DEFAULT_MO (TCG_MO_ALL & ~TCG_MO_ST_LD)`: TSO, what an in-order core with a FIFO write
buffer gives (a load may pass an earlier store; everything else stays in program order). It is free
on x86 hosts; on arm64 TCG adds a `dmb ishld` before each guest load and a `dmb ish` before each guest
store.

### Which memory model is faithful

What the S3's cores guarantee, from the sources there are:

- **The core configuration** (`xtensa/esp32s3/include/xtensa/config/core-isa.h` in PlatformIO's
  IDF headers): `XCHAL_CORE_ID "LX7_ESP32_S3_MP"`, `LX7.0.12`; one load/store unit; a 4-entry write
  buffer (`XCHAL_NUM_WRITEBUFFER_ENTRIES 4`); no data cache in the core (`XCHAL_DCACHE_SIZE 0`; the
  cache in front of flash and PSRAM is Espressif's, outside the core); S32C1I and L32AI/S32RI present
  (`XCHAL_HAVE_RELEASE_SYNC 1`); no PIF write responses. An in-order pipeline with one load/store
  unit cannot complete loads out of order; the write buffer is where a store waits while later loads
  go ahead.
- **Tensilica's own Linux port** (`arch/xtensa/include/asm/barrier.h`, "Copyright (C) 2001 - 2012
  Tensilica Inc."): `__rmb()` is `barrier()`, a compiler barrier only, so loads are never reordered
  with loads; `__wmb()` and `__mb()` are MEMW; `__smp_mb__before_atomic()`/`after_atomic()` are compiler
  barriers, so S32C1I orders by itself. The ISA's contract is therefore "loads in order, MEMW for
  anything involving stores": it does not promise store-to-store order.
- **Zephyr** (`include/zephyr/arch/xtensa/atomic_xtensa.h`): "Actual Xtensa hardware seems to have
  only in-order pipelines".
- **ESP-IDF on the S3**, which is what the silicon is known to run correctly on two cores:
  `spinlock_release()` frees a lock with a plain store (`NEED_VOLATILE_MUX` is empty unless the ESP32
  PSRAM workaround is on), and the external-RAM compare-and-set has no MEMW between the store it
  protects and the store that releases. IDF puts MEMW where a store has to be *complete*: before
  `wsr ps` in its interrupt-masking compare-and-set fallbacks (`xt_utils.h`, `hal/cpu_ll.h`) and, via
  GCC's default `-mserialize-volatile`, before every volatile (device-register) access, i.e. to drain
  the write buffer before a later load or an interrupt. Store order between two plain stores it never
  fences: the write buffer drains in order.

So the hardware is TSO: loads in order, stores in order, a load may pass a buffered store. The ISA
allows more (store reordering), but no S3 software that works on the silicon can depend on it, and
modelling it is what broke the stock. TSO is not stronger than the chip either: `TCG_MO_ALL`
(sequential consistency) would also forbid a load passing a store, which the write buffer allows.

### Measured: TSO against sequential consistency (stock 7.2.4)

Two rounds side by side (3 + 3 instances, 20 s per boot), the second with the cache patch in both:

| build | boots | spinlock asserts | `CORRUPT HEAP` | other aborts | host CPU / boot (median) |
|---|---|---|---|---|---|
| TSO | 48 + 72 = 120 | 0 | 0 | 0 | 7.88 s, 7.77 s |
| `TCG_MO_ALL` | 48 + 72 = 120 | 0 | **1** | 0 | 7.94 s, 7.74 s |

Memory-bound loop (one core, 18 reps each, the same six-way run): MO 0 18.35 M cycles, TSO 20.14 M
(**+10 %**), `TCG_MO_ALL` 20.02 M (+9 %). An earlier six-way run gave TSO +13 %. Boot CPU is the same
for both within noise (an earlier comparison against MO 0 gave about +8 %, also noisy).

**Recommendation: TSO.** It is the chip's model, it removes the failure, and sequential consistency
costs the same and adds nothing: the heap corruption it was meant to rule out happened under it too.
`TCG_MO_ST_ST` alone also fixes the failure and cost nothing measurable in the loop (in the first
six-way run it even ran faster than MO 0, which I cannot explain); it leaves load order to the host,
which the chip does not.

Rejected:
- `-accel tcg,thread=single` (round-robin, one host thread): no reordering, but a core spinning on
  the other waits for a kick. The litmus did not finish in 300 s (MTTCG: about 1 s), and the stock
  booted so slowly that its preflight sampled the power button at 2.6..3.3 s instead of 1.2 s, after
  the 3 s hold had ended, and went to deep sleep: none of 13 boots returned from `app_main` in 30 s.
- `--deterministic` (icount, also round-robin): same reason; it is fine for replays, not as a default.

## Rates

| reproduction | before (MO 0) | after (TSO) |
|---|---|---|
| stock 7.6.8, 6 instances x 8 boots | 26 / 48 | 0 / 48 |
| stock 7.6.8, 5 instances x 6 boots (load for the breakpoint runs) | 15 / 30 | - |
| stock 7.6.8, the stress test at 3 instances x 3 boots | 2 / 9 (and a second failing run) | 0 / 9 |
| stock 7.2.4, 6 instances x 4 boots (20 s each); after: also 3 x 16 and 3 x 24 | 6 / 24 asserted (141) | 0 / 168 at 141; 1 / 168 heap corruption (below) |
| litmus, 1 M rounds | 78..342 violations | 0 |

With one instance alone the stock booted cleanly in the single try made; the rate grows with the
number of emulators running (the other agents' emulators were running throughout too). Why load
raises it is not established; the failing handoff needs CPU0's ISR and a CPU1 task on the same PSRAM
queue within a few host cycles of each other.

**A different failure, not a memory-order one: a heap corruption on 7.2.4.** One of 168 TSO 7.2.4
boots stopped at 2.4 s with `CORRUPT HEAP: Bad head at 0x3fce45c0. Expected 0xabba1234 got
0x3fcbed44`, then `assert failed: multi_heap_free multi_heap_poisoning.c:279 (head != NULL)`. One of
the 120 boots under `TCG_MO_ALL` stopped with **the same address and the same value**, through the
same calls: `vQueueDelete` (7.2.4 `FUN_4237402c`, the assert string names it) freeing a queue whose
heap block's head canary holds a DRAM pointer, in a task with a PSRAM stack. Sequential consistency
forbids every reordering, so this is not ordering: a timing-dependent use-after-free or stray pointer
store, in the firmware or in a model writing guest memory (a peripheral or DMA write-back to a buffer
already freed is the first thing to check). In the 288 TSO and SC boots of 7.2.4 it happened twice
(0.7 %); none of the 7.6.8 boots with a fix (over 100) showed it. Investigated since: a race between two
of 7.2.4's own tasks, not the emulator's (`docs/heap.md`).

## Why only the Mac

x86 hosts are TSO: two stores from one host thread are never seen out of order, whatever TCG emits.
CI runs on Ubuntu x86_64, so it could not fail there, and the test's litmus passes on x86 with or
without the patch. Every arm64 host (Apple silicon, arm64 Linux) was exposed.

## `<cached disabled>` in every assert: a cache-model bug (fixed)

`__assert_func` replaces flash-resident strings with `<cached disabled>` when
`spi_flash_cache_enabled()` is false. In 7.6.8 that reads EXTMEM `DCACHE_CTRL` (0x600C4000) and
`ICACHE_CTRL` (0x600C4060) bit 0, and on a stock running normally at Home the emulator answered 0 for
both (`xp /1wx` over QMP). The cause: `hw/misc/esp32s3_cache.c` took bit 0 of `DCACHE_CTRL1` /
`ICACHE_CTRL1` (0x600C4004 / 0x600C4064) for the enable bit as well, on reads and writes. Those
registers hold the two bus-shut bits (bit 0 core 0, bit 1 core 1). The ROM's `Cache_Resume_DCache`
(0x4004f480) sets `DCACHE_CTRL |= 1` and then `DCACHE_CTRL1 &= ~3`, so the second write cleared the
enable again; `Cache_Resume_ICache` (0x4004f3f4) the same for the ICache. So every ESP-IDF assert in
the emulator lost its strings; the message never meant a flash operation was in progress.

The patch makes CTRL1 a plain two-bit register and clears both enable bits on reset. With it, the
same assert on an MO 0 build (to provoke it; 5 of 12 boots) reads:

```
assert failed: spinlock_acquire spinlock.h:141 (lock->owner == core_owner_id)
```

and the heap corruption above `assert failed: multi_heap_free multi_heap_poisoning.c:279 (head !=
NULL)`. With both patches, `tests/test_smp.py`, `tests/test_aes.py`, `tests/test_boot.py` and
`test_stock_boots_to_home_lights_and_reacts_to_touch` + `test_stock_screens_walk` pass (35 passed,
79 s), and 72 stock 7.2.4 boots ran clean.

## What remains uncertain

- `spinlock.h` of IDF 6.0.1 itself was not read; the line number and the expression come from the
  image and agree with 5.5.5's file.
- The memory model is inferred from the core configuration, Tensilica's barrier definitions and the
  code ESP-IDF runs on the chip; no litmus test has run on the device itself. The tests/test_smp.py
  program would do it unchanged if it were flashed as a second-stage bootloader, which the device
  rules here do not allow (offset 0 is the bootloader).
- Other symptoms of the same cause (any IDF critical section's data stores could reach the other core
  after the plain-store release) were not looked for; flakes on arm64 that never reproduced on CI are
  candidates.
- The ISR at 0x422aa0b2..0x422aa145 (7.6.8) and the PSRAM queue it feeds were not identified.

## Reproducing

```
# the precise check, a few seconds, no device files (on an arm64 host it fails without the patch)
.venv/bin/python -m pytest -q tests/test_smp.py -k program_order
# the symptom: four stock 7.6.8 instances x three boots, about 35 s
X4EMU_SMP_STRESS=1 .venv/bin/python -m pytest -q tests/test_smp.py -k spinlock
```

The breakpoint dump above came from a gdbstub client on `-gdb tcp:127.0.0.1:PORT` (a raw RSP `Z0`
on `0x40384069`, then `c`), attached while `x4emu run --boot-hold-power` was still holding the
button, with QMP `human-monitor-command` `info registers -a` and `x /4wx` at the stop.
