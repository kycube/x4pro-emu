"""x4emu — drive the Xteink X4 Pro emulator (QEMU, ESP32-S3) from a shell.

`x4emu.cli:main` is the console script; `x4emu.paths` finds the checkout the emulator needs.
Documentation: docs/x4emu.md in the x4pro-emu repository.
"""
__version__ = '0.1.0'

from .cli import main

__all__ = ['main', '__version__']
