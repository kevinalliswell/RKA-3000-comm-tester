from threading import Event

import pytest

from rka_comm_tester.workers import PortWorkers


def test_same_port_is_exclusive_but_other_port_can_run():
    workers = PortWorkers()
    release = Event()
    started = Event()

    def slow():
        started.set()
        release.wait(2)
        return "done"

    first = workers.submit("COM7", slow)
    assert started.wait(1)
    with pytest.raises(RuntimeError, match="正在执行"):
        workers.submit("COM7", lambda: None)
    second = workers.submit("COM8", lambda: "other")
    assert second.result(timeout=1) == "other"
    release.set()
    assert first.result(timeout=1) == "done"
    workers.close()
