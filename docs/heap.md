# `CORRUPT HEAP: Bad head at 0x3fce45c0` on stock 7.2.4: a double close in the firmware

Written 2026-10-12 (squad member `hp`). Status: **diagnosed as the firmware's, not fixed** (nothing
in the emulator to fix). The instrumentation that found it is a debug-only QEMU patch on branch
`x4pro-heap` (`target/xtensa/x4watch.c`), not part of `qemu-patches/`.

## The symptom

Two of 288 stock 7.2.4 boots in `docs/smp.md`'s runs (one under TSO, one under full sequential
ordering) stopped with

```
CORRUPT HEAP: Bad head at 0x3fce45c0. Expected 0xabba1234 got 0x3fcbed44
assert failed: multi_heap_free multi_heap_poisoning.c:279 (head != NULL)
```

at the same address with the same value. Both backtraces end in the same seven frames:

| frame | 7.2.4 function | what it is |
|---|---|---|
| `0x40385c5a` | `FUN_40385c38` | `multi_heap_free` (poisoning layer), head check fails |
| `0x4237404f` | `FUN_4237402c` | `vQueueDelete` |
| `0x4037b512` | `FUN_4037b4e0` | `_lock_close` (asserts `xQueueGetMutexHolder(h) == NULL`, the string names it) |
| `0x4037b5c5` | `FUN_4037b5bc` | `__retarget_lock_close_recursive(lock)`: copies the handle to its own stack and closes the copy |
| `0x42330b22` | `FUN_42330b08` | picolibc `fclose(f)`: flush, `if (f->lock > 1) close lock` (`f+16`), `close` callback |
| `0x421032b3` | `FUN_4210324c` | the system-font slot's reset: `if (slot->fp) fclose(slot->fp)` (`slot+0x7a0`), zeroes `fp` only afterwards |
| `0x421033ad` | `FUN_42103344` | the slot's `close()` (vtable `+0x38`, vtable at `0x3c3d4b34`) |

Above that the two differ, and the stacks name the tasks (TCBs found by name in a DRAM dump,
layout as in `tools/tcbwalk.py`):

| task | prio | core | stack | crash |
|---|---|---|---|---|
| `xteink_bgio_mt` (background I/O, maintenance queue) | 3 | 1 | `0x3c6e611c..` | TSO boot: job `font.system.boot_plan_restore` (`FUN_421ea060`) -> `FUN_420f9a84`, which closes every slot whose open flag is set before reopening it |
| `xteink_ui` | 5 | 1 | `0x3c6cb5b0..` | SC boot: `FUN_420f9b9c` (from `0x420270a3`), which closes the flagged slots when its font check `FUN_421e9230` (call at `0x420f9dba`) returns anything but 0, 2 or 3, and resets all slots (vtable `+0x18`, `FUN_42103310` -> `FUN_4210324c`, no flag check) when it returns 2 |

`xteink_bgio_fg` (prio 4) is pinned to core 1 as well; `main` runs on core 0. The slot object is
`0x3fcad110` (the base face; the font manager at `0x3fcae680` holds the slots at `+8`/`+0xc` and
their open flags at `+0x10`).

## What the value says: the block was already free

- The first DRAM heap starts at `0x3FCBED30` (`heap_init` line in every boot log). ESP-IDF's
  `multi_heap_register_impl` puts its 20-byte `heap_t` there and the TLSF control structure right
  after it, so `0x3FCBED44` is `&control->block_null`, the TLSF free-list sentinel.
- TLSF's block header is `prev_phys, size, next_free, prev_free`; TLSF hands out the block plus 8,
  where the poisoning layer puts its 8-byte head (canary, size) before the caller's pointer. A free
  block's `next_free` therefore lies exactly on the canary, and a block that is alone in its free
  list has `next_free == &block_null`.
- So `got 0x3fcbed44` means the block at `0x3fce45b8` (head `0x3fce45c0`, the mutex at
  `0x3fce45c8`) was **free** when `vQueueDelete` freed it: the mutex was freed twice.

The logging watchpoint below shows TLSF writing exactly this value there on every ordinary free of
the block (`0x40386097: s32i` of `a9 = 0x3fcbed44` to `0x3fce45c0`, `a3 = 0x3fce45b8`, the block).

