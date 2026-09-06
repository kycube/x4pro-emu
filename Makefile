# x4pro-emu — see CLAUDE.md
PY      := .venv/bin/python
QEMU    := qemu/build/qemu-system-xtensa
NPROC   := $(shell sysctl -n hw.ncpu 2>/dev/null || nproc)
QEMU_REF ?= febae182e132e4055529be423a818225ebddaa3a

.PHONY: setup build test qemu firmware models clean-run help

help:
	@echo "make setup   - venv, clone qemu (esp-develop @ $(QEMU_REF)) + apply patches, clone firmware"
	@echo "make build   - build qemu, models, CrossPoint x4pro firmware"
	@echo "make test    - model unit tests + pytest end-to-end (no device needed)"

setup: .venv/bin/python qemu/.x4pro-patched firmware/platformio.ini
	@echo "setup done"

.venv/bin/python:
	python3 -m venv .venv
	.venv/bin/pip install -q --upgrade pip
	.venv/bin/pip install -q esptool pyserial pillow pytest

qemu/.git:
	git clone --branch esp-develop --depth 1 https://github.com/espressif/qemu.git qemu
	cd qemu && git fetch --depth 1 origin $(QEMU_REF) && git checkout -q $(QEMU_REF) && git checkout -q -b x4pro

qemu/.x4pro-patched: qemu/.git
	cd qemu && git am --3way ../qemu-patches/*.patch
	touch $@

firmware/platformio.ini:
	git clone --recurse-submodules https://github.com/crosspoint-reader/crosspoint-reader.git firmware

qemu: qemu/build/build.ninja
	cd qemu/build && ninja -j$(NPROC) qemu-system-xtensa

qemu/build/build.ninja:
	mkdir -p qemu/build && cd qemu/build && ../configure --target-list=xtensa-softmmu \
	  --enable-gcrypt --enable-slirp --enable-png --disable-user --disable-capstone \
	  --disable-vnc --disable-gtk --disable-sdl --disable-docs --disable-werror

firmware:
	cd firmware && pio run -e x4pro

models:
	$(MAKE) -C models

build: qemu models firmware

test: models
	$(MAKE) -C models test
	$(PY) -m pytest -q tests

clean-run:
	rm -rf .x4emu
