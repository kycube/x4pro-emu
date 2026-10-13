# Upstreaming the generic ESP32-S3 QEMU work

Written 2026-10-12 (squad S7, prefix `uq`). **Nothing has been submitted, pushed, forked or posted.**
Every step that leaves this machine is listed in section 8 and waits for the owner, whose name,
e-mail and `Signed-off-by` the commits do not contain yet (`Signed-off-by: TODO <owner>`).

## 1. Where things are

| What | Where |
|---|---|
| Upstream series, 15 commits on `esp-develop` @ `febae182` (still Espressif's HEAD today) | branch **`upstream-s3`** in the QEMU clone, worktree `qemu/wt-up` |
| The board on top, 3 commits | branch **`x4pro-on-upstream`** (same worktree; it is checked out there now) |
| Builds | `qemu/wt-up/build` (the Makefile's configure line); a copy of the upstream-only binary was kept in the session scratch dir |
| The x4pro branch, `qemu/build`, `qemu-patches/` | untouched |

The worktree's three panel symlinks (`hw/display/x4pro_epd_core.c`, `hw/display/epd_core.h`,
`include/hw/display/epd_core.h`) point to `../../../models/…`, which resolves only from `<repo>/qemu/`.
In `qemu/wt-up` they were repointed to absolute paths in the working tree only and marked
`git update-index --assume-unchanged`; the commits keep the relative targets, so the trees compare equal.

## 2. How Espressif takes contributions (read-only survey, 2026-10-12)

- **Route: GitHub pull requests against `esp-develop`** on `espressif/qemu`. No mailing list. Every PR
  is mirrored into Espressif's Jira (`sync-jira.yml`, the `(QEMU-NNN)` suffix and `Status: To Do`
  label appear automatically) and merged from their internal GitLab: PR #140 (sdmmc on the S3) was
  merged by `espressif-bot` 4.5 months after it was opened, after `o-marshmallow` (Omar Chebib) took
  it; PR #142 (a GDMA owner-bit fix) was *closed* while its commit landed (`33cc9a87`). Expect long
  latency and that maintainers may re-apply commits themselves.
- **Who merges:** Omar Chebib and Ivan Grokhotkov (Espressif); Lucas Tamborrino, Harshal Patil, Yuan Yu,
  Dmitry Yakovlev write most S3/C3/C6 code.
- **DCO / Signed-off-by:** not required in practice. None of Espressif's own commits and none of the
  merged external ones carry `Signed-off-by` (only commits pulled from upstream QEMU do). QEMU's
  `checkpatch.pl` in the tree does demand one; several open PRs say "DCO Signed-off-by pending". Adding
  the owner's own is the safe default.
