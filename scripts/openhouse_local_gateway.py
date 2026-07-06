#!/usr/bin/env python3
"""Local OpenHouse gateway for Tidal Echo.

Serves the PWA under /chat/ and proxies /relay/ to backend/app.py running on
127.0.0.1:3011. This keeps the browser-facing API_BASE="/relay" contract intact
without requiring nginx for local service-manager usage.
"""

from __future__ import annotations

import http.client
import mimetypes
import os
import select
import signal
import socket
import subprocess
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
WEB_DIR = ROOT / "web"
BACKEND_APP = ROOT / "backend" / "app.py"

HOST = os.environ.get("TIDAL_ECHO_HOST", "127.0.0.1")
PORT = int(os.environ.get("TIDAL_ECHO_PORT", "23087"))
RELAY_HOST = os.environ.get("TIDAL_ECHO_RELAY_HOST", "127.0.0.1")
RELAY_PORT = int(os.environ.get("RELAY_PORT", "3011"))
BACKEND_PYTHON = os.environ.get("TIDAL_ECHO_BACKEND_PYTHON", sys.executable)

HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
}


def start_backend() -> subprocess.Popen:
    env = os.environ.copy()
    env.setdefault("RELAY_PORT", str(RELAY_PORT))
    env.setdefault("RELAY_PUBLIC_PREFIX", "/relay")
    env.setdefault("RELAY_APP_PATH", "/chat/")
    env.setdefault("RELAY_ALLOW_ORIGINS", f"http://{HOST}:{PORT}")
    return subprocess.Popen([BACKEND_PYTHON, str(BACKEND_APP)], cwd=str(ROOT), env=env)


def wait_backend(timeout: float = 20.0) -> None:
    deadline = time.time() + timeout
    last_err: Exception | None = None
    while time.time() < deadline:
        try:
            conn = http.client.HTTPConnection(RELAY_HOST, RELAY_PORT, timeout=1.5)
            conn.request("GET", "/healthz")
            res = conn.getresponse()
            res.read()
            if 200 <= res.status < 500:
                return
        except Exception as exc:
            last_err = exc
        finally:
            try:
                conn.close()
            except Exception:
                pass
        time.sleep(0.3)
    raise RuntimeError(f"backend did not become ready: {last_err}")


