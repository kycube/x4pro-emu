# The upstream watch

Everything this emulator runs on is pinned: CrossPoint Reader as the `firmware/` submodule, Espressif's
QEMU as `QEMU_REF` in the Makefile, PlatformIO through CrossPoint's `platformio.ini` and ci.yml's core
pin, and on the desk Mac whatever Homebrew has installed today. On 2026-10-12 a manual check after five
weeks found the three ways that goes stale (docs/log.md, "Upstream check after five weeks"):

- **CrossPoint moved on** (1.6.5, 93 commits past the pin). It built and booted; 17 of the 18 CrossPoint
  tests passed, and the one miss was upstream's own UI change ("Recent Books" became "Library") against
  a device-made golden. Upstream drift is mostly *goldens differ*, rarely *the emulator broke*.
- **Espressif QEMU** `esp-develop` had not moved, but when it does the 20-patch series has to follow.
- **Homebrew moved under the Mac's build**: `qemu/build` had been configured against a libgcrypt keg
  Homebrew had since removed, and a fresh configure failed on gnutls 3.8.13 (now `--disable-gnutls` in
  the Makefile). CI runs on Ubuntu and could not see either.

That check now runs by itself every Monday: `tools/upstream_check.py` for the quick answer, and
`.github/workflows/upstream.yml` for the builds behind it.

## What is watched

| What | Pinned here | Compared against | Who looks |
|---|---|---|---|
| CrossPoint Reader | gitlink `firmware` (`git ls-tree HEAD firmware`) | its latest GitHub release; its default branch (`develop`) for information | report, **crosspoint-latest** |
| freeink-sdk | the gitlink inside CrossPoint at the pin | the gitlink at that release | report |
| platform-espressif32 | `platform =` in CrossPoint's `platformio.ini` at the pin | the same line at the release; pioarduino's latest release | report |
| pioarduino core | `PIO_CORE_VERSION` in ci.yml | what CrossPoint's own CI installs at the release; the latest release | report |
| Espressif QEMU | `QEMU_REF` in the Makefile | `esp-develop` HEAD | report, **qemu-head** |
| Homebrew (desk Mac) | nothing: whatever is current | a fresh configure and build on a GitHub arm64 Mac | **macos-build** |
| This checkout (local runs only) | `qemu/build`, `.venv`, `~/.platformio/penv`, `firmware/` | the disk | report |

## What stays manual

**The stock firmware (`xteink_app`).** Its OTA packages are encrypted and reach the reader only through
the device's own update prompt, so nothing here can see a new version coming. When the device offers
one: back up first (`tools/device.py backup`, CLAUDE.md "The real device"), let the owner update, back up
again, and re-baseline the way the 7.6.8 entry in docs/log.md did (`tools/stockver.d/<version>.json`,
`docs/stock-<version>.md`, the patch sites, the string packs). The report repeats this line every week.

Also manual: moving the CrossPoint pin (below), and anything the device must confirm.

## Running the check locally

```
.venv/bin/python tools/upstream_check.py              # text
.venv/bin/python tools/upstream_check.py --markdown   # what CI writes into the step summary
.venv/bin/python tools/upstream_check.py --json       # one object: pins, crosspoint, qemu, platformio, local, findings, errors
```

