"""In-process entry points for the CLI, for callers that want a result back instead of stdout: the
`x4emu shell` REPL, `tools/x4emu_mcp.py` and `tools/parity.py` (see docs/x4emu.md, "In-process API").

`run(argv, name=None)` mirrors `--json`: it returns the JSON object the command would have printed
(the shapes documented per-command in docs/x4emu.md), or on any failure — a bad argument, a
command's own `sys.exit`, an unexpected exception — an error object carrying whatever fields the
command had already gathered plus `"error"` and `"rc"` (the exit code the real CLI would have used).
`"rc"` is there whenever that exit code is not 0: also, without `"error"`, for a result the CLI
reports through its exit code alone (`screenshot --diff` with differing pixels: `"rc": 1`). It never
raises `SystemExit` and never prints anything.

`run_text(argv, name=None)` mirrors plain human-mode output: it returns the text the command would
have printed to stdout (whatever its exit code). On failure it raises `RuntimeError` (never
`SystemExit`) carrying that same text plus the failure message.

Both build the parser exactly as `cli.build_parser()` does and call the same command functions
`main()` calls; only the presentation differs, via `output.Out`'s `collect` mode, and `out` is put
back as it was afterwards (`output.preserved`).
"""
import contextlib
import io

from . import cli
from .output import out, preserved


def _parse(full_argv):
    """`build_parser().parse_args`, with argparse's own usage errors (which print to stderr and
    call `sys.exit(2)`) turned into `(None, message, code)` instead of raised."""
    stderr = io.StringIO()
    try:
        with contextlib.redirect_stderr(stderr):
            return cli.build_parser().parse_args(full_argv), None, None
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 2
        return None, (stderr.getvalue().strip() or f'usage error (exit {code})'), code


def _call(argv, name, json_mode):
    """Run one command with `out` collecting. Returns (the failure message or None, the exit code
    the CLI would have used)."""
    out.configure(json_mode, collect=True)
    a, err, code = _parse((['--name', str(name)] if name is not None else []) + list(argv))
    if err is not None:
        return err, code
    try:
        rc = a.fn(a)
    except SystemExit as e:
        return (e.code if isinstance(e.code, str) else f'exit {e.code}'), 1
    except Exception as e:
        return f'{type(e).__name__}: {e}', 1
    out.finish()
    return None, rc or 0


def run(argv, name=None):
    """Run one CLI command in-process; return its `--json` object (plus `"rc"` when the exit code
    would not be 0). Never raises, never prints."""
    with preserved():
        err, rc = _call(argv, name, True)
        if err is not None:
            out.error(err)
        result = dict(out.collected() or {})
        if err is not None or rc:
            result['rc'] = rc
        return result


def run_text(argv, name=None):
    """Run one CLI command in-process; return the human text it would have printed. Raises
    `RuntimeError` (never `SystemExit`) if the command fails."""
    with preserved():
        err, _ = _call(argv, name, False)
        text = out.text()
        if err is not None:
            raise RuntimeError(text + ('' if text.endswith('\n') else '\n') + err if text else err)
        return text
