"""Exercise the distributed frozen API without opening any serial port."""

from __future__ import annotations

import argparse
import json
import os
import queue
import secrets
import subprocess
import tempfile
import threading
from http.client import HTTPConnection
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("executable", type=Path)
    args = parser.parse_args()
    token = secrets.token_urlsafe(32)
    with tempfile.TemporaryDirectory(prefix="rka-smoke-") as temp:
        environment = dict(os.environ, RKA_API_TOKEN=token, PYTHONUTF8="1", USERPROFILE=temp)
        with (Path(temp) / "backend-stderr.txt").open("w+", encoding="utf-8") as errors:
            process = subprocess.Popen(
                [str(args.executable.resolve()), "--port", "0"], env=environment,
                cwd=temp, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=errors, text=True, encoding="utf-8",
            )
            try:
                ready: queue.Queue[str] = queue.Queue()
                threading.Thread(target=lambda: ready.put(process.stdout.readline()),
                                 daemon=True).start()
                port = json.loads(ready.get(timeout=30))["port"]

                def request(method, path, payload=None, authenticated=True):
                    connection = HTTPConnection("127.0.0.1", port, timeout=10)
                    headers = {"Content-Type": "application/json"}
                    if authenticated:
                        headers["Authorization"] = f"Bearer {token}"
                    connection.request(method, path,
                                       json.dumps(payload) if payload is not None else None, headers)
                    response = connection.getresponse()
                    result = response.status, json.loads(response.read())
                    connection.close()
                    return result

                assert request("GET", "/api/health", authenticated=False)[0] == 401
                assert request("GET", "/api/health")[1]["ok"]
                assert isinstance(request("GET", "/api/ports")[1]["ports"], list)
                assert request("GET", "/api/config")[1]["config"] is None
                # Rejected before probe execution: no real/fake port is opened.
                assert request("POST", "/api/scans", {})[0] == 400
                assert request("POST", "/api/shutdown")[0] == 202
                assert process.wait(timeout=15) == 0
                print("Frozen backend: auth, health, ports, validation and shutdown passed.")
            except BaseException:
                errors.flush()
                errors.seek(0)
                print(errors.read())
                raise
            finally:
                if process.poll() is None:
                    subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                                   check=False, capture_output=True)
                    process.wait(timeout=10)


if __name__ == "__main__":
    main()
