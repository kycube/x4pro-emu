"""`x4emu watch`: a tiny stdlib HTTP server (127.0.0.1 only) that shows the panel and board state
live in a browser. Nothing here polls QEMU on its own — every QMP query happens inside the handling
of one HTTP request and the connection is closed again before the handler returns (the socket
serves one client at a time), so the instance's other `x4emu` commands keep working the whole time
`watch` is up (`tests/test_cli_polish.py` proves that explicitly). The browser side does the
polling: `/` serves a page that fetches `/state.json` every 300 ms and only reloads `/panel.png`
when `refresh_count` changed.
"""
import html
import http.server
import json
import os
import sys
import time

from .commands import screenshot_to
from .output import out
from .paths import idir
from .qmp import BOARD, connect

PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><title>x4emu watch: __NAME__</title>
<style>
body { font: 14px monospace; background: #111; color: #eee; margin: 1em; }
img { border: 1px solid #444; image-rendering: pixelated; max-width: 100%; }
#status { margin-top: .5em; white-space: pre; }
</style></head>
<body>
<h1>x4emu watch &mdash; __NAME__</h1>
<img id="panel" src="/panel.png" alt="panel">
<div id="status">connecting...</div>
<script>
let lastRefresh = null;
async function poll() {
  try {
    const r = await fetch('/state.json', {cache: 'no-store'});
    const s = await r.json();
    document.getElementById('status').textContent =
      'refresh_count: ' + s.refresh_count + '\\n' +
      'uptime_us: ' + s.uptime_us + '\\n' +
      'last_mode: ' + s.last_mode;
    if (s.refresh_count !== lastRefresh) {
      lastRefresh = s.refresh_count;
      document.getElementById('panel').src = '/panel.png?t=' + Date.now();
    }
  } catch (e) { /* instance may be between requests */ }
  setTimeout(poll, 300);
}
poll();
</script>
</body></html>
"""


def make_handler(name, out_file):
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass            # keep stdout to the one URL line `cmd_watch` already printed

        def _send(self, code, content_type, body):
            self.send_response(code)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            try:
                if self.path == '/' or self.path.startswith('/?'):
                    body = PAGE.replace('__NAME__', html.escape(name)).encode()
                    self._send(200, 'text/html; charset=utf-8', body)
                elif self.path.startswith('/state.json'):
                    q = connect(name)
                    try:
                        s = q.qom_get(BOARD, 'state')
                    finally:
                        q.close()
                    body = (s if isinstance(s, str) else json.dumps(s)).encode()
                    self._send(200, 'application/json', body)
                elif self.path.startswith('/panel.png'):
                    dest = screenshot_to(name, os.path.join(idir(name), 'watch.png'))
                    body = open(dest, 'rb').read()
                    if out_file:
                        with open(out_file, 'wb') as f:
                            f.write(body)
                    self._send(200, 'image/png', body)
                else:
                    self._send(404, 'text/plain', b'not found')
            except Exception as e:
                try:
                    self._send(502, 'text/plain', str(e).encode())
                except OSError:
                    pass        # the client went away

    return Handler


def cmd_watch(a):
    handler = make_handler(a.name, os.path.abspath(a.out) if a.out else None)
    httpd = http.server.HTTPServer(('127.0.0.1', a.port), handler)
    port = httpd.server_address[1]           # resolves --port 0 to the port the OS picked
    url = f'http://127.0.0.1:{port}/'
    out.line(url)
    out.set(url=url, port=port)
    out.finish()          # print/collect right away: `watch` blocks below until --seconds or ^C
    sys.stdout.flush()    # stdout is block-buffered when it is not a TTY (piped to a test/caller)
    try:
        if a.seconds is not None:
            httpd.timeout = 0.5
            deadline = time.time() + a.seconds
            while time.time() < deadline:
                httpd.handle_request()
        else:
            httpd.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