## Whose mutex it is (clean boots, instrumented)

The block's life in a clean boot is the same every time the heap has its usual layout (all but a
few of the instrumented boots):

1. 18 times a 512-byte DMA bounce buffer: `sdmmc_read_sectors` (`FUN_4229d000`) allocates it with
   `MALLOC_CAP_DMA` because FATFS hands it a PSRAM buffer, the SD host model's DMA fills it
   (`dwc_sdmmc`, `addr=3fce45c8 len=512`, inside the allocation every time), the driver frees it.
2. The 19th allocation (92 bytes) is the **lock of the base system-font file**
   (`/sdcard/XTData/system_fonts/misans-demib…`): `xteink_bgio_mt` opens it in `FUN_421040a8`
   (`font.system.layered_open`), the first `fread` takes the FILE lock, which picolibc creates lazily
   (`0x42330d60`: under a global lock, `if (!f->lock) __retarget_lock_init_recursive(&f->lock)` ->
   `xQueueCreateMutex` -> `0x3fce45c8`). That FILE stays open as `slot->fp` for the rest of the run;
   neither slot close (`FUN_42103344`, `FUN_42103310`, `FUN_4210324c`) runs in a clean boot.

Read back from a running clean boot: slot `0x3fcad110` has `+0x7a0 = 0x3c6ee520` (also held by its
sub-reader at `+0x614`), and that FILE's lock word (`+16`) is `0x3fce45c8`, the block, alive
(`0xabba1234`, size `0x5c`). The second slot `0x3fcada60` holds FILE `0x3c6ee60c` with lock
`0x3fce4634`, the next mutex up. So the crash is the slot's `fclose(slot->fp)` meeting a FILE whose
lock had been deleted already: the base font file was closed twice.

## Not a model writing guest memory

`X4_DMAWATCH` hooks `address_space_write`, `address_space_set` and `address_space_map(is_write)`
(every DMA path: GDMA for SPI2/AES, `dwc_sdmmc`, anything using `dma_memory_*` or
`cpu_physical_memory_*`) and logs any write touching `0x3fce4580..0x3fce4600` with the model's host
backtrace. In every instrumented boot the only such writes were the SD host's 512-byte sector reads
into a live bounce buffer: the per-boot check tracks every allocation and free in the range from
the poisoning layer's hooks and found none of 5622 writes (294 boots) outside a live allocation. (An
earlier version of the check that assumed the single block flagged one boot whose heap layout had
moved by 0x20; its log was not kept.) The descriptor write-back goes to `0x3fcdcbd0`, elsewhere. The SD
host model completes commands and DMA synchronously inside the firmware's CMD register write, so
it cannot write late into a buffer the driver has already freed. A model copying through a raw RAM
pointer (`memory_region_get_ram_ptr`) would bypass the hook; the only ones in this machine that do
are the cache model (flash contents) and `ssi_psram` (its own RAM), neither of which reaches
internal DRAM. On the CPU side, the logging watchpoint on the head (`X4_WPWATCH`, in every saved
log that had it) saw six store sites and no others: `0x40385d40`/`d44` (the poisoning layer writing
canary and size on allocation) and `0x40386097`/`099`, `0x403863b7`/`3b9` (TLSF linking the block
into a free list).

## Why the firmware can close it twice

Every path that closes the slot's FILE reads `slot->fp`, calls `fclose`, and clears `slot->fp`
afterwards:

- `FUN_4210324c` (reset): `if (fp) fclose(fp)`, then more clean-up, then `fp = 0`;
- `FUN_42103344` (`close`, vtable `+0x38`) -> `FUN_4210324c`, flags cleared after it returns;
- `FUN_42103310` (vtable `+0x18`) -> `FUN_4210324c` with no flag check;
- `FUN_421040a8` (`open`): `if (fp) fclose(fp); fp = new`.

