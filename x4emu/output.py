"""One place that decides whether a command talks to a human or to a program.

Without `--json` every command prints exactly what it always printed (tests and the MCP server
parse some of those lines). With `--json` the human lines are dropped and one JSON object is
printed on stdout instead — including for failures, which become `{"error": "..."}` (merged with
whatever the command had already gathered) and keep the exit code they had before.
"""
import contextlib
import json
import sys


class Out:
    def __init__(self, json_mode=False):
        self.json_mode = json_mode
        self.data = {}
        self._raw = None
        self._done = False

    def configure(self, json_mode):
        self.json_mode = json_mode
        self.data = {}
        self._raw = None
        self._done = False

    # -- human text: printed only when not in --json mode
    def line(self, text):
        if not self.json_mode:
            print(text)

    def write(self, text):
        if not self.json_mode:
            sys.stdout.write(text)
            sys.stdout.flush()

    # -- machine fields: kept only for --json mode
    def set(self, **fields):
        self.data.update(fields)

    def obj(self, value):
        """Use `value` as the whole JSON payload (state, battery, light: existing shapes)."""
        self._raw = value

    def payload(self):
        return self.data if self._raw is None else self._raw

    def finish(self):
        if self.json_mode and not self._done:
            self._done = True
            print(json.dumps(self.payload()))

    def error(self, msg):
        """Print the failure as JSON (once), carrying any fields the command had already set."""
        if self.json_mode and not self._done:
            self._done = True
            body = dict(self.data) if self._raw is None else {}
            body['error'] = msg
            print(json.dumps(body))


out = Out()


@contextlib.contextmanager
def suspended():
    """Run a command's function without letting its output escape.

    `x4emu replay` calls the recorded command functions in this process: their human lines would
    bury the one-line-per-step output and their `out.set` fields would end up in the replay's own
    JSON object. Inside this block the human lines are dropped (as in `--json` mode) and the fields
    go to a scratch payload, which is yielded so the caller can look at it."""
    saved = (out.json_mode, out.data, out._raw, out._done)
    out.json_mode, out.data, out._raw, out._done = True, {}, None, False
    try:
        yield out.data
    finally:
        out.json_mode, out.data, out._raw, out._done = saved
