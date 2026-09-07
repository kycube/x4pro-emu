"""In-process entry points for the CLI, for callers that want a result back instead of stdout: the
`x4emu shell` REPL and `tools/x4emu_mcp.py` (see docs/x4emu.md, "In-process API").

`run(argv, name=None)` mirrors `--json`: it returns the JSON object the command would have printed
(the shapes documented per-command in docs/x4emu.md), or on any failure — a bad argument, a
command's own `sys.exit`, an unexpected exception — an error object carrying whatever fields the
command had already gathered plus `"error"` and `"rc"` (the exit code the real CLI would have used).
It never raises `SystemExit` and never prints anything.

`run_text(argv, name=None)` mirrors plain human-mode output: it returns the text the command would
have printed to stdout. On failure it raises `RuntimeError` (never `SystemExit`) carrying that same
text plus the failure message, which is the contract `tools/x4emu_mcp.py`'s old subprocess helper
(`_x4`) had, so tools built on it did not need to change their error handling.

Both build the parser exactly as `cli.build_parser()` does and call the same command functions
`main()` calls; only the presentation differs, via `output.Out`'s `collect` mode (see
`output.suspended`, the pattern this borrows: swap `out`'s state, run the command, put it back).
"""
import contextlib
import io

from . import cli
from .output import out


def _snapshot():
    return (out.json_mode, out.collect, out.data, out._raw, out._done, out._text, out._collected)


def _restore(saved):
    (out.json_mode, out.collect, out.data, out._raw, out._done, out._text, out._collected) = saved


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


def _full_argv(argv, name):
    return (['--name', str(name)] if name is not None else []) + list(argv)


def run(argv, name=None):
    """Run one CLI command in-process; return its `--json` object. Never raises, never prints."""
    saved = _snapshot()
    try:
        out.configure(True, collect=True)
        a, err, code = _parse(_full_argv(argv, name))
        if err is not None:
            return {'error': err, 'rc': code}
        try:
            rc = a.fn(a)
        except SystemExit as e:
            out.error(e.code if isinstance(e.code, str) else f'exit {e.code}')
            payload = dict(out.collected() or {})
            payload['rc'] = 1
            return payload
        except Exception as e:
            out.error(f'{type(e).__name__}: {e}')
            payload = dict(out.collected() or {})
            payload['rc'] = 1
            return payload
        out.finish()
        return dict(out.collected() or {})
    finally:
        _restore(saved)


def _fail_text(msg):
    text = out.text()
    if not text:
        return msg
    return text + ('' if text.endswith('\n') else '\n') + msg


def run_text(argv, name=None):
    """Run one CLI command in-process; return the human text it would have printed. Raises
    `RuntimeError` (never `SystemExit`) if the command fails."""
    saved = _snapshot()
    try:
        out.configure(False, collect=True)
        a, err, code = _parse(_full_argv(argv, name))
        if err is not None:
            raise RuntimeError(err)
        try:
            a.fn(a)
        except SystemExit as e:
            raise RuntimeError(_fail_text(e.code if isinstance(e.code, str) else f'exit {e.code}')) from None
        except Exception as e:
            raise RuntimeError(_fail_text(f'{type(e).__name__}: {e}')) from None
        out.finish()
        return out.text()
    finally:
        _restore(saved)