I found no lock around the slots: `FUN_420f9a84` (bgio's restore) and `FUN_420f9b9c` (the UI)
take none (`FUN_42340f10`/`FUN_420fc598` around the restore's loop only set and clear a flag
record); the restore's compare-and-swap of a token on the plan object (`FUN_40382858` on plan
`+0x24`) guards the plan, not the slots, and the UI never touches it. The two tasks share core 1,
so they interleave by preemption: the UI (prio 5) preempts bgio_mt (prio 3) whenever it wakes, and
bgio_mt runs whenever the UI blocks.
`fclose` blocks easily (its close callback goes through the VFS and FATFS, which take the volume
lock and may wait for the SD card), and picolibc's `fclose` deletes the FILE's lock *before* that
callback. So: task A enters `FUN_4210324c`, reads `fp`, `fclose` deletes the lock and blocks in
the close callback; task B runs, reads the same `fp` (A clears it only after `fclose` returns),
and its `fclose` deletes the mutex again -> the head holds TLSF's free-list link -> `Bad head`.
Which task is second only depends on who blocked: the two crashes have one each. Sequential calls
cannot do it (each clears `fp` before returning), and the calls into the slot's sub-readers
(`+0x610`/`+0x6d0`/`+0x680`/`+0x740`, `FUN_42104f1c`/`FUN_4210815c`/`FUN_421083bc`) only clear
their own state; I found no other `fclose` of `slot+0x7a0`.

In clean boots neither close runs: the restore finds the slots closed (flags `0`) and only opens
them, and the UI's check about a second later returns 0 (`0x420f9dbd`, `a10 = 0`).

## Caught in the act

Slowing core 0 a little (`X4_JITTER=2000:20:1`: the `main` task's core loses about a sixth of its
time in random 0..2 ms stalls, as a loaded host does to one vCPU thread) makes the race common. Boot
`hph0-1` of run w8, watch log (guest ms, task from the stack pointer):

```
2951.751 bgio_mt  restore #1 (FUN_421ea060)       slots closed: opens slot 0 (2952), slot 1 (3419), writes .cache (4050..4065)
4065.709 bgio_mt  restore #2, 0.7 ms after #1's last fclose; flags 0x101: closes slot 0
4065.778 bgio_mt  slot reset FUN_4210324c(0x3fcad110)  fp = 0x3c6ee520, then suspended inside it
4066.683 ui       font check FUN_420f9b9c           -> FUN_421e9230 returns 5
4067.118 ui       slot reset FUN_4210324c(0x3fcad110)  fp = 0x3c6ee520 still set
4068.026 ui       fclose(0x3c6ee520) -> vQueueDelete(0x3fce45c8) -> free(0x3fce45c8)
4069.080 ui       slot 1: fclose(0x3c6ee60c) -> vQueueDelete(0x3fce4634)
4074.777 ui       resets both slots again (vtable +0x18)
          bgio_mt resumes inside its reset of slot 0:
Guru Meditation Error: Core 1 panic'ed (StoreProhibited)   EXCVADDR 0x00007c84
Backtrace: 0x421e075f 0x421e07f1 0x42108807 0x4210882d 0x42103288 0x421033ad 0x420f9ad0 0x421ea16c ...
```

From `0x421033ad` outwards these are the frames of the TSO boot's `CORRUPT HEAP` stack: bgio_mt in
the restore -> `FUN_420f9a84` -> slot close -> `FUN_4210324c`. Here bgio_mt was suspended early in
the reset (inside `FUN_42108814`, before its own `fclose`) and died on the state the UI had torn
down. When the switch falls later,
between bgio_mt's load of `fp` and its `fclose`, or while bgio_mt's `fclose` waits in its close
callback after deleting the lock, one of the two `fclose`s deletes a dead mutex: `Bad head at
0x3fce45c0 … got 0x3fcbed44`. Boot `hph1-14` crashed the same way (restore #2 at 4440.1, UI check 5
at 4441.3, UI `fclose(0x3c6ee520)` at 4442.2 while bgio_mt was in the reset), and `hph0-19` ran the
same interleaving without crashing (restore #2 at 4759.9, UI check 5 at 4760.9, UI `fclose` at
4761.9). The near misses `hpg0-8` (run w7) and `hpm0-18` (run w11) had the UI's check return 2 during
restore #2 and reset both slots (vtable `+0x18`) while bgio_mt was opening slot 0 again.

Every one of them has the same two preconditions, and no clean boot has either: **a second restore**
right after the first (clean boots run one), and **the UI's font check during it** (it returns 5 or
2 instead of 0). The second restore is not a second request: with the poster hooked (`hpm0-18`, run
w11), `main` posted the job once (`FUN_42100a2c` -> `FUN_421e9dac` at 3047.4, job kind `0xf`,
thunk `0x421ea310`) and bgio_mt still ran the restore at 3051.7 and again at 4177.3, so the job
itself runs again (what makes it do so was not pinned down).

