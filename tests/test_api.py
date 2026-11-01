import json
import os
import subprocess
import sys
import threading
import time
from http.client import HTTPConnection
from pathlib import Path

import pytest

from rka_comm_tester.api import ApiServer
from rka_comm_tester.application import ScanConflict, ScanService
from rka_comm_tester.models import ScanResult
from rka_comm_tester.profiles import FileProfileStore

PAYLOAD = {"device": "taie", "port": "__RKA_NO_SUCH_PORT__", "end_address": 1}


def wait_for(service, job_id, status):
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        snapshot = service.get(job_id)
        if snapshot["status"] == status:
            return snapshot
        time.sleep(0.005)
    pytest.fail(f"job did not reach {status}")


@pytest.fixture
def make_service(tmp_path):
    services = []

    def create(probe, tare=None):
        path = tmp_path / "communication.log"
        path.write_text("test log\n", encoding="utf-8")
        service = ScanService(path, probe, lambda: [], FileProfileStore(tmp_path / "config.json"),
                              tare=tare)
        services.append(service)
        return service

    yield create
    for service in services:
        service.close()


def test_results_complete_only_after_generator_cleanup(make_service):
    cleanup = threading.Event()

    def probe(config, cancelled):
        try:
            yield ScanResult(config.device, config.port, 1, "online", {"pv_c": 100.0})
        finally:
            cleanup.set()

    service = make_service(probe)
    job = service.start(PAYLOAD)
    result = wait_for(service, job["id"], "completed")
    assert cleanup.is_set()
    assert result["results"][0]["values"] == {"pv_c": 100.0}
    assert result["finished_at"]
    assert service.cancel(job["id"])["status"] == "completed"
    assert service.get("missing") is None
    assert service.cancel("missing") is None


def test_conflicting_scans_cancel_and_graceful_shutdown(make_service):
    started = threading.Event()
    cleaned = threading.Event()

    def probe(config, cancelled):
        started.set()
        try:
            cancelled.wait(2)
            yield ScanResult(config.device, config.port, 1, "offline")
        finally:
            cleaned.set()

    service = make_service(probe)
    job = service.start(PAYLOAD)
    assert started.wait(1)
    with pytest.raises(ScanConflict):
        service.start(PAYLOAD)
    service.close()
    assert cleaned.is_set()
    assert service.get(job["id"])["status"] == "cancelled"
    with pytest.raises(ScanConflict):
        service.start(PAYLOAD)


def test_cancel_waits_for_driver_cleanup(make_service):
    started = threading.Event()
    cleanup_allowed = threading.Event()

    def probe(config, cancelled):
        started.set()
        cancelled.wait(2)
        cleanup_allowed.wait(2)
        return
        yield

    service = make_service(probe)
    job = service.start(PAYLOAD)
    try:
        assert started.wait(1)
        assert service.cancel(job["id"])["status"] == "running"
        with pytest.raises(ScanConflict):
            service.start(PAYLOAD)
    finally:
        cleanup_allowed.set()
    assert wait_for(service, job["id"], "cancelled")["finished_at"]


def test_unexpected_probe_failure_is_visible(make_service):
    def probe(config, cancelled):
        raise RuntimeError("driver failed")
        yield

    service = make_service(probe)
    result = wait_for(service, service.start(PAYLOAD)["id"], "error")
    assert result["error"] == "RuntimeError: driver failed"


def test_tare_is_a_separate_balance_only_job_sharing_the_port_lock(make_service):
    started = threading.Event()

    def probe(config, cancelled):
        started.set()
        cancelled.wait(2)
        return
        yield

    def tare(config, cancelled):
        yield ScanResult(config.device, config.port, None, "online", {"after_g": 0.0})

    balance = PAYLOAD | {"device": "balance"}
    with pytest.raises(ValueError):
        make_service(probe).start_tare(balance)  # not wired
    service = make_service(probe, tare)
    with pytest.raises(ValueError):
        service.start_tare(PAYLOAD)  # TAIE has no tare
    job = service.start(balance)
    assert started.wait(1)
    with pytest.raises(ScanConflict):
        service.start_tare(balance)
    service.cancel(job["id"])
    wait_for(service, job["id"], "cancelled")
    job = service.start_tare(balance)
    assert job["operation"] == "tare"
    assert wait_for(service, job["id"], "completed")["results"][0]["values"] == {"after_g": 0.0}


