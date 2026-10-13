# Documentation

Start with [`../CLAUDE.md`](../CLAUDE.md) (build, the CLI, the stock boot recipe, the device rules);
the [project README](../README.md) is the overview.

## The emulator

| document | what it covers |
|---|---|
| [x4emu.md](x4emu.md) | the `x4emu` CLI: every command, its JSON shape, installing it with pipx |
| [hardware.md](hardware.md) | the X4 Pro as the emulator models it: pins, buses, panel variants, with sources |
| [grayscale.md](grayscale.md) | the waveform-level grey model of the UC8279 and how it was calibrated |
| [smp.md](smp.md) | the two emulated cores and the memory order: the `spinlock.h:141` assert, TSO, the cache bits |
| [audit.md](audit.md) | every claim of the original build brief, confirmed or corrected, with evidence |

## The stock firmware (`xteink_app`)

| document | what it covers |
|---|---|
| [stock-firmware.md](stock-firmware.md) | the UI layer of 7.2.4 mapped for modification (addresses are 7.2.4's) |
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

## Folders

- [`device/`](device/) — captures from the real reader: boot, sleep and wake logs, the efuse dump, the
  partition table, screenshots and a photo of the panel, flash backup checksums. The flash dumps
  themselves never leave the gitignored `images/` (they hold the owner's WiFi credentials).
- [`device/idf-headers/`](device/idf-headers/) — the ESP-IDF register headers and ROM linker scripts the
  models were written against.
- [`emu/`](emu/) — emulator-side logs kept for comparison with `device/`.
- [`images/`](images/) — the README's pictures, rebuilt from the test goldens by
  `tools/readme_images.py`.