## Rates

All on stock 7.2.4 (`flash-2026-09-06-a.bin`, redacted), the device's card files, `qemu-patches`
0001-0024 plus the debug patch, 14 s per boot, at most four emulators at once (other agents' load
on top). "Race" means the interleaving above was seen in the watch log (second restore + UI check
closing slots while bgio_mt is inside a slot close); it is only detectable in runs that hook the
restore entry (w7 onward).

| run | perturbation | boots | `CORRUPT HEAP` | StoreProhibited (same race) | race seen |
|---|---|---|---|---|---|
| `docs/smp.md` (TSO and SC builds) | 3-6 emulators + other agents | 288 | **2** | 0 | (no hooks) |
| w1, w2a (`--fast-epd`), w2b, w4, w9 | none (4 emulators) | 252 | 0 | 0 | 0 in the 11 hooked boots |
| w5b | CPU1 stalls `2000:20:2` | 19 | 0 | 0 | (no hooks) |
| w3j, w6 | both CPUs `3000:10:3`, `2000:20:3` | 40 | 0 | 0 | (no hooks) |
| w5a, w7, w8, w11, w12, w13 | **CPU0 stalls** `2000:20:1` | 235 | 0 | **2** | **5** of 211 hooked |
| w14 | **CPU0 stalls** `3000:25:1` | 136 | 0 | **1** | **3** |
| w10 | CPU0 stalls `5000:30:1` | 5 | 0 | 0 | 0 (the heap layout moves; the font locks land elsewhere) |
| v768, v768b, v768c: stock **7.6.8** (`flash-2026-10-12-a.bin`) | CPU0 stalls `2000:20:1` (38) / `3000:25:1` (145) | 183 | 0 | 0 | (7.2.4 hooks do not apply) |

