#!/usr/bin/env bash
# The shell steps ci.yml and upstream.yml share: one copy here instead of one per job. Each job
# calls `bash .github/scripts/ci.sh STEP ...` from the repository root after actions/checkout.
# Written for bash 3.2 as well (the macOS runner's /bin/bash), so it also runs on the desk Mac.
#
#   apt-deps                  the Ubuntu packages make setup / build / test need
#   git-identity              the committer `git am` (make setup applies qemu-patches/) insists on
#   pins                      qemu_ref=... and pio_core=... out of the Makefile, for $GITHUB_OUTPUT
#   pio-core VERSION          pioarduino's PlatformIO core VERSION and the imports its builder needs,
#                             into the job's python (`make firmware PIO=pio` then uses it)
#   pytest-retry DIR ARG...   pytest ARG... tests into DIR/run1 + DIR/junit-1.xml; if tests failed
#                             (exit 1), the failures once more (--lf) into DIR/run2 + DIR/junit-2.xml;
#                             exits with the last run's status. tools/upstream_check.py tests-summary
#                             names a test that passed only on the retry: a flake to look at
#   collect DEST [--qemu-build-logs] PATTERN...
#                             files matching PATTERN under $CI_TMP (pytest's basetemp) into
#                             DEST/screenshots/, every .x4emu/<instance>/ log into DEST/logs/, and
#                             with --qemu-build-logs QEMU's meson-log.txt and config.log into
#                             DEST/qemu-build/, for actions/upload-artifact
set -euo pipefail

apt_deps() {
  sudo apt-get update
  # CLAUDE.md's "Build" list, in Ubuntu 24.04 names. glib/pixman are QEMU's hard requirements;
  # gcrypt/slirp/png back --enable-gcrypt/--enable-slirp/--enable-png in the Makefile's configure
  # line; mtools + dosfstools are what tools/mksd.py shells out to (mkfs.vfat --offset needs
  # dosfstools >= 4.2, which noble has).
  sudo apt-get install -y --no-install-recommends \
    build-essential git ninja-build meson pkg-config python3-venv \
    libglib2.0-dev libpixman-1-dev libgcrypt20-dev libslirp-dev libpng-dev \
    mtools dosfstools
}

git_identity() {
  git config --global user.name  "x4pro-emu CI"
  git config --global user.email "ci@x4pro-emu.invalid"
}

pins() {
  local qemu_ref pio_core
  qemu_ref=$(sed -n 's/^QEMU_REF *?*= *//p' Makefile | head -1)
  pio_core=$(sed -n 's/^PIO_CORE_VERSION *?*= *//p' Makefile | head -1)
  if [ -z "$qemu_ref" ] || [ -z "$pio_core" ]; then
    echo "ci.sh pins: QEMU_REF or PIO_CORE_VERSION not found in the Makefile" >&2
    exit 1
  fi
  echo "qemu_ref=$qemu_ref"
  echo "pio_core=$pio_core"
}

pio_core() {
  local version=${1:?usage: ci.sh pio-core VERSION}
  python -m pip install --upgrade pip
  python -m pip install -U "https://github.com/pioarduino/platformio-core/archive/refs/tags/$version.zip"
  # platform-espressif32's builder imports these unconditionally and its own penv bootstrap does
  # not install them reliably on the hosted runners (same pins as the CrossPoint repo's CI).
  python -m pip install -U "littlefs-python>=0.16.0" "fatfs-ng>=0.1.14" "pyyaml>=6.0.2" \
    "rich-click>=1.8.6" "zopfli>=0.2.2" "intelhex>=2.3.0" "rich>=14.0.0" "urllib3<2" \
    "cryptography>=45.0.3" "certifi>=2025.8.3" "ecdsa>=0.19.1" "bitstring>=4.3.1" \
    "reedsolo>=1.5.3,<1.8" "esp-idf-size>=2.0.0" "esp-coredump>=1.14.0"
  pio --version
}

pytest_retry() {
  local dir=${1:?usage: ci.sh pytest-retry DIR PYTEST-ARG...} rc=0
  shift
  mkdir -p "$dir"
  .venv/bin/python -m pytest "$@" --basetemp="$dir/run1" --junitxml="$dir/junit-1.xml" tests || rc=$?
  if [ "$rc" -eq 1 ]; then
    echo "::warning::tests failed; repeating the failures once"
    rc=0
    .venv/bin/python -m pytest "$@" --lf --basetemp="$dir/run2" --junitxml="$dir/junit-2.xml" tests || rc=$?
  fi
  return "$rc"
}

collect() {
  local dest=${1:?usage: ci.sh collect DEST [--qemu-build-logs] PATTERN...} qemu_logs=0 f d n p
  shift
  if [ "${1:-}" = --qemu-build-logs ]; then qemu_logs=1; shift; fi
  mkdir -p "$dest/logs"
  if [ "$qemu_logs" = 1 ]; then
    mkdir -p "$dest/qemu-build"
    for f in qemu/build/meson-logs/meson-log.txt qemu/build/config.log; do
      if [ -f "$f" ]; then cp "$f" "$dest/qemu-build/"; fi
    done
  fi
  # By pattern, not wholesale: the same directories hold the 16 MB flash and 64 MB SD images each
  # test builds. find + cp rather than rsync, which macOS replaced with openrsync.
  if [ "$#" -gt 0 ] && [ -n "${CI_TMP:-}" ] && [ -d "$CI_TMP" ]; then
    local names=(-name "$1")
    shift
    for p in "$@"; do names=("${names[@]}" -o -name "$p"); done
    (cd "$CI_TMP" && find . -type f \( "${names[@]}" \) -print) | while IFS= read -r f; do
      mkdir -p "$dest/screenshots/$(dirname "$f")"
      cp "$CI_TMP/$f" "$dest/screenshots/$f"
    done
  fi
  # One .x4emu/<instance>/ per test; the console and QEMU logs are what a failure is read from.
  # Copied out of the dot-directory so upload-artifact does not treat them as hidden.
  for d in .x4emu/*/; do
    [ -d "$d" ] || continue
    n=$(basename "$d")
    mkdir -p "$dest/logs/$n"
    for f in console.log uart0.log qemu.log run.json; do
      if [ -f "$d$f" ]; then cp "$d$f" "$dest/logs/$n/$f"; fi
    done
  done
  du -sh "$dest" || true
}

step=${1:?usage: ci.sh apt-deps|git-identity|pins|pio-core|pytest-retry|collect ...}
shift
case "$step" in
  apt-deps)     apt_deps ;;
  git-identity) git_identity ;;
  pins)         pins ;;
  pio-core)     pio_core "$@" ;;
  pytest-retry) pytest_retry "$@" ;;
  collect)      collect "$@" ;;
  *)            echo "ci.sh: unknown step '$step'" >&2; exit 2 ;;
esac
