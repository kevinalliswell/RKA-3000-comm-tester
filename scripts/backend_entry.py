"""Minimal frozen entry point: no Qt or desktop dependencies."""

from rka_comm_tester.api import main

if __name__ == "__main__":
    raise SystemExit(main())
