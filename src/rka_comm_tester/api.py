"""Authenticated loopback HTTP adapter and Python composition root."""

from __future__ import annotations

import argparse
import hmac
import json
import logging
import os
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from .application import ScanConflict, ScanService
from .logging_setup import setup_logging
from .profiles import FileProfileStore
from .scanner import available_ports, scan

logger = logging.getLogger(__name__)


class ApiHandler(BaseHTTPRequestHandler):
    server: ApiServer

    def setup(self) -> None:
        super().setup()
        self.connection.settimeout(10)

    def log_message(self, format: str, *args: object) -> None:
        logger.debug("api %s - %s", self.address_string(), format % args)

    def _json(self, status: int, payload: object) -> None:
        body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self) -> bool:
        # Only the owning Tauri process (or the explicit development proxy) knows the token.
        value = self.headers.get("Authorization", "")
        if not hmac.compare_digest(value.encode(), f"Bearer {self.server.token}".encode()):
            self._json(401, {"error": "未授权的本地请求"})
            return False
        return True

    def _read_json(self) -> dict:
        if self.headers.get_content_type() != "application/json":
            raise ValueError("请求必须使用 application/json")
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > 1_048_576:
            raise ValueError("请求体为空或超过 1 MB")
        data = json.loads(self.rfile.read(length))
        if not isinstance(data, dict):
            raise ValueError("请求体必须是 JSON 对象")
        return data

    def do_GET(self) -> None:  # noqa: N802
        self._dispatch("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch("POST")

    def _dispatch(self, method: str) -> None:
        if not self._authorized():
            return
        path = urlsplit(self.path).path.rstrip("/")
        service = self.server.service
        parts = path.strip("/").split("/")
        try:
            if method == "GET" and path == "/api/health":
                self._json(200, service.health())
            elif method == "GET" and path == "/api/ports":
                self._json(200, {"ports": service.ports()})
            elif method == "GET" and path == "/api/config":
                self._json(200, {"config": service.load_profile()})
            elif method == "GET" and path == "/api/log":
                self._json(200, {
                    "filename": service.log_path.name,
                    "content": service.log_path.read_text(encoding="utf-8"),
                })
            elif method == "POST" and path == "/api/config":
                self._json(200, {"config": service.save_profile(self._read_json())})
            elif method == "POST" and path == "/api/scans":
                self._json(202, service.start(self._read_json()))
            elif method == "POST" and path == "/api/shutdown":
                self._json(202, {"ok": True})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
            elif parts[:2] == ["api", "scans"] and len(parts) == 3 and method == "GET":
                job = service.get(parts[2])
                self._json(200, job) if job else self._json(404, {"error": "扫描任务不存在"})
            elif (parts[:2] == ["api", "scans"] and len(parts) == 4
                  and parts[3] == "cancel" and method == "POST"):
                job = service.cancel(parts[2])
                self._json(200, job) if job else self._json(404, {"error": "扫描任务不存在"})
            else:
                self._json(404, {"error": "接口不存在"})
        except ScanConflict as exc:
            self._json(409, {"error": str(exc)})
        except (ValueError, TypeError, KeyError, UnicodeDecodeError) as exc:
            self._json(400, {"error": str(exc)})
        except Exception:
            logger.exception("api request failed")
            self._json(500, {"error": "业务服务异常，请查看日志"})


class ApiServer(ThreadingHTTPServer):
    def __init__(self, address: tuple[str, int], service: ScanService, token: str) -> None:
        if address[0] != "127.0.0.1":
            raise ValueError("业务 API 仅允许监听 127.0.0.1")
        if not token:
            raise ValueError("业务 API 需要访问令牌")
        self.service = service
        self.token = token
        super().__init__(address, ApiHandler)


def run_server(host: str, port: int, log_file: Path | None = None) -> None:
    log_path = setup_logging(log_file, console=True)
    token = os.environ.get("RKA_API_TOKEN") or secrets.token_urlsafe(32)
    service = ScanService(
        log_path, scan, available_ports,
        FileProfileStore(Path.home() / ".rka-comm-tester" / "config.json"),
    )
    server = ApiServer((host, port), service, token)
    # Machine-readable handshake on stdout; diagnostic logs go to stderr/files.
    print(json.dumps({"port": server.server_port}), flush=True)
    try:
        server.serve_forever(poll_interval=0.1)
    except KeyboardInterrupt:
        pass
    finally:
        service.close()
        server.server_close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="RKA-3000 本地业务 API")
    parser.add_argument("--host", default="127.0.0.1", choices=["127.0.0.1"])
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--log-file", type=Path)
    args = parser.parse_args(argv)
    run_server(args.host, args.port, args.log_file)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
