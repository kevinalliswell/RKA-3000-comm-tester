import logging

from rka_comm_tester.logging_setup import setup_logging


def test_log_file_is_created_and_contains_probe_context(tmp_path):
    path = setup_logging(tmp_path / "logs" / "communication.log")
    logging.getLogger("rka_comm_tester.scanner").info("test address=7 status=offline")
    for handler in logging.getLogger("rka_comm_tester").handlers:
        handler.flush()
    body = path.read_text(encoding="utf-8")
    assert "address=7 status=offline" in body
    assert "RKA communication tester started" in body
