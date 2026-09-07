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
        self.collect = False
        self.data = {}
        self._raw = None
        self._done = False
        self._text = ''
        self._collected = None

    def configure(self, json_mode, collect=False):
        """`collect=True` (used by `x4emu/api.py`) gathers output instead of printing it: the
        human lines go to a buffer (`text()`) and the final JSON object goes to `collected()`,
        so a command can be run in-process without anything reaching real stdout."""
        self.json_mode = json_mode
        self.collect = collect
        self.data = {}
        self._raw = None
        self._done = False
        self._text = ''
        self._collected = None

    # -- human text: printed only when not in --json mode (or buffered when collecting)
    def line(self, text):
        if self.json_mode:
            return
        if self.collect:
            self._text += text + '\n'
        else:
            print(text)

    def write(self, text):
        if self.json_mode:
            return
        if self.collect:
            self._text += text
        else:
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
            payload = self.payload()
            if self.collect:
                self._collected = payload
            else:
                print(json.dumps(payload))

    def error(self, msg):
        """Print the failure as JSON (once), carrying any fields the command had already set."""
        if self.json_mode and not self._done:
            self._done = True
            body = dict(self.data) if self._raw is None else {}
            body['error'] = msg
            if self.collect:
                self._collected = body
            else:
                print(json.dumps(body))

    def collected(self):
        """The JSON object gathered while `collect` was on (see `x4emu/api.py:run`)."""
        return self._collected

    def text(self):
        """The human text gathered while `collect` was on (see `x4emu/api.py:run_text`)."""
        return self._text


out = Out()


@contextlib.contextmanager
def suspended():
    """Run a command's function without letting its output escape.

    `x4emu replay` calls the recorded command functions in this process: their human lines would
    bury the one-line-per-step output and their `out.set` fields would end up in the replay's own
    JSON object. Inside this block the human lines are dropped (as in `--json` mode) and the fields
    go to a scratch payload, which is yielded so the caller can look at it."""
    saved = (out.json_mode, out.collect, out.data, out._raw, out._done)
    out.json_mode, out.collect, out.data, out._raw, out._done = True, False, {}, None, False
    try:
        yield out.data
    finally:
        out.json_mode, out.collect, out.data, out._raw, out._done = saved
