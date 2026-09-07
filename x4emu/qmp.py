"""Minimal QMP client over the instance's UNIX socket, plus the two liveness helpers."""
import json
import os
import socket
import sys

from .paths import idir

BOARD = '/machine/x4pro-board'
INPUT = '/machine/x4pro-input'


class QMP:
    def __init__(self, path, timeout=5.0):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(timeout)
        self.sock.connect(path)
        self.buf = b''
        self._recv()                      # greeting
        self.cmd('qmp_capabilities')

    def _recv(self):
        while b'\n' not in self.buf:
            d = self.sock.recv(65536)
            if not d:
                raise EOFError('QMP closed')
            self.buf += d
        line, self.buf = self.buf.split(b'\n', 1)
        return json.loads(line)

    def cmd(self, name, /, **args):   # positional-only: QMP arguments may themselves be called "name"
        msg = {'execute': name}
        if args:
            msg['arguments'] = args
        self.sock.sendall((json.dumps(msg) + '\n').encode())
        while True:
            r = self._recv()
            if 'event' in r:
                continue
            if 'error' in r:
                raise RuntimeError(f"{name}: {r['error'].get('desc')}")
            return r.get('return')

    def qom_get(self, path, prop):
        return self.cmd('qom-get', path=path, property=prop)

    def qom_set(self, path, prop, value):
        return self.cmd('qom-set', path=path, property=prop, value=value)

    def close(self):
        self.sock.close()


def connect(name, timeout=5.0):
    p = os.path.join(idir(name), 'qmp.sock')
    if not os.path.exists(p):
        sys.exit(f'no instance "{name}" (missing {p}); run `x4emu run` first')
    return QMP(p, timeout)


def pid_alive(name):
    try:
        pid = int(open(os.path.join(idir(name), 'pid')).read())
        os.kill(pid, 0)
        return pid
    except (OSError, ValueError):
        return None
