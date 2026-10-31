"""Keep release versions identical across Python, Rust and npm manifests (Python 3.12)."""

import argparse
import json
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", help="Release tag, exactly v<manifest-version>")
    args = parser.parse_args()
    versions = {}
    for name in ["package.json", "frontend/package.json", "src-tauri/tauri.conf.json"]:
        versions[name] = json.loads((ROOT / name).read_text(encoding="utf-8"))["version"]
    for name in ["package-lock.json", "frontend/package-lock.json"]:
        data = json.loads((ROOT / name).read_text(encoding="utf-8"))
        versions[name] = data["version"]
        versions[f"{name} root package"] = data["packages"][""]["version"]
    for name, section in [("pyproject.toml", "project"), ("src-tauri/Cargo.toml", "package")]:
        versions[name] = tomllib.loads((ROOT / name).read_text(encoding="utf-8"))[section]["version"]
    lock = tomllib.loads((ROOT / "src-tauri/Cargo.lock").read_text(encoding="utf-8"))
    versions["Cargo.lock"] = next(package["version"] for package in lock["package"]
                                  if package["name"] == "rka-3000-comm-tester")
    module = (ROOT / "src/rka_comm_tester/__init__.py").read_text(encoding="utf-8")
    versions["Python __version__"] = re.search(r'__version__ = "([^"]+)"', module)[1]
    expected = versions["package.json"]
    for name, version in versions.items():
        if version != expected:
            raise SystemExit(f"Version mismatch: {name}={version}, expected {expected}")
    if args.tag and args.tag != f"v{expected}":
        raise SystemExit(f"Tag {args.tag} does not match v{expected}")
    print(f"All manifest versions match: {expected}")


if __name__ == "__main__":
    main()
