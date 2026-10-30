"""PyInstaller entry point; keep package imports absolute."""

from rka_comm_tester.app import main

if __name__ == "__main__":
    raise SystemExit(main())