"Race seen" = a boot whose watch log shows the UI resetting a slot while bgio_mt is inside a reset or
open of the slots during a second restore (`classify.py` in the squad's scratch), whether it crashed
or not. Every crash was such a boot; no clean boot had a second restore. The two variants differ
only in where bgio_mt was suspended: all three captured crashes stopped it inside `FUN_42108814`
(early in the reset), so it died on the torn-down state; the `CORRUPT HEAP` variant needs the switch
inside or just before its `fclose`, which these runs did not happen to hit. Over all 294 boots
checked with the allocation-tracking checker, the models wrote 5622 times into the watched range,
never outside a live allocation.

Slowing core 0 (where `main` posts the restore job and the boot sequence runs) against core 1
(where `xteink_ui` and `xteink_bgio_mt` race) is what opens the window; slowing core 1 or both does
not. A loaded host does this at random to one vCPU thread, consistent with `docs/smp.md` seeing
the corruption only with several emulators running and my quieter runs seeing none without help. The interleaving
itself is all firmware: both tasks are pinned to core 1 and switch by FreeRTOS preemption and
blocking, which the emulator does not reorder.

## Verdict

**The firmware's.** Stock 7.2.4 resets the system font's slots from two tasks without a lock: the
background-I/O task `xteink_bgio_mt` (prio 3) in a second `font.system.boot_plan_restore`, and
`xteink_ui` (prio 5) when its font check fails during that restore. Both run `FUN_4210324c`,
which reads `slot->fp`, `fclose`s it and clears it only afterwards, so the second one closes a FILE
the first already closed: its lock mutex is deleted twice and the poisoning check finds TLSF's
free-list link `&block_null` (`0x3fcbed44`) in the head canary. Or the slower one dies on state the
other tore down (StoreProhibited in `FUN_42108814`'s callee `0x421e075f`). No emulator model writes
the block; nothing in QEMU needs fixing. The same tasks, priorities and pinning run on the device,
so the race is there too; whether the device ever meets the timing that triggers a second restore
during the UI's check is not known (no device log of it).

7.6.8 (what the device ran then) did not crash in 183 boots under the same stalls, where 7.2.4
crashed 3 times in 371; at 7.2.4's rate 1.5 crashes were expected, so this does not show a fix by
itself (p = 0.2). Its restore was rewritten (`FUN_42204b80`: the slot reopen `FUN_421265fc` is
retried up to twice after a 40-tick delay call, `FUN_4237ddf4(0x28)`, while it reports result 3),
which looks like the vendor met the same contention, but I did not trace 7.6.8's slot close.

What stays uncertain: why the restore job runs a second time (posted once by `main`, returned 0 the
first time, then re-run by the bgio machinery behind `FUN_4233a978`); why stalls on core 0 bring
it on; and the exact suspension point of the two `CORRUPT HEAP` boots, which were not instrumented.

## Reproducing

With the debug build (`git -C qemu worktree add wt-heap x4pro-heap`, built with the Makefile's
configure line; the worktree's three model symlinks must point at `/Users/mini/x4pro-emu/models/…`):

```
tools/mkflash.py F.bin --raw images/device/flash-2026-09-06-a.bin && tools/nvsedit.py F.bin redact
tools/mksd.py C.img --size 256M --src images/device/sd-files
X4EMU_QEMU=$PWD/qemu/wt-heap/build/qemu-system-xtensa X4_JITTER=3000:25:1 \
X4_WATCH_LOG=/abs/watch.log \
X4_WATCH=0x421ea063,0x420f9dbd,0x42103347/m2+0x7a0:1,0x4210324f/m2+0x7a0:1,0x42103313,0x42330b0b/m2:8 \
  tools/x4emu --name hp0 run --flash F.bin --sd C.img --boot-hold-power
```

and boot it 20-50 times (the squad's `repro.py` did this four instances at a time): a boot with two
`pc=421ea063` lines (restore entries) and a `pc=420f9dbd` line with `a10` of 5 or 2 is the race.
About one boot in forty showed it and about one in a hundred crashed, at that evening's host load
(the rate moves with it). The hooks are not needed for the crash itself, only to see it.

## The instrumentation (QEMU branch `x4pro-heap`, debug only)

The branch lives in the local QEMU clone (`git -C qemu branch --list x4pro-heap`) and, should the clone be
re-made, in `images/qemu-debug-x4watch-2026-10-13.bundle` (`git -C qemu fetch ../images/qemu-debug-x4watch-2026-10-13.bundle x4pro-heap:x4pro-heap`).

`target/xtensa/x4watch.c`, wired into `translate.c`, `system/physmem.c`, `accel/tcg/watchpoint.c`,
`accel/tcg/tcg-accel-ops-mttcg.c` and `hw/sd/dwc_sdmmc.c`; every knob is an environment variable and
off by default:

| variable | effect |
|---|---|
| `X4_WATCH="PC[/fR:LO:HI][/mR[+OFF]:N][/sUS[:K]],..."` | at each execution of PC (use the function entry + 3, after `entry`): CPU, guest ns, `a0..a11`, a windowed backtrace (live windows from the physical register file, spilled ones from the base save area), N words at `aR+OFF`; `f` filters on a register range, `s` stalls that vCPU thread for US µs on the first K hits |
| `X4_WPWATCH="ADDR:LEN"` | a QEMU watchpoint (flag `0x08`) on both CPUs that logs every CPU store into the range with PC, registers and backtrace, then lets the store proceed |
| `X4_DMAWATCH="LO:HI,..."` | every non-CPU write into the ranges, with the address space, the data and the host backtrace |
| `X4_WATCH_LOG=FILE` | where all of it goes (line-buffered, appended) |
| `X4_JITTER="US:PCT[:MASK]"` | once per host millisecond, with probability PCT %, stall a vCPU thread (MASK of CPUs) for 0..US µs: what a loaded host does to one MTTCG thread |
| `X4_SDLAT_US=N` | the SD host accepts CMD17/18 at once and completes them N µs of virtual time later |

`x4emu run` passes the environment through, so `X4EMU_QEMU=…/qemu/wt-heap/build/qemu-system-xtensa
X4_WATCH=… x4emu run …` is all it takes. Function addresses used here (7.2.4): `fclose` `0x42330b08`,
`vQueueDelete` `0x4237402c`, `multi_heap_free` (poisoning) `0x40385c38`, `poison_allocated_region`
`0x40385d38`, `fopen` `0x42330d84`, `_lock_close` `0x4037b4e0`.
