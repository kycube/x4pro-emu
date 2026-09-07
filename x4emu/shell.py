"""`x4emu shell`: a REPL that runs commands in-process through the same parser and command
functions as the CLI, so each line behaves exactly like `x4emu <line>` would from a fresh process —
same human output, or (when the shell itself was started with `--json`) the same JSON object per
line — without paying for a new Python process and QMP handshake on every command.

Reads one command per line from stdin (`shlex.split`); `exit`, `quit` or EOF end the session; a
failing line is reported (to stderr in human mode, as an `{"error": ...}` object in `--json` mode)
and the loop continues. `name NEW` switches the instance the following lines target. The prompt
(`x4emu NAME> `) is only printed when stdin is a TTY, so non-interactive use
(`printf 'status\\nstate\\n' | x4emu --name X shell`) produces nothing but each line's own output.
"""
import json
import shlex
import sys

from . import api
from .output import out


def cmd_shell(a):
    name = a.name
    interactive = sys.stdin.isatty()
    while True:
        if interactive:
            sys.stdout.write(f'x4emu {name}> ')
            sys.stdout.flush()
        line = sys.stdin.readline()
        if line == '':                # EOF
            break
        line = line.strip()
        if not line:
            continue
        try:
            parts = shlex.split(line)
        except ValueError as e:        # unbalanced quotes etc.
            print(f'{e}', file=sys.stderr)
            continue
        if not parts:
            continue
        if parts[0] in ('exit', 'quit'):
            break
        if parts[0] == 'name' and len(parts) == 2:
            name = parts[1]
            continue
        try:
            if a.json_out:
                result = api.run(parts, name=name)
                sys.stdout.write(json.dumps(result) + '\n')
            else:
                text = api.run_text(parts, name=name)
                if text:
                    sys.stdout.write(text)
                    if not text.endswith('\n'):
                        sys.stdout.write('\n')
        except RuntimeError as e:
            print(str(e), file=sys.stderr)
        except Exception as e:         # belt and braces: api.run/run_text should not raise these
            print(f'{type(e).__name__}: {e}', file=sys.stderr)
        sys.stdout.flush()
    # Each line already printed its own output via api.run/api.run_text (which save and restore
    # `out`'s state, including `_done`, around every call); mark the outer `out` as handled so
    # `cli._dispatch`'s own trailing `out.finish()` does not print a stray extra object.
    out._done = True