No builds, about 10 s. It asks GitHub through `gh api` when `gh` is installed and authenticated (in CI:
`GH_TOKEN`), else through plain git: `git ls-remote`, and a treeless fetch (a few MB, into a temporary
directory that is removed afterwards) for commit counts and gitlinks; QEMU's history is fetched only 400
commits deep in that mode, so a larger gap reads "more than 400". A gh call that fails is asked again
through git (`notes` says which), so a sandbox where `gh` cannot reach the API still gets an answer.
`--backend gh|git` forces one. It runs on any Python from 3.9 up and imports nothing outside the
standard library (the report job uses the runner's `python3`, no venv).

Exit status: **0** whenever the report was written, whatever it says (it is a report, not a gate);
**2** when some remote could not be read (the report still prints, with a "Could not be read" list).
`--save F` keeps the JSON, `--load F` renders a saved one, `--github-output F` appends
`crosspoint_tag`, `crosspoint_behind`, `qemu_head`, `qemu_moved` (the workflow's job gates).

The local section only means something on the desk Mac:

- *qemu/build was configured against Homebrew kegs that are gone* — the 2026-10-12 libgcrypt case: the
  next link fails. `rm -rf qemu/build && make qemu` (a fresh configure).
- *.venv / ~/.platformio/penv: its base interpreter is gone* — a `python@3` bottle replaced the Cellar
  Python the venv was made from. `rm -rf .venv && make setup`; delete `~/.platformio/penv` and the next
  `pio run` rebuilds it.
- *firmware/ is checked out at X, not the pin* — someone left the submodule elsewhere (a scratch build
  of a newer release, say). `git submodule update --init --recursive firmware`.

## The workflow

`.github/workflows/upstream.yml`: Mondays 06:17 UTC, and **Actions → Upstream watch → Run workflow**
(`gh workflow run upstream.yml`, add `-f force=true` to run the two gated jobs even when nothing moved).
It only reads: `permissions: contents: read`, no commits, no issues. Each job writes its own section of
the run's summary page; screenshots and logs are in the run's artifacts.

| Job | Runs when | Typical time | Red means |
|---|---|---|---|
| `report` | always | < 1 min | a remote could not be read |
| `crosspoint-latest` | CrossPoint's latest release is not in the pin (or `force`) | ~12 min: QEMU from ci.yml's cache, the firmware ~2 min, the pytest run ~4 min (ci.yml's figures) | the emulator broke on the new firmware, or that firmware does not build |
| `qemu-head` | `esp-develop` moved past `QEMU_REF` (or `force`) | ~15 min: QEMU from scratch (ci.yml rebuilds it with the firmware in 7 min), then as above | the patch series conflicts, or the patched head does not build or fails `make test` |
| `macos-build` | always | ~20–30 min, an estimate until its first run: `brew update` + install, QEMU from scratch (54 s at `-j4` on the desk Mac, longer on the runner's 3 cores), PlatformIO packages (cached after the first run), the firmware | today's Homebrew breaks `make setup`, a fresh QEMU configure/build, the fast tests or a CrossPoint boot |

### Reading `report`

A table of pin against upstream, then findings, each with what to do:

- **drift** — something moved that the other jobs test: read *their* summaries before acting.
- **action** — something here should change (ci.yml's core pin no longer matches CrossPoint's CI at the
  release; a stale local build).
- **info** — worth knowing, nothing to do yet (develop's unreleased commits, freeink-sdk moving with the
  release, a newer pioarduino platform that CrossPoint has not adopted).
- **ok** — checked and current.

### Reading `crosspoint-latest`

It is ci.yml's build-and-test with `firmware/` cloned at the latest release tag (freeink-sdk at that tag's
own gitlink, the pioarduino core that tag's own CI installs), and pytest run with **`--golden-drift`**,
an opt-in option of `tests/conftest.py` (off by default; `make test` and ci.yml never pass it):

- A test listed in `GOLDEN_ORACLES` (today the two device-screenshot oracles in
  `tests/test_device_screenshot.py`) that fails **in one of its own comparisons against a device-made
  golden** — a bare assert in the test body whose message is a pixel diff, not an `x4emu` command — while
  its emulator still runs with **no unknown panel command and no crash marker** in its console, is
  reported as **xfail** and listed under *Golden drift — review, not a failure*.
- A test that passed while its emulator logged a crash marker (Guru Meditation, `abort()`, `assert
  failed:`, a backtrace, a heap or stack check) or counted an unknown panel command is turned into a
  **failure**.
- Everything else fails as it always would: boot not reaching Home, a timeout, a lost input, a console
  string upstream reworded. The failures are run once more (`--lf`); one that passes the second time is
  named as *passed only on the retry* (the input race, NEXT_PHASE §0 h) and does not turn the job red.

The verdict line says which it was. To act:

- **Emulator OK, N golden(s) drifted** — expected while the pin is behind. Look at the screenshots in
  the `crosspoint-latest-screenshots-and-logs` artifact: if the difference is upstream's UI, nothing to
  do. A drifted golden that reaches the oracle's *own* threshold check only after the first failing
  assert is not exercised further in that test (its later steps do not run); the other tests cover them.
- **Emulator-level failure** — read the failing test's console and qemu.log in the artifact
  (`logs/pytest-…/`). An unknown panel command or a crash in a firmware that boots on the device is an
  emulator gap: reproduce locally against a scratch build of that release (below) and fix the model.
  A test that no longer fits the firmware (a menu moved, a log line reworded) is a test to update when the
  pin moves, not an emulator bug: say so in the log entry.
- **"does not build here"** — an upstream build change (a new platform or core, a renamed env). Not the
  emulator; check CrossPoint's own CI at the tag, and ci.yml's `PIO_CORE` when the pin moves.

**Moving the pin** is a decision, not a reaction to this job: the device oracles (home, reader pages,
`tests/golden/device-*.bmp`) were captured from CrossPoint 1.6.0 on the device, so a bump needs new
device captures, which need CrossPoint on the device (CLAUDE.md's device rules), then
`git -C firmware checkout <tag> && git add firmware`, ci.yml's `PIO_CORE` if CrossPoint's CI moved, and a
`make test`.

Locally, against a release without moving the submodule (what the 2026-10-12 check and this page's
verification did): clone the tag elsewhere (`git clone --depth 1 --branch TAG --recurse-submodules
--shallow-submodules https://github.com/crosspoint-reader/crosspoint-reader.git /path/fwTAG`), `pio run
-e x4pro` there, and run pytest with a one-line plugin that sets `sys.modules['conftest'].BUILD` to
`/path/fwTAG/.pio/build/x4pro` in `pytest_configure`, plus `--golden-drift=FILE` and
`X4EMU_TEST_PREFIX=…` so the instances do not collide with anyone else's.

### Reading `qemu-head`

A row per patch: *applies*, *applied with a 3-way merge*, or **conflicts** in the files named (the
series stops there; later patches are not tried). Then the configure/build, the pinned CrossPoint
build and `make test` against the new QEMU, whose failures are all the new base's: the firmware and
goldens are the pinned ones. To act on a moved head:

```
cd qemu && git fetch origin esp-develop
git rebase --onto FETCH_HEAD <old QEMU_REF> x4pro          # resolve what the summary listed
git format-patch -o ../qemu-patches FETCH_HEAD..x4pro       # after removing the old ../qemu-patches/*.patch
cd .. && sed -i '' 's/^QEMU_REF ?= .*/QEMU_REF ?= <new head>/' Makefile
rm -rf qemu/build && make qemu && make test
```

`QEMU_REF` and the series move in one commit (ci.yml's cache key hashes both). A local `qemu/` that was
cloned `--depth 1` may need `git fetch --unshallow` before the rebase.

### Reading `macos-build`

The desk Mac's setup on a clean GitHub arm64 Mac: `brew update`, then CLAUDE.md's packages plus the two
QEMU's configure finds on the desk Mac by itself (gnutls, which the Makefile's `--disable-gnutls` must
keep out, and libusb) and python@3.14 for PlatformIO's venv; `make setup`; **a fresh QEMU configure** with the
Makefile's own line (nothing of QEMU is cached); the models and their tests; pytest before any firmware
exists, so only the fast cases run (the CrossPoint and stock ones skip themselves, `tests/test_aes.py`
runs the emulator through qtest); the pinned CrossPoint built with the Makefile's PlatformIO venv (`.venv-pio`); one boot to
Home (`tests/test_boot.py::test_boot_to_home_and_press_right`). The summary lists the formula versions
and every step's outcome; a failed configure appends the tail of `meson-log.txt` (the whole file and
`config.log` are in the `macos-build-logs` artifact).

To act on a red run: reproduce on the desk Mac with `brew upgrade`, then `rm -rf qemu/build && make
qemu`; the fix usually goes into the Makefile's configure line (a `--disable-<feature>` for an optional
dependency QEMU auto-detects), as `--disable-gnutls` did. A green run with a red desk Mac points at the
local section of the report (stale kegs, a broken venv), not at Homebrew.

Only the PlatformIO packages are cached (`~/.platformio` without `penv`, keyed by the venv's PlatformIO
version and `platformio.ini`): `penv`'s `pyvenv.cfg` names a Cellar Python that the next `python@3`
bottle removes.

## Files

- `tools/upstream_check.py` — the report (`report`, the default) and `tests-summary` (JUnit XML, the
  `--golden-drift` file and the emulator consoles → Markdown; exit 1 on a failure or a crash marker).
  On the desk Mac pass `--instance-prefix pytest-<your prefix>` with `--logs .x4emu`: the directory
  holds every instance anyone ran, crashed experiments included.
- `tests/test_upstream_check.py` — the parsing, findings, rendering and summary with the network stubbed
  (the 2026-10-12 state as the fixture), and a guard that `GOLDEN_ORACLES` still names real tests.
- `tests/conftest.py` — `--golden-drift FILE`, `GOLDEN_ORACLES`, the health check.
- `.github/workflows/upstream.yml` — the four jobs; its shared parts are copies of ci.yml's (keep them in
  step).

**Found on its first run (2026-10-13).** Homebrew's `platformio` formula now runs on Python 3.15, and
the pinned pioarduino platform (55.03.311) refuses anything past 3.14 ("Python version must be 3.10 …
3.14"). The Makefile therefore keeps PlatformIO in its own `.venv-pio` (pioarduino's core v6.1.19, the
version CrossPoint's CI uses) on the newest Python 3.10–3.14 it finds; Homebrew's `platformio` is no
longer needed. CI and the two Ubuntu jobs pass `PIO=pio` to keep using the core they install.
