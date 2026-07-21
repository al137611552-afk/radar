"""Serve the Watchman read-only web dashboard with the Python standard library."""

from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from dashboard import (
    build_dashboard_payload,
    build_product_detail,
    normalize_product_code,
)

STATIC_ROUTES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/assets/dashboard.css": ("assets/dashboard.css", "text/css; charset=utf-8"),
    "/assets/dashboard.js": (
        "assets/dashboard.js", "text/javascript; charset=utf-8"
    ),
}
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        "img-src 'self' data:; connect-src 'self'; object-src 'none'; "
        "base-uri 'none'; frame-ancestors 'none'"
    ),
}


class DashboardServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class DashboardHandler(BaseHTTPRequestHandler):
    project_root: Path
    web_root: Path

    def _headers(self, content_type: str, length: int, cache: str) -> None:
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", cache)
        for name, value in SECURITY_HEADERS.items():
            self.send_header(name, value)
        self.end_headers()

    def _send(self, status: int, content_type: str, body: bytes, cache="no-cache"):
        self.send_response(status)
        self._headers(content_type, len(body), cache)
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler API
        parsed = urlsplit(self.path)
        route = parsed.path
        if route == "/api/dashboard":
            try:
                payload = build_dashboard_payload(self.project_root)
                body = json.dumps(
                    payload, ensure_ascii=False, allow_nan=False,
                    separators=(",", ":"),
                ).encode("utf-8")
            except Exception:
                self.log_error("dashboard payload failed")
                self._send(
                    500, "application/json; charset=utf-8",
                    b'{"error":"dashboard unavailable"}', "no-store",
                )
                return
            self._send(200, "application/json; charset=utf-8", body, "no-store")
            return

        if route == "/api/product":
            codes = parse_qs(parsed.query, keep_blank_values=True).get("code", [])
            try:
                if len(codes) != 1:
                    raise ValueError("exactly one product code is required")
                code = normalize_product_code(codes[0])
            except ValueError:
                self._send(
                    400, "application/json; charset=utf-8",
                    b'{"error":"invalid product code"}', "no-store",
                )
                return
            try:
                payload = build_product_detail(self.project_root, code)
                body = json.dumps(
                    payload, ensure_ascii=False, allow_nan=False,
                    separators=(",", ":"),
                ).encode("utf-8")
            except Exception:
                self.log_error("product payload failed")
                self._send(
                    500, "application/json; charset=utf-8",
                    b'{"error":"product unavailable"}', "no-store",
                )
                return
            self._send(200, "application/json; charset=utf-8", body, "no-store")
            return

        static = STATIC_ROUTES.get(route)
        if static is None:
            self._send(404, "text/plain; charset=utf-8", b"Not found")
            return
        relative, content_type = static
        path = self.web_root / relative
        try:
            body = path.read_bytes()
        except OSError:
            self._send(404, "text/plain; charset=utf-8", b"Not found")
            return
        self._send(200, content_type, body)

    def log_message(self, format, *args):
        super().log_message(format, *args)


def create_server(
    host: str,
    port: int,
    project_root: Path | None = None,
    web_root: Path | None = None,
) -> DashboardServer:
    project = Path(project_root or Path(__file__).resolve().parent).resolve()
    assets = Path(web_root or project / "web").resolve()
    class ConfiguredDashboardHandler(DashboardHandler):
        pass

    ConfiguredDashboardHandler.project_root = project
    ConfiguredDashboardHandler.web_root = assets
    return DashboardServer((host, port), ConfiguredDashboardHandler)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Watchman只读行情功能面板")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument(
        "--project-root", type=Path, default=Path(__file__).resolve().parent
    )
    args = parser.parse_args(argv)
    if not 0 <= args.port <= 65535:
        parser.error("--port must be between 0 and 65535")
    return args


def main(argv=None):
    args = parse_args(argv)
    server = create_server(args.host, args.port, project_root=args.project_root)
    print(f"Watchman面板：http://{args.host}:{server.server_port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
