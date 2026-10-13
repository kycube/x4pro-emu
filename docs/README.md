# Documentation

Start with [`../CLAUDE.md`](../CLAUDE.md) (build and test, the stock boot recipe, the device rules);
the [project README](../README.md) is the overview.

## The emulator

| document | what it covers |
|---|---|
| [x4emu.md](x4emu.md) | the `x4emu` CLI: every command, its JSON shape, installing it with pipx |
| [hardware.md](hardware.md) | the X4 Pro as the emulator models it: pins, buses, panel variants, with sources; the full list of what is not emulated; the keys of `x4emu state` |
| [grayscale.md](grayscale.md) | the waveform-level grey model of the UC8279 and how it was calibrated |
| [upstream-qemu.md](upstream-qemu.md) | contributing the generic ESP32-S3 work to Espressif's QEMU: the split, the PR drafts, QEMU's AI-content policy |
| [heap.md](heap.md) | the stock 7.2.4 `CORRUPT HEAP`: a race between two of its own tasks over a font file, not the emulator's |
| [smp.md](smp.md) | the two emulated cores and the memory order: the `spinlock.h:141` assert, TSO, the cache bits |
| [parity.md](parity.md) | the device-parity harness (`tools/parity.py`): step scripts, the owner's runbook, the capture comparison |
| [audit.md](audit.md) | every claim of the original build brief, confirmed or corrected, with evidence |

## The stock firmware (`xteink_app`)

| document | what it covers |
|---|---|
| [stock-firmware.md](stock-firmware.md) | the UI layer mapped for modification (a 7.2.4 survey: its addresses are 7.2.4's) |
| [stock-7.5.4.md](stock-7.5.4.md) · [stock-7.6.8.md](stock-7.6.8.md) | what moved in each update, and the re-located patch sites |
| [lua-apps.md](lua-apps.md) | the Lua app host: the container, the API as confirmed at runtime, its measured limits |
| [gamebook.md](gamebook.md) | gamebooks on the Lua host: writing a story, drawing plates, how the engine works |
| [xic.md](xic.md) | the `.xic` screen-capture format and the developer menu that writes it |
| [fonts.md](fonts.md) · [reading-fonts.md](reading-fonts.md) | the system font decision, and adding reading fonts to the stock's list |

## Process

| document | what it covers |
|---|---|
| [log.md](log.md) | the engineering log, newest at the bottom: what was done, what failed, and why |
| [NEXT_PHASE.md](NEXT_PHASE.md) | the handoff for the next working session: where things stand, the plan, how work is split |
| [upstream.md](upstream.md) | the weekly upstream watch (`.github/workflows/upstream.yml`): what it checks and how to act on each finding |

## Tools (`tools/`)

Every tool's module docstring (`--help`) is its manual; the page named here has the background.

| tool | what it does | see |
|---|---|---|
| `x4emu` (shim), `x4emu_mcp.py` | the emulator CLI and its MCP server | [x4emu.md](x4emu.md) |
| `mkflash.py`, `mksd.py`, `mkefuse.py` | a 16 MB flash image (from a build or a raw dump), a FAT32 card image, the efuse file | [x4emu.md](x4emu.md) |
| `device.py` | the desk reader: backup, slots, console, the guarded slot writers | `../CLAUDE.md`, "The real device" |
| `nvsedit.py` | read and edit NVS in a flash image; `redact` blanks the WiFi credentials | [hardware.md](hardware.md) |
| `stockver.py` (+ `stockver.d/`) | the per-version offsets the stock tools read; `identify IMAGE` | [stock-7.5.4.md](stock-7.5.4.md), [stock-7.6.8.md](stock-7.6.8.md) |
| `stockdev.py`, `stockpatch.py` | the developer menu; named byte patches (`developer-menu`, `hidden-menu-rows`, `lua-apps-row`) | [xic.md](xic.md), [lua-apps.md](lua-apps.md) |
| `stockstrings.py` | read and relabel the stock's UI string packs | [stock-firmware.md](stock-firmware.md) §6 |
| `xic2png.py` | decode and encode the stock's `.xic` captures | [xic.md](xic.md) |
| `xtfont.py`, `fontcompare.py` | build and install `.xtf` system fonts; compare candidates at the device's cells | [fonts.md](fonts.md), [reading-fonts.md](reading-fonts.md) |
| `xtapp.py` | pack, inspect and install Lua apps | [lua-apps.md](lua-apps.md) |
| `gamebook.py` (+ `gamebook_engine.lua`, `pixart.py`) | gamebooks for the Lua host | [gamebook.md](gamebook.md) |
| `parity.py` | device parity: the step script on the emulator, the owner's runbook, the comparison | [parity.md](parity.md) |
| `ghidra_stock.py` (+ `ghidra/`) | Ghidra over a stock image: `analyze` once, then xrefs, callers, decompile | [stock-firmware.md](stock-firmware.md) §9 |
| `appdis.py`, `tcbwalk.py`, `epdtrace.py` | disassemble an app at a PC; walk the FreeRTOS tasks of a DRAM dump; diff panel command streams | [NEXT_PHASE.md](NEXT_PHASE.md) §7 |
| `upstream_check.py` | what moved upstream since the pins | [upstream.md](upstream.md) |
| `readme_images.py` | rebuild the README's pictures from the goldens | `../README.md` |

## Folders

- [`device/`](device/) — captures from the real reader: boot, sleep and wake logs, the efuse dump, the
  partition table, screenshots and a photo of the panel, flash backup checksums. The flash dumps
  themselves never leave the gitignored `images/` (they hold the owner's WiFi credentials).
- [`device/idf-headers/`](device/idf-headers/) — the ESP-IDF register headers and ROM linker scripts the
  models were written against.
- [`emu/`](emu/) — emulator-side logs kept for comparison with `device/`.
- [`images/`](images/) — the README's pictures, rebuilt from the test goldens by
  `tools/readme_images.py`.

Outside `docs/`: [`../fonts/README.md`](../fonts/README.md) — the local-only typeface directory and why its
binaries are never committed.
