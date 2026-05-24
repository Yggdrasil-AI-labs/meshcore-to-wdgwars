#!/usr/bin/env python3
"""
Heimdall web — self-hosted server with WDGoWars upload proxy.

The public GitHub Pages deploy can't direct-upload because wdgwars.pl's API
doesn't return CORS headers. This script gives self-hosters a working path:
it serves the static files AND proxies same-origin requests to /api/upload/
through to https://wdgwars.pl/api/upload/. The browser sees a same-origin
POST so CORS doesn't apply; the server-to-server forward inherits no such
restriction.

Usage:
    python3 serve.py [--port 8765] [--host 127.0.0.1] [--upstream URL]

Then in the page's "Endpoint" field, use:
    /api/upload/

(relative path — points at this proxy instead of the cross-origin WDG URL).

Stdlib only. No dependencies. Runs anywhere Python 3.8+ is installed.
"""
from __future__ import annotations
import argparse
import http.server
import socketserver
import sys
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_UPSTREAM = "https://wdgwars.pl/api/upload/"
PROXY_PREFIX = "/api/upload"


class ProxyHandler(http.server.SimpleHTTPRequestHandler):
    upstream_url: str = DEFAULT_UPSTREAM

    def log_message(self, fmt, *args):
        sys.stderr.write(f"[heimdall-serve] {self.address_string()} - " + fmt % args + "\n")

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS, GET")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-API-Key, Accept")
        self.end_headers()

    def do_POST(self):
        if not self.path.startswith(PROXY_PREFIX):
            self.send_error(404, "Only /api/upload/ is proxied")
            return

        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""

        fwd_headers = {}
        for h in ("Content-Type", "X-API-Key", "Accept", "User-Agent"):
            v = self.headers.get(h)
            if v:
                fwd_headers[h] = v

        req = urllib.request.Request(
            self.upstream_url, data=body, method="POST", headers=fwd_headers,
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                self._relay(resp.status, resp.headers.get_content_type(), resp.read())
        except urllib.error.HTTPError as e:
            self._relay(e.code, e.headers.get_content_type() or "application/json",
                        e.read())
        except Exception as e:
            sys.stderr.write(f"[heimdall-serve] proxy error: {e}\n")
            self.send_error(502, f"Proxy error: {e}")

    def _relay(self, status: int, content_type: str, body: bytes):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)


def main():
    ap = argparse.ArgumentParser(description="Heimdall web — self-hosted with upload proxy")
    ap.add_argument("--port", type=int, default=8765, help="port to bind (default: 8765)")
    ap.add_argument("--host", default="127.0.0.1", help="host to bind (default: 127.0.0.1)")
    ap.add_argument("--upstream", default=DEFAULT_UPSTREAM,
                    help=f"upstream upload URL (default: {DEFAULT_UPSTREAM})")
    args = ap.parse_args()

    ProxyHandler.upstream_url = args.upstream

    web_dir = Path(__file__).resolve().parent
    import os
    os.chdir(web_dir)

    with socketserver.ThreadingTCPServer((args.host, args.port), ProxyHandler) as srv:
        srv.allow_reuse_address = True
        print(f"Heimdall web — serving {web_dir}")
        print(f"  Static:   http://{args.host}:{args.port}/")
        print(f"  Proxy:    POST /api/upload/  ->  {args.upstream}")
        print(f"  In the page Settings, set Endpoint to:  /api/upload/")
        print(f"  Ctrl+C to stop.")
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            print("\nShutting down.")


if __name__ == "__main__":
    main()
