"""Freeze the Python API and collect license texts for Windows distribution."""

from __future__ import annotations

import importlib.metadata
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    if sys.platform != "win32" or platform.machine().lower() not in {"amd64", "x86_64"}:
        raise SystemExit("The release sidecar requires Windows x64 Python.")
    if sys.version_info[:2] != (3, 12):
        raise SystemExit("Use Python 3.12 for release builds.")
    subprocess.run([
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
        "--onefile", "--console", "--noupx", "--name", "rka-backend",
        "--distpath", str(ROOT / "build" / "backend"),
        "--workpath", str(ROOT / "build" / "pyinstaller"),
        "--specpath", str(ROOT / "build"),
        "--paths", str(ROOT / "src"),
        "--collect-submodules", "rka_comm_tester._vendor",
        "--collect-data", "rka_comm_tester._vendor",
        "--exclude-module", "PySide6", "--exclude-module", "tkinter",
        str(ROOT / "scripts" / "backend_entry.py"),
    ], cwd=ROOT, check=True)
    binaries = ROOT / "src-tauri" / "binaries"
    binaries.mkdir(exist_ok=True)
    shutil.copy2(ROOT / "build/backend/rka-backend.exe",
                 binaries / "rka-backend-x86_64-pc-windows-msvc.exe")

    notices = ROOT / "build" / "notices"
    notices.mkdir(parents=True, exist_ok=True)
    for source, name in [
        (ROOT / "LICENSE", "APPLICATION-LICENSE.txt"),
        (ROOT / "THIRD_PARTY_NOTICES.md", "THIRD_PARTY_NOTICES.md"),
        (ROOT / "src/rka_comm_tester/_vendor/VENDOR.md", "VENDOR.md"),
        (ROOT / "src/rka_comm_tester/_vendor/azbil_mfc/LICENSE-MIT", "AZBIL-MIT.txt"),
        (ROOT / "src/rka_comm_tester/_vendor/taie_temp/LICENSE-APACHE-2.0", "TAIE-APACHE-2.0.txt"),
        (ROOT / "third_party/pyserial/LICENSE.txt", "PYSERIAL-LICENSE.txt"),
        (Path(sys.base_prefix) / "LICENSE.txt", "PYTHON-LICENSE.txt"),
    ]:
        shutil.copy2(source, notices / name)
    # The pyserial 3.5 wheel omits its license; preserve the upstream v3.5 text.
    if importlib.metadata.version("pyserial") != "3.5":
        raise RuntimeError("Refresh the bundled pyserial license when changing its version")
    installer_dist = importlib.metadata.distribution("pyinstaller")
    copying = next(file for file in installer_dist.files or [] if file.name == "COPYING.txt")
    shutil.copy2(installer_dist.locate_file(copying), notices / "PYINSTALLER-COPYING.txt")
    with (notices / "python-build-requirements.txt").open("w", encoding="utf-8") as output:
        subprocess.run([sys.executable, "-m", "pip", "freeze"], check=True, stdout=output)


if __name__ == "__main__":
    main()