- **PR template** (`.github` has none in this repo; PRs show Espressif's common template): Description,
  Related (Fixes #NNN), Testing, and a checklist — "Keep PRs as small as possible", "Git history is
  clean — commits are squashed to the minimum necessary", do not force-push after review.
- **Style:** QEMU style, checked with `scripts/checkpatch.pl`; Espressif's own files predate it (C99
  comments, long lines) but new contributions that pass it (PR #185, #199 say so) are the norm.
- **Tests:** CI (`.github/workflows/build.yml`) only *builds* xtensa-softmmu and riscv32-softmmu on five
  host platforms; it runs no tests. The tree has **no** ESP32 qtests or functional tests
  (`tests/functional/test_xtensa_lx60.py` is the only Xtensa one). PR #199 proposes the first,
  `tests/qtest/esp32s3-intmatrix-test.c`. Espressif tests the machines through ESP-IDF's own CI
  (pytest-embedded, `idf.py qemu`). Evidence in PR bodies is usually an ESP-IDF example's log.
- **Large model additions:** Espressif's own S3 bring-up landed as one commit per model
  (`hw/gpio: implement ESP32-S3 GPIO`, `hw/i2c: add I2C to ESP32-S3`, …) followed by a machine commit;
  TWAI came as model + "add to the machine" commit pairs. No VMState anywhere except TWAI, so a
  VMState-free model is the norm (save/restore of the S3 machine does not work either way).
- **No MAINTAINERS entries** for any ESP file, so checkpatch's "does MAINTAINERS need updating?" warning
  fires on every new ESP file, Espressif's included.
- **AI-assisted work:** Espressif states no policy. Open PRs #195/#199 disclose "Found and analysed
  with the help of an AI coding assistant (Claude Code)". **Upstream QEMU declines it outright**
  (`docs/devel/code-provenance.rst`, "Use of AI-generated content": contributions "believed to include
  or derive from AI generated content … ChatGPT, Claude, Copilot" are declined; exceptions only after a
  qemu-devel discussion). Every commit here carries `Co-Authored-By: Claude …`; it must stay — removing
  it to get past a policy would misrepresent authorship.
- **Overlapping open PRs (all by other people, none reviewed yet):**
  - #185 *intc: drive each CPU interrupt line as the OR of its sources* — a superset of our 0006
    (ESP32 too, change-only forwarding, a range-check fix). **Use it instead of ours.**
  - #177 *esp32s3_spi: count dummy cycles across the data lines in use* — the same change as our 0009.
  - #181 *esp32s3: let the GDMA reach PSRAM* — same defect as our 0019, fixed by linking all of system
    memory; ours gives the GDMA DRAM + the DCache window only.
  - #176 *esp_gdma: report the EOF descriptor address on the transmit path* — touches the same hunk as
    our 0008 (textual conflict, compatible semantics).
  - #195 *report the BBPLL calibration as done* — the analog-master register our (kept-here) analog
    master model also answers. #199 (INTR_STATUS) adds the same level bitmap #185 does.

## 3. The split, patch by patch

24 patches: **9 go upstream whole, 8 are split (generic part up, board part here), 7 stay here.**

| x4pro patch | Destination | Why |
|---|---|---|
| 0001 machine subclass, USJ model, I/O logger | split: USJ model → `hw/misc: add an ESP32-S3 USB Serial/JTAG controller model`; machine + logger here | the USJ is SoC; the machine and the logging overlays are the board's |
| 0002 GPIO, SPI2, panel device, board state | split: GPIO → `hw/gpio: …`, GP-SPI → `hw/ssi: …`; panel + state here | |
| 0003 USJ SOF on every INT_RAW read | **upstream** (folded into the USJ model) | generic ESP-IDF connection-monitor behaviour |
| 0004 I2C0 + GT911/PCF8563/CW2017, LEDC, deep sleep, QOM input | split: I2C → `hw/i2c: …`, LEDC → `hw/misc: … LEDC …`; devices, sleep, input here | |
| 0005 cache OCCUPY/LOCK DONE overlay, SPI probe answers, BUSY on reset | split: the DONE semantics re-done **in the cache model** (`hw/misc/esp32s3_cache: complete the DCache lock and occupy operations`); the overlay, probe answers and BUSY stay | upstream fixes the model; the board's overlay is now redundant but kept so the board tree equals x4pro |
| 0006 intmatrix re-routing | **upstream** — but **superseded by PR #185** | |
| 0007 SPI2 through GDMA | split: into the GP-SPI model; board wiring here | |
| 0008 GDMA: no fetch after the last descriptor | **upstream** | |
| 0009 SPI1 dummy cycles at line width | **upstream** — identical to PR #177 | |
| 0010 analog-master I2C, SENS/SAR ADC, radio stub, capped loggers | **here** (doubtful: analog master and SENS) | see below |
| 0011 LEDC hardware fades | split: into the LEDC model; state JSON here | |
| 0012 GP-SPI transfer timing | split: into the GP-SPI model; state JSON here | |
| 0013 analog master: DC-offset comparator, PLL lock | **here** (doubtful) | PHY-calibration answers for a WiFi start without a radio |
| 0014 GT911 key byte, BM8563 base-epoch | here | board devices |
| 0015 RTC IO stored-value overlay | here | brief: stays; generic-ish (a later upstream candidate) |
| 0016 dwc_sdmmc config registers + trace events | **upstream** | |
| 0017 / 0018 waveform-grey panel properties / default | here | panel model |
| 0019 GDMA reaches PSRAM through the DCache MMU | **upstream** (vs PR #181) | |
| 0020 AES OFB IV memory, CTR INC32 | **upstream** | |
| 0021 target/xtensa TSO | **upstream to Espressif**; belongs in **QEMU proper** too (section 7) | |
| 0022 cache CTRL1 is the bus-shut register | **upstream** | |
| 0023 GT911 frames latch until the clear | here | board device |
| 0024 GPIO per-pin sample counts | split (doubtful): counters in the GPIO model; state JSON here | harness instrumentation; maintainers may ask to drop it |

**Doubtful, kept here for now:** `esp32s3_saradc` (SENS: SAR ADC oneshot + temperature sensor with fixed
answers) and `x4pro_ana_i2c` (the analog-master regi2c block). Both are SoC blocks and would help other
firmware (esp-hal waits on `BBPLL_CAL_DONE`, PR #195; `adc_oneshot_read()` and
`temperature_sensor_get_celsius()` read SENS), but their reason to exist here is the canned PHY answers that
let libphy's WiFi start terminate, which Espressif's "WiFi is not emulated, use openeth" position does not
need. Offer them later as an RFC: rename `x4pro_ana_i2c` after `esp32c6_i2c_ana_mst.c`, split the PHY
answers (DC-offset comparator, RF PLL lock) from the generic regi2c/BBPLL part, map both in `esp32s3.c`
above `rtc_cntl` (SENS at 0x60008800 lies inside its window).

## 4. The upstream series (`upstream-s3`, 15 commits)

```
1b7edb4 hw/xtensa/esp32s3_intc: recompute CPU interrupt lines when a source is re-routed
05a97e4 hw/dma/esp_gdma: do not fetch the descriptor after the last one of a read
ce8e842 hw/ssi/esp32s3_spi: count dummy cycles at the read mode's line width
34ca0cc hw/sd/dwc_sdmmc: store the configuration registers, trace instead of DEBUG
a6d1bd3 hw/xtensa/esp32s3: let the GDMA reach PSRAM through the data cache's MMU
5dfeaaf hw/misc/esp_aes: OFB keeps the keystream in IV memory, CTR honours INC32
fb5c23e hw/misc/esp32s3_cache: CTRL1 is the bus-shut register, not the enable bit
aa35a01 hw/misc/esp32s3_cache: complete the DCache lock and occupy operations
fa10df2 target/xtensa: use TSO as the guest memory order
93fb85f hw/misc: add an ESP32-S3 USB Serial/JTAG controller model
6907df4 hw/gpio: add an ESP32-S3 GPIO model with pads and interrupts
7230b0c hw/ssi: add an ESP32-S3 GP-SPI (SPI2/SPI3) master model
5fc3b16 hw/i2c: add an ESP32-S3 I2C controller model
c241152 hw/misc: add an ESP32-S3 LEDC model with hardware fades
082c0e0 hw/xtensa/esp32s3: instantiate GPIO, USB Serial/JTAG, GP-SPI, I2C and LEDC
```

26 files, +2720/−79. No "x4pro", "Xteink", "stock" or CrossPoint reference in code, comments or messages;
firmware is described as "an ESP-IDF 6.0.1 application". New files carry
`Copyright (c) 2026 TODO <owner>` and `SPDX-License-Identifier: GPL-2.0-or-later`.

- **checkpatch:** `git show --format=email <c> | scripts/checkpatch.pl -` on every commit: 0 errors,
  0 warnings, except the expected "does MAINTAINERS need updating?" on the five new-file commits.
  (Before: the same code had 275 over-90-column lines, 149 trailing statements, 26 missing braces.)
  The reformatting was mechanical and verified: a token comparison (comments and whitespace dropped,
  adjacent string literals merged, braces ignored) shows each new model file identical to the x4pro
  branch's.
- **Build from the series alone:** `qemu/wt-up/build`, Makefile configure line, `ninja -j6
  qemu-system-xtensa`: clean, no compiler warnings.
- **Espressif's own esp32s3 tests:** none exist in the tree (section 2). Run instead on the upstream-only
  binary: `tests/test_aes.py`'s 30 qtest cases against `-machine esp32s3` (a scratch copy with the
  machine name changed): **30 passed**. A CrossPoint flash image on plain `-M esp32s3`: boots to its
  main loop, its console arrives through the USB Serial/JTAG chardev (with the base tree's stub that
  output vanished), the I2C master NACKs on the empty bus as the hardware does, the SPI2 panel probe
  runs through the GP-SPI model.
- **Not done:** every commit was not built on its own (only the series head); the models are
  independent files, so bisectability is likely but unverified.
- **Behaviour change for existing `-M esp32s3` users:** `-global driver=esp32s3.gpio,property=strap_mode`
  no longer reaches the GPIO device (it is `esp32s3.gpio-full` now, same property).

## 5. The board on top (`x4pro-on-upstream`) and tree equality

```
c63eb89 xteink-x4pro: e-paper panel device (SSD1677 / UC8179 / UC8279)
10f88dd xteink-x4pro: board I2C devices, sleep and RTC IO overlays, radio side
5a13af9 xteink-x4pro: the X4 Pro machine on the esp32s3 SoC's models
```

The board now uses the SoC's GPIO, USB Serial/JTAG, SPI2, I2C0 and LEDC (`/machine/soc/...`) instead of
instantiating its own copies over the stubs, and sets the GPIO idle mask through the machine's
`compat_props`. `/machine/x4pro-board` and `/machine/x4pro-input` (all x4emu uses) are unchanged.

**Build:** clean. **Smoke run** (`X4EMU_QEMU=qemu/wt-up/build/qemu-system-xtensa X4EMU_TEST_PREFIX=uq-
pytest tests/test_boot.py tests/test_aes.py tests/test_smp.py tests/test_input_delivery.py -q`):
**37 passed, 1 skipped** (the env-gated SMP stress run), 83 s.

**`git diff x4pro x4pro-on-upstream`: 21 files, +946/−417; every meson.build is identical.**

| Files | Difference | Behaviour |
|---|---|---|
| 16 files: the five models' .c/.h, `esp_gdma.c`, `esp32s3_spi.c`, `dwc_sdmmc.c/.h`, `esp_aes.c`, `esp32s3_intc.h`, `esp32s3_gpspi.h`, … | checkpatch reformatting and reworded comments; **token-identical** to x4pro | none |
| `hw/xtensa/esp32s3_intc.c` | as above, plus one local `const XtensaConfig *config` to shorten a line | none |
| `hw/misc/esp32s3_cache.c/.h` | the new LOCK/OCCUPY DONE handling (`OCCUPY_CTRL` register fields added) | none for the board: its DONE overlay (priority 2) still shadows both registers |
| `hw/xtensa/esp32s3.c` | the wiring commit (model instances, includes, `ESP32S3_GPSPI_COUNT`/`I2C_COUNT`) | SPI3 and I2C1 now exist in the SoC; the board's `iolog` regions at priority 1 still shadow them, as before |
| `hw/xtensa/xteink_x4pro.c` | uses the SoC's instances (−~60 lines), `x4pro_soc_device()`, the compat prop, header comment | QOM paths `/machine/x4pro-gpio/-spi2/-i2c0/-ledc` and `/machine/usj` are gone; nothing references them |

To re-check: for each file in `git diff --name-only x4pro x4pro-on-upstream`, compare the two versions
as C token streams with comments and whitespace dropped and adjacent string literals merged (a 60-line
Python tokenizer did this in the session's scratch directory; it is not kept in the repo).

## 6. Proposed pull requests (separate, small, in this order)

Each PR: base `espressif/qemu:esp-develop`, one or two commits cherry-picked from `upstream-s3`.

### PR A — `hw/dma/esp_gdma: do not fetch the descriptor after the last one of a read` (05a97e4)

> ## Description
> `esp_gdma_read_channel()` fetches the next list node even when the requested bytes are complete. The
> last node has a NULL next pointer, so every transfer that ends on a node boundary reads a 12-byte
> descriptor from guest address 0 and logs three LOG_GUEST_ERROR lines — every DMA transaction of
> ESP-IDF's `spi_master` does this. Fetch the next node only when more bytes are needed, as the write
> path already does.
> ## Related
> Touches the same hunk as #176 (EOF descriptor address); the two are compatible, whichever lands second
> needs a trivial rebase.
> ## Testing
> ESP-IDF 6.0.1 application driving SPI2 through `spi_master` (with the GP-SPI model of PR F): before,
> three `guest_errors` lines per transaction; after, none. checkpatch clean.
> Found and analysed with the help of an AI coding assistant (Claude Code); the commit carries a
> Co-Authored-By trailer.

Risk: low.

### PR B — `hw/sd/dwc_sdmmc: store the configuration registers, trace instead of DEBUG` (34ca0cc)

> ESP-IDF's SDMMC driver touches SDMMC_CLOCK, CLKDIV, CLKENA, TMOUT, CTYPE, UHS_REG, BMOD, CARDTHRCTL,
> EMMC_DDR and CDETECT/WRTPRT on every card operation; all fell to the LOG_UNIMP default (483,175 lines in
> a 5-second boot with `-d unimp`). Store and read back the configuration-only registers, answer card
> detect / write protect from the SD bus, and replace the compiled-out DEBUG macro with trace events.
> Testing: an ESP-IDF 6.0.1 application mounting its FAT card (a board machine built on `esp32s3`, same
> sdmmc wiring): the same file operations as before, no LOG_UNIMP lines from the block;
> `-trace 'dwc_sdmmc_*'` shows the transfers. (Re-run on plain `-M esp32s3` with an SD image before
> opening; not done here.)

Risk: low; a reviewer may ask to split "store registers" from "trace events" (two commits).

### PR C — `hw/misc/esp_aes: OFB keeps the keystream in IV memory, CTR honours INC32` (5dfeaaf)

> Two DMA-AES results differed from the hardware's: OFB encryption left the last ciphertext block in IV
> memory instead of the last block-cipher output (so the second call of a long OFB stream was wrong), and
> CTR ran gcrypt's 128-bit counter where AES_INC_SEL 0 selects INC32 (ESP-IDF's GCM sets it), so the
> keystream carried into bit 32 when the low word wrapped. Testing: every block mode, both key sizes,
> both INC_SEL values and multi-call streams compared against a host AES implementation over qtest
> (30 cases, `-machine esp32s3`).

Risk: medium-low; maintainers will want that test in-tree — convert x4pro-emu's `tests/test_aes.py`
into `tests/qtest/esp32s3-aes-test.c` (not done).

### PR D — the cache: `CTRL1 is the bus-shut register` + `complete the DCache lock and occupy operations` (fb5c23e, aa35a01)

> 1. EXTMEM_DCACHE_CTRL1/ICACHE_CTRL1 hold SHUT_CORE0_BUS/SHUT_CORE1_BUS, not the enable bit. The ROM's
> `Cache_Resume_DCache()` sets CTRL.ENABLE and then clears both CTRL1 bits, so the caches read as disabled
> from the first resume on, `spi_flash_cache_enabled()` returned false and every ESP-IDF assert printed
> `<cached disabled>` instead of its strings.
> 2. DCACHE_LOCK_CTRL and DCACHE_OCCUPY_CTRL read as 0; ESP-IDF 6.x startup calls the ROM's
> `Cache_Occupy_Items()`, which polls their DONE bits, and hung before `app_main()`. Treat them like the
> other maintenance operations (enable bit cleared, DONE set on read).
> Testing: an ESP-IDF 6.0.1 application (on a board machine built on `esp32s3`) boots past startup, and
> a provoked assert prints
> `assert failed: spinlock_acquire spinlock.h:141 (lock->owner == core_owner_id)`.

Risk: low. (The board's own overlay answers DONE unconditionally; the model version uses
`check_and_reset_ena()` like its neighbours.)

### PR E — `target/xtensa: use TSO as the guest memory order` (fa10df2)

> PR body = the commit message (litmus numbers, the spinlock.h:141 failure on arm64 hosts, why TSO and
> not SC, the 10–13 % cost on a memory-bound loop on arm64, nothing on x86). Add: "Espressif ships
> aarch64-apple-darwin and aarch64-linux builds, which are the affected hosts."

Risk: medium. Max Filippov's line "Xtensa processors have a weak memory model" is deliberate; the change
affects every Xtensa CPU, not only the S3; an arm64 slowdown. A narrower alternative: a separate
`TCGCPUOps` (with `guest_default_memory_order`) for the esp32/esp32s3 core classes only. See section 7 for
QEMU proper.

### PR F — the peripheral models (93fb85f … 082c0e0, 6 commits)

> ## Description
> The ESP32-S3 machine stubs or lacks most peripherals a real board firmware touches. This adds models
> for the USB Serial/JTAG controller (CDC console half, chardev-backed), the GPIO block (pads, external
> drivers on named GPIO lines, edge/level interrupts), the two GP-SPI hosts (CPU and GDMA paths, transfers
> timed at the CLOCK register's rate), the two I2C controllers (master FIFO mode on an I2CBus) and the
> LEDC (duty, resolution, hardware fades in guest time), and instantiates them in the esp32s3 machine.
> One commit per model, then the machine commit.
> ## Testing
> On `esp-develop` @ febae182: checkpatch clean except the MAINTAINERS note for new files; builds
> without warnings. An Arduino-ESP32 3.x / ESP-IDF 5.5 e-reader firmware on `-M esp32s3`: console on the
> USB Serial/JTAG chardev, I2C NACKs on an empty bus, SPI2 transfers through the GDMA. The same models
> drive a full board emulation (e-paper panel on SPI2, touch/RTC/gauge on I2C0, buttons on GPIO, a
> frontlight on LEDC) whose end-to-end suite passes on them.
> ESP-IDF's `spi_master` also needs the interrupt-matrix fix in #185.
> Found and analysed with the help of an AI coding assistant (Claude Code).

Risks (high, by model):
- **GPIO:** a second S3 GPIO type (`esp32s3.gpio-full`) beside Espressif's `esp32s3.gpio`
  (an `esp32.gpio` subclass); they may want it to *be* `esp32s3.gpio`. The `strap_mode` -global change.
  The per-pin sample counters (`sample-gap-us`) are test-harness instrumentation — likely asked to drop.
  The `idle` mask stands in for IO_MUX pulls.
- **I2C:** `esp32_i2c.c` (the ESP32's) is already compiled for the S3; they may ask for one shared model.
  The `trace-i2c` file property is not QEMU style (trace events are); expect to convert it.
- **USJ:** the C3 and C6 machines use the same stub; they may want this model shared with them. The
  "SOF reads as set on every INT_RAW read" behaviour is a deliberate modelling shortcut.
- **GP-SPI:** the "≥ 1 ms transfers take their time, shorter ones are instant" rule will be questioned.
- **LEDC:** an `esp32_ledc.c` exists for the ESP32 (different block); counters/getters for a board.
- **No qtests.** Missing: register-level qtests per model (GPIO OUT/ENABLE/IN and an edge interrupt;
  I2C a write+read against an `i2c-echo`-style device; LEDC a fade's DUTY_R walk under `-icount`; USJ a
  write and flush to a chardev). #199 shows the pattern.
- Size: ~2,400 lines; the template asks for small PRs. Fallback: one PR per model, each with its own
  slice of the machine commit.

### Do not open — comment instead (owner's call)

- **intmatrix (1b7edb4):** #185 is a superset. A comment on #185: "independently hit by ESP-IDF 6.0.1
  `spi_master` (TRANS_DONE pending while the source is parked on 6); an equivalent fix has carried an
  e-reader emulation since 2026-09". Then drop our commit once #185 lands.
- **SPI dummy cycles (ce8e842):** identical to #177; a comment with the NVS symptom (pages judged corrupt
  on every boot) adds evidence.
- **GDMA → PSRAM (a6d1bd3):** comment on #181 offering the narrower variant (DRAM + DCache window only),
  or open it as an alternative if they prefer.

## 7. QEMU proper (qemu-devel) — the TSO change

The memory-order bug is in `target/xtensa`, upstream of Espressif; upstream master still has
`.guest_default_memory_order = 0` in `target/xtensa/cpu.c` (`xtensa_tcg_ops`; the 9.2 base has it as
`TCG_GUEST_DEFAULT_MO` in `cpu-param.h`). **The patch cannot go there as written:** QEMU's code-provenance
policy declines contributions that include or derive from AI-generated content, and this one does
(Co-Authored-By: Claude). Options for the owner:

1. **Report it, do not send a patch** — a GitLab issue (gitlab.com/qemu-project/qemu/-/issues) or a mail to
   qemu-devel, Cc Max Filippov <jcmvbkbc@gmail.com>, describing the failure, the litmus and the
   measurements, and asking whether he wants TSO for all Xtensa cores or per configuration. The policy
   permits AI use for debugging and analysis when its output is not contributed; say that the analysis
   was AI-assisted. The fix is one line he can write himself.
2. Ask qemu-devel for an exception (unlikely to be granted for a hardware model).
3. A patch written by the owner from scratch, without AI help — only if that is genuinely the case.

Draft issue (owner to rewrite in their own words):

> **target/xtensa: MTTCG on arm64 hosts reorders guest stores; ESP-IDF SMP spinlocks fail**
> `xtensa_tcg_ops.guest_default_memory_order` is 0, so on a weakly ordered host (arm64) TCG emits no
> barrier between two guest stores. ESP32-S3 firmware (ESP-IDF 5.x/6.x) depends on store→store order: the
> software compare-and-set for PSRAM spinlocks releases its lock with a plain store right after the store
> it protects, and `spinlock_release()` frees with a plain store. On an Apple-silicon host, two-core runs
> hit `assert failed: spinlock_acquire spinlock.h:141 (lock->owner == core_owner_id)` in about half of the
> boots under load; never on x86. A message-passing litmus on the two cores (no MEMW between data and flag
> stores) saw stale data 78–342 times per million rounds with MO 0 or LD_LD, never with ST_ST or TSO.
> TSO costs ~10 % on a memory-bound guest loop on arm64, nothing on x86. Would you take TSO for Xtensa, or
> prefer it per core configuration? (Analysis done with the help of an AI assistant.)

## 8. Exact steps awaiting the owner (nothing below has been run)

The owner approves each step; nothing is pushed until then.

```sh
cd /Users/mini/x4pro-emu/qemu/wt-up
git checkout upstream-s3

# 1. Put your own sign-off in place of the placeholder (all 15 commits)
git rebase febae182 --exec 'git log -1 --format=%B | sed "s/^Signed-off-by: TODO <owner>$/Signed-off-by: Your Name <you@example.org>/" | git commit --amend -q -F -'
#    and the copyright line in the five new files (then amend the commits that add them):
grep -rl 'TODO <owner>' hw include
for c in $(git rev-list --reverse febae182..HEAD); do git show --format=email $c | scripts/checkpatch.pl - | tail -1; done

# 2. Your fork (GitHub web or gh) and a remote
gh repo fork espressif/qemu --clone=false
git remote add fork git@github.com:<you>/qemu.git

# 3. One branch per PR, each on esp-develop
git branch pr-gdma-last-desc febae182 && git checkout pr-gdma-last-desc && git cherry-pick 05a97e4
git branch pr-sdmmc-regs    febae182 && git checkout pr-sdmmc-regs    && git cherry-pick 34ca0cc
git branch pr-aes-ofb-ctr   febae182 && git checkout pr-aes-ofb-ctr   && git cherry-pick 5dfeaaf
git branch pr-cache         febae182 && git checkout pr-cache         && git cherry-pick fb5c23e aa35a01
git branch pr-xtensa-tso    febae182 && git checkout pr-xtensa-tso    && git cherry-pick fa10df2
git branch pr-s3-periph     febae182 && git checkout pr-s3-periph     && git cherry-pick 93fb85f 6907df4 7230b0c 5fc3b16 c241152 082c0e0
#    (use the rewritten hashes after step 1: git log --oneline febae182..upstream-s3)
#    build each branch once: cd build && ninja qemu-system-xtensa

# 4. Push and open, one at a time, body from section 6 saved to a file
git push fork pr-gdma-last-desc
gh pr create -R espressif/qemu -B esp-develop -H <you>:pr-gdma-last-desc \
  --title "hw/dma/esp_gdma: do not fetch the descriptor after the last one of a read" --body-file prA.md
#    ... likewise for B, C, D, E, F; comments on #185, #177, #181 with gh pr comment <n> -R espressif/qemu
```

Afterwards, when Espressif merges (or rewrites) any of these, rebase `x4pro-on-upstream` on the new
`esp-develop`, regenerate `qemu-patches/` from it (`git format-patch -o ../qemu-patches <base>..x4pro-on-upstream`)
and point `make setup` at that base — a separate, reviewed change.

## 9. Lines for the coordinator's files

- `CLAUDE.md` / `docs/NEXT_PHASE.md` §10.3, S7 row: "prepared (2026-10-12): `upstream-s3` (15 commits, checkpatch
  clean, builds, AES qtests pass on `-M esp32s3`) and `x4pro-on-upstream` (3 board commits; tree = x4pro up to
  formatting, wiring and the cache DONE fix; smoke 37 passed). Not submitted: owner's sign-off and approval;
  #185/#177/#181 overlap. `docs/upstream-qemu.md`."
- `docs/log.md`: the overlap with open PRs #185 (supersedes our 0006), #177 (= 0009), #181 (vs 0019); QEMU's
  AI-content policy blocks the TSO patch on qemu-devel.
- Housekeeping: `qemu/wt-up` is a worktree with its own `build/` (~1.5 GB); remove with
  `git -C qemu worktree remove --force wt-up` when done (the branches stay).
