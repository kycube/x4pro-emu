# x4pro-emu — see CLAUDE.md
PY      := .venv/bin/python
QEMU    := qemu/build/qemu-system-xtensa
NPROC   := $(shell sysctl -n hw.ncpu 2>/dev/null || nproc)
QEMU_REF ?= febae182e132e4055529be423a818225ebddaa3a

.PHONY: setup build test qemu firmware models clean-run run help rom-symbols

help:
	@echo "make setup   - venv, clone qemu (esp-develop @ $(QEMU_REF)) + apply patches, clone firmware"
	@echo "make build   - build qemu, models, CrossPoint x4pro firmware"
	@echo "make test    - model unit tests + pytest end-to-end (no device needed)"

setup: .venv/bin/python qemu/.x4pro-patched firmware/platformio.ini
	@echo "setup done"

.venv/bin/python:
	python3 -m venv .venv
	.venv/bin/pip install -q --upgrade pip
	.venv/bin/pip install -q esptool pyserial pillow pytest mcp capstone

qemu/.git:
	git clone --branch esp-develop --depth 1 https://github.com/espressif/qemu.git qemu
	cd qemu && git fetch --depth 1 origin $(QEMU_REF) && git checkout -q $(QEMU_REF) && git checkout -q -b x4pro

qemu/.x4pro-patched: qemu/.git
	cd qemu && git am --3way ../qemu-patches/*.patch
	touch $@

# The submodule pin (CrossPoint 1.6.0 + one commit), not upstream's default branch: the tests and the
# device oracles were made against it, and a plain clone would build whatever upstream is at today.
firmware/platformio.ini:
	git submodule update --init --recursive firmware

qemu: qemu/build/build.ninja
	cd qemu/build && ninja -j$(NPROC) qemu-system-xtensa

qemu/build/build.ninja:
	mkdir -p qemu/build && cd qemu/build && ../configure --target-list=xtensa-softmmu \
	  --enable-gcrypt --enable-slirp --enable-png --disable-user --disable-capstone \
	  --disable-vnc --disable-gtk --disable-sdl --disable-docs --disable-werror --disable-gnutls

# PlatformIO: pioarduino's core fork at the version CrossPoint's own CI uses (and ci.yml), in a venv of
# its own on a Python the pinned platform accepts. platform-espressif32 55.03.311 refuses Python 3.15+,
# which Homebrew's platformio formula moved to in October 2026 (the upstream watch's macos-build found
# it), so the build no longer depends on that formula. `make firmware PIO=pio` uses one on PATH instead.
PIO_CORE ?= https://github.com/pioarduino/platformio-core/archive/refs/tags/v6.1.19.zip
PIO_PYTHON ?= $(firstword $(foreach v,3.14 3.13 3.12 3.11 3.10,$(shell command -v python$(v) 2>/dev/null)))
PIO ?= .venv-pio/bin/pio

.venv-pio/bin/pio:
	@test -n "$(PIO_PYTHON)" || { echo "PlatformIO needs Python 3.10-3.14 on PATH (python3.14 ...); the pinned platform refuses newer"; exit 1; }
	$(PIO_PYTHON) -m venv .venv-pio
	.venv-pio/bin/pip install -q "$(PIO_CORE)"

firmware: $(filter .venv-pio/bin/pio,$(PIO))
	cd firmware && $(if $(filter .venv-pio/%,$(PIO)),$(CURDIR)/$(PIO),$(PIO)) run -e x4pro

models:
	$(MAKE) -C models

build: qemu models firmware

# PYTEST_ARGS: extra pytest flags (empty by default, so `make test` is unchanged).
# CI passes --basetemp=... so the PNGs the tests write land in a known directory.
PYTEST_ARGS ?=

test: models
	$(MAKE) -C models test
	$(PY) -m pytest -q $(PYTEST_ARGS) tests

# One-command edit-to-pixels loop: build the firmware, assemble the image, boot, wait, screenshot.
NAME ?= dev0
SD   ?= images/sd-device.img
run: firmware
	$(PY) tools/mkflash.py images/flash.bin --build firmware/.pio/build/x4pro
	-$(PY) tools/x4emu --name $(NAME) stop
	$(PY) tools/x4emu --name $(NAME) run --flash images/flash.bin $(if $(wildcard $(SD)),--sd $(SD),) --fast-epd
	$(PY) tools/x4emu --name $(NAME) wait-text "Entering activity" --timeout 90
	$(PY) tools/x4emu --name $(NAME) wait-quiet --seconds 2 --timeout 60
	$(PY) tools/x4emu --name $(NAME) screenshot images/last-run.png

clean-run:
	rm -rf .x4emu

# ROM symbol table for reading stock-firmware PCs (images/ is gitignored): esp-rom-elfs 20241011.
ROM_NM := images/rom/esp32s3_rev0_rom.nm
rom-symbols: $(ROM_NM)
$(ROM_NM):
	mkdir -p images/rom && cd images/rom && \
	  curl -sSL -o esp-rom-elfs-20241011.tar.gz https://github.com/espressif/esp-rom-elfs/releases/download/20241011/esp-rom-elfs-20241011.tar.gz && \
	  tar -xzf esp-rom-elfs-20241011.tar.gz esp32s3_rev0_rom.elf && \
	  $$(ls $$HOME/.platformio/packages/toolchain-xtensa-esp-elf/bin/xtensa-esp-elf-nm) -n esp32s3_rev0_rom.elf > esp32s3_rev0_rom.nm
