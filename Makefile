# x4pro-emu — see CLAUDE.md
PY      := .venv/bin/python
QEMU    := qemu/build/qemu-system-xtensa
NPROC   := $(shell sysctl -n hw.ncpu 2>/dev/null || nproc)
QEMU_REF ?= febae182e132e4055529be423a818225ebddaa3a

.PHONY: setup build test test-serial test-durations qemu firmware models clean-run run help rom-symbols

help:
	@echo "make setup   - venv, clone qemu (esp-develop @ $(QEMU_REF)) + apply patches, clone firmware"
	@echo "make build   - build qemu, models, CrossPoint x4pro firmware"
	@echo "make test    - model unit tests + pytest end-to-end on TEST_JOBS workers (no device needed)"
	@echo "make test-serial    - the same, one test at a time"
	@echo "make test-durations - re-measure tests/durations.json (the parallel run's order)"

setup: .venv/bin/python qemu/.x4pro-patched firmware/platformio.ini
	@echo "setup done"

.venv/bin/python:
	python3 -m venv .venv
	.venv/bin/pip install -q --upgrade pip
	.venv/bin/pip install -q esptool pyserial pillow pytest pytest-xdist mcp capstone

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

# PYTEST_ARGS: extra pytest flags (empty by default). CI passes --basetemp=... so the PNGs the tests
# write land in a known directory.
PYTEST_ARGS ?=

# `make test` runs pytest on TEST_JOBS pytest-xdist workers: every test boots its own emulator under
# its own instance name (tests/conftest.py adds the worker to X4EMU_TEST_PREFIX), --maxschedchunk=1
# hands the tests out one at a time, and tests/conftest.py orders them longest first from
# tests/durations.json, so the long stock tests start at once. TEST_JOBS defaults to the host's CPU
# count, at most TEST_JOBS_MAX (each emulator runs two vCPU threads, but mostly idles in WAITI): 4 on
# CI's 4-vCPU runner, TEST_JOBS_MAX on the desk Mac. `make test TEST_JOBS=1` and `make test-serial`
# run one test at a time; without pytest-xdist in .venv the tests run one at a time as well.
TEST_JOBS_MAX ?= 8
TEST_JOBS ?= $(shell n=$(NPROC); [ "$$n" -gt $(TEST_JOBS_MAX) ] && n=$(TEST_JOBS_MAX); echo $$n)
XDIST_ARGS = $(shell [ "$(TEST_JOBS)" -gt 1 ] && $(PY) -c 'import xdist' 2>/dev/null && echo --numprocesses=$(TEST_JOBS) --maxschedchunk=1)

test: models
	$(MAKE) -C models test
	$(if $(XDIST_ARGS),,@[ "$(TEST_JOBS)" -le 1 ] || echo "pytest-xdist is not in .venv (.venv/bin/pip install pytest-xdist): the tests run one at a time")
	$(PY) -m pytest -q $(XDIST_ARGS) $(PYTEST_ARGS) tests

test-serial: models
	$(MAKE) -C models test
	$(PY) -m pytest -q $(PYTEST_ARGS) tests

# Re-measure every test's duration into tests/durations.json (merged: tests that did not run keep
# their old value). Run it on an otherwise idle machine after adding or reshaping a long test.
test-durations:
	$(PY) -m pytest -q $(XDIST_ARGS) --record-durations=tests/durations.json $(PYTEST_ARGS) tests

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
