"""Stage a portable directory, ZIP, NSIS installer and SHA-256 manifest."""

import hashlib
import json
import shutil
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    version = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))["version"]
    name = f"RKA-3000-comm-tester_{version}_windows-x64"
    release = ROOT / "src-tauri" / "target" / "release"
    portable = ROOT / "build" / "portable" / name
    assets = ROOT / "dist" / "release"
    if portable.exists() or assets.exists():
        raise SystemExit("Use a clean build/portable and dist/release directory before staging.")
    portable.mkdir(parents=True)
    assets.mkdir(parents=True)
    shutil.copy2(release / "rka-3000-comm-tester.exe", portable)
    shutil.copy2(ROOT / "src-tauri/binaries/rka-backend-x86_64-pc-windows-msvc.exe",
                 portable / "rka-backend.exe")
    shutil.copytree(ROOT / "build/notices", portable / "notices")
    for source in ["LICENSE", "README.md", "THIRD_PARTY_NOTICES.md"]:
        shutil.copy2(ROOT / source, portable)
    (portable / "START-HERE.txt").write_text(
        f"RKA-3000 communication tester {version}\n\n"
        "Extract the entire ZIP, then run rka-3000-comm-tester.exe.\n"
        "Keep rka-backend.exe next to it. Python / Node / Rust are not required.\n"
        "Windows 10/11 x64 and Microsoft Edge WebView2 Runtime are required.\n"
        "WebView2: https://developer.microsoft.com/microsoft-edge/webview2/\n"
        "User config and logs: %USERPROFILE%\\.rka-comm-tester\\\n"
        "The application is unsigned. See README.md for use and diagnostic limits.\n",
        encoding="utf-8")
    with zipfile.ZipFile(assets / f"{name}_portable.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        for file in sorted(portable.rglob("*")):
            if file.is_file():
                archive.write(file, file.relative_to(portable.parent))
    installers = list((release / "bundle" / "nsis").glob("*-setup.exe"))
    if len(installers) != 1:
        raise SystemExit(f"Expected one NSIS installer, got {installers}")
    shutil.copy2(installers[0], assets / f"{name}_setup.exe")
    checksums = []
    for file in sorted(assets.iterdir()):
        with file.open("rb") as stream:
            checksums.append(f"{hashlib.file_digest(stream, 'sha256').hexdigest()}  {file.name}")
    (assets / "SHA256SUMS.txt").write_text("\n".join(checksums) + "\n", encoding="utf-8")
    print(f"Release assets: {assets}")
    print(f"Portable executable: {portable / 'rka-3000-comm-tester.exe'}")


if __name__ == "__main__":
    main()
