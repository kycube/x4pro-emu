"""Where the repository is, and where an instance keeps its files.

The CLI needs the checkout even when it is installed elsewhere (`pipx install .`): it runs
`qemu/build/qemu-system-xtensa`, generates the efuse image with `tools/mkefuse.py` from
`docs/device/efuse-dump.txt`, and keeps every instance under `.x4emu/<name>/`.

ROOT is resolved lazily (so `--help` works anywhere) in this order:

1. `$X4EMU_ROOT`;
2. the first directory at or above the current one that looks like the checkout;
3. the checkout containing this package (an editable install, or `tools/x4emu`);

and otherwise the command fails with a message naming the ways to fix it.
`$X4EMU_QEMU` overrides the emulator binary, `$X4EMU_NAME` the default instance name.
"""
import os
import sys

# What makes a directory the x4pro-emu checkout: the CLI shim and the QEMU patch series.
MARKERS = ('tools/x4emu', 'qemu-patches')

_root = None


def is_repo(d):
    """True if `d` looks like an x4pro-emu checkout."""
    return (os.path.isfile(os.path.join(d, 'tools', 'x4emu'))
            and os.path.isdir(os.path.join(d, 'qemu-patches')))


def set_root(d):
    """Pin ROOT (used by the in-repo `tools/x4emu` shim)."""
    global _root
    _root = os.path.abspath(d)
    return _root


def find_root():
    """Resolve the checkout, or exit with a message that says how to point us at one."""
    env = os.environ.get('X4EMU_ROOT')
    if env:
        d = os.path.abspath(os.path.expanduser(env))
        if not is_repo(d):
            sys.exit(f'X4EMU_ROOT={env} is not an x4pro-emu checkout '
                     f'(no {MARKERS[0]} and {MARKERS[1]}/ there)')
        return d
    d = os.path.abspath(os.getcwd())
    while True:
        if is_repo(d):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # editable install
    if is_repo(here):
        return here
    sys.exit('cannot find the x4pro-emu checkout: set X4EMU_ROOT=/path/to/x4pro-emu, or run from '
             'inside the checkout (a directory holding tools/x4emu and qemu-patches/)')


def root():
    """The checkout, resolved once per process."""
    global _root
    if _root is None:
        _root = find_root()
    return _root


def qemu():
    """The emulator binary ($X4EMU_QEMU, else qemu/build/qemu-system-xtensa in the checkout)."""
    return os.environ.get('X4EMU_QEMU') or os.path.join(root(), 'qemu', 'build', 'qemu-system-xtensa')


def idir(name):
    """The instance directory .x4emu/<name>/ (qmp.sock, console.log, uart0.log, pid, run.json)."""
    return os.path.join(root(), '.x4emu', name)


def default_name():
    return os.environ.get('X4EMU_NAME', 'dev0')