class Handler(BaseHTTPRequestHandler):
    server_version = "TidalEchoOpenHouse/1.0"

    def log_message(self, fmt: str, *args: object) -> None:
        sys.stderr.write("[%s] %s\n" % (self.log_date_time_string(), fmt % args))

    def do_GET(self) -> None:
        if self.path == "/healthz":
            self._send_text(200, "ok\n")
            return
        if self.path.startswith("/relay/") or self.path == "/relay":
            if self.headers.get("Upgrade", "").lower() == "websocket":
                self._proxy_websocket()
                return
            self._proxy()
            return
        self._serve_static()

    def do_HEAD(self) -> None:
        if self.path.startswith("/relay/") or self.path == "/relay":
            self._proxy()
            return
        self._serve_static(head_only=True)

    def do_POST(self) -> None:
        if self.path.startswith("/relay/") or self.path == "/relay":
            self._proxy()
            return
        self._send_text(404, "not found\n")

    def do_PATCH(self) -> None:
        if self.path.startswith("/relay/") or self.path == "/relay":
            self._proxy()
            return
        self._send_text(404, "not found\n")

    def do_DELETE(self) -> None:
        if self.path.startswith("/relay/") or self.path == "/relay":
            self._proxy()
            return
        self._send_text(404, "not found\n")

    def _send_text(self, status: int, body: str) -> None:
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def _serve_static(self, head_only: bool = False) -> None:
        parsed = urlsplit(self.path)
        path = parsed.path
        if path in {"", "/"}:
            self.send_response(302)
            self.send_header("Location", "/chat/")
            self.end_headers()
            return
        if path == "/chat" or path == "/chat/":
            rel = "index.html"
        elif path.startswith("/chat/"):
            rel = path[len("/chat/") :]
        else:
            rel = path.lstrip("/")

        candidate = (WEB_DIR / rel).resolve()
        if WEB_DIR.resolve() not in candidate.parents and candidate != WEB_DIR.resolve():
            self._send_text(403, "forbidden\n")
            return
        if candidate.is_dir():
            candidate = candidate / "index.html"
        if not candidate.exists() or not candidate.is_file():
            self._send_text(404, "not found\n")
            return

        ctype = mimetypes.guess_type(str(candidate))[0] or "application/octet-stream"
        data = b"" if head_only else candidate.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store" if candidate.name == "index.html" else "public, max-age=60")
        self.send_header("Content-Length", str(candidate.stat().st_size))
        self.end_headers()
        if not head_only:
            self.wfile.write(data)

    def _proxy(self) -> None:
        parsed = urlsplit(self.path)
        upstream_path = parsed.path[len("/relay") :] or "/"
        if parsed.query:
            upstream_path += "?" + parsed.query

        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else None
        headers = {
            k: v
            for k, v in self.headers.items()
            if k.lower() not in HOP_BY_HOP and k.lower() != "host"
        }

        conn = http.client.HTTPConnection(RELAY_HOST, RELAY_PORT, timeout=3600)
        try:
            conn.request(self.command, upstream_path, body=body, headers=headers)
            res = conn.getresponse()
            self.send_response(res.status, res.reason)
            for k, v in res.getheaders():
                if k.lower() in HOP_BY_HOP:
                    continue
                self.send_header(k, v)
            self.end_headers()
            content_type = (res.getheader("Content-Type") or "").lower()
            if "text/event-stream" in content_type:
                while True:
                    line = res.readline()
                    if not line:
                        break
                    self.wfile.write(line)
                    self.wfile.flush()
            else:
                while True:
                    chunk = res.read(8192)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    self.wfile.flush()
        except BrokenPipeError:
            pass
        except Exception as exc:
            self._send_text(502, f"relay proxy error: {exc}\n")
        finally:
            conn.close()

    def _proxy_websocket(self) -> None:
        parsed = urlsplit(self.path)
        upstream_path = parsed.path[len("/relay") :] or "/"
        if parsed.query:
            upstream_path += "?" + parsed.query

        upstream = None
        try:
            upstream = socket.create_connection((RELAY_HOST, RELAY_PORT), timeout=10)
            header_lines = [
                f"{self.command} {upstream_path} HTTP/1.1",
                f"Host: {RELAY_HOST}:{RELAY_PORT}",
            ]
            for k, v in self.headers.items():
                if k.lower() == "host":
                    continue
                header_lines.append(f"{k}: {v}")
            upstream.sendall(("\r\n".join(header_lines) + "\r\n\r\n").encode("iso-8859-1"))

            client = self.connection
            client.settimeout(None)
            upstream.settimeout(None)
            while True:
                readable, _, _ = select.select([client, upstream], [], [], 3600)
                if not readable:
                    break
                for sock in readable:
                    data = sock.recv(65536)
                    if not data:
                        return
                    if sock is client:
                        upstream.sendall(data)
                    else:
                        client.sendall(data)
        except BrokenPipeError:
            pass
        except Exception as exc:
            try:
                self._send_text(502, f"websocket proxy error: {exc}\n")
            except Exception:
                pass
        finally:
            if upstream is not None:
                try:
                    upstream.close()
                except Exception:
                    pass


def main() -> int:
    backend = start_backend()

    def shutdown(*_: object) -> None:
        if backend.poll() is None:
            backend.terminate()
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    try:
        wait_backend()
        httpd = ThreadingHTTPServer((HOST, PORT), Handler)
        print(f"Tidal Echo local gateway listening on http://{HOST}:{PORT}/chat/", flush=True)
        httpd.serve_forever()
    finally:
        if backend.poll() is None:
            backend.terminate()
            try:
                backend.wait(timeout=5)
            except subprocess.TimeoutExpired:
                backend.kill()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