def test_http_authorization_validation_profile_and_error_paths(make_service):
    def probe(config, cancelled):
        yield ScanResult(config.device, config.port, 1, "online")

    def tare(config, cancelled):
        yield ScanResult(config.device, config.port, None, "online")

    service = make_service(probe, tare)
    server = ApiServer(("127.0.0.1", 0), service, "test-secret")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def request(method, path, payload=None, authenticated=True):
        connection = HTTPConnection("127.0.0.1", server.server_port, timeout=2)
        headers = {"Content-Type": "application/json"}
        if authenticated:
            headers["Authorization"] = "Bearer test-secret"
        connection.request(method, path, json.dumps(payload) if payload is not None else None, headers)
        response = connection.getresponse()
        result = response.status, json.loads(response.read())
        assert response.getheader("Access-Control-Allow-Origin") is None
        connection.close()
        return result

    try:
        assert request("POST", "/api/scans", PAYLOAD, False)[0] == 401
        assert request("GET", "/api/health")[1]["ok"]
        assert request("GET", "/api/ports")[1] == {"ports": []}
        assert request("GET", "/api/config")[1] == {"config": None}
        assert request("POST", "/api/config", PAYLOAD)[0] == 200
        assert request("GET", "/api/config")[1]["config"]["port"] == PAYLOAD["port"]
        assert request("GET", "/api/log")[1]["content"] == "test log\n"
        for bad in [{}, [], PAYLOAD | {"port": None}, PAYLOAD | {"timeout_s": "bad"},
                    PAYLOAD | {"stopbits": True}, PAYLOAD | {"start_address": 2}]:
            assert request("POST", "/api/scans", bad)[0] == 400
        assert request("POST", "/api/tare", PAYLOAD)[0] == 400
        status, job = request("POST", "/api/tare", PAYLOAD | {"device": "balance"})
        assert (status, job["operation"]) == (202, "tare")
        wait_for(service, job["id"], "completed")
        status, job = request("POST", "/api/scans", PAYLOAD)
        assert (status, job["operation"]) == (202, "scan")
        assert request("GET", f"/api/scans/{job['id']}")[0] == 200
        assert request("GET", "/api/scans/missing")[0] == 404
        assert request("POST", "/api/scans/missing/cancel", {})[0] == 404
        assert request("POST", "/api/unknown", {})[0] == 404
        assert request("POST", "/api/shutdown")[0] == 202
        thread.join(2)
        assert not thread.is_alive()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


def test_api_rejects_non_loopback_and_empty_token(make_service):
    service = make_service(lambda config, stop: iter(()))
    with pytest.raises(ValueError):
        ApiServer(("0.0.0.0", 0), service, "secret")
    with pytest.raises(ValueError):
        ApiServer(("127.0.0.1", 0), service, "")


def _start_backend(tmp_path, *extra, stdin):
    src = Path(__file__).resolve().parents[1] / "src"
    environment = dict(os.environ, RKA_API_TOKEN="secret", PYTHONPATH=str(src),
                       HOME=str(tmp_path), USERPROFILE=str(tmp_path))
    process = subprocess.Popen(
        [sys.executable, "-m", "rka_comm_tester.api", "--port", "0",
         "--log-file", str(tmp_path / "communication.log"), *extra],
        env=environment, stdin=stdin, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        text=True,
    )
    port = json.loads(process.stdout.readline())["port"]
    return process, port


def test_backend_exits_when_owner_closes_stdin(tmp_path):
    process, _ = _start_backend(tmp_path, "--exit-on-stdin-eof", stdin=subprocess.PIPE)
    try:
        assert process.poll() is None
        process.stdin.close()  # What the OS does when the desktop process dies.
        assert process.wait(timeout=10) == 0
        # Also under `python -m`, API diagnostics must reach the user log file.
        log = (tmp_path / "communication.log").read_text(encoding="utf-8")
        assert "rka_comm_tester.api owner process closed stdin" in log
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()


def test_backend_without_stdin_watch_ignores_closed_stdin(tmp_path):
    process, port = _start_backend(tmp_path, stdin=subprocess.DEVNULL)
    try:
        time.sleep(0.5)
        assert process.poll() is None
        connection = HTTPConnection("127.0.0.1", port, timeout=5)
        connection.request("POST", "/api/shutdown", headers={"Authorization": "Bearer secret"})
        assert connection.getresponse().status == 202
        assert process.wait(timeout=10) == 0
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
