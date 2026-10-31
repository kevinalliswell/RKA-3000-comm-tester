"""Collect license metadata and shipped dependency license texts after building."""

from __future__ import annotations

import html
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def license_texts(directory: Path) -> dict[str, str]:
    files = []
    for child in directory.iterdir():
        if child.name.lower().startswith(("license", "licence", "copying", "notice", "copyright")):
            files.extend(child.rglob("*") if child.is_dir() else [child])
    return {str(file.relative_to(directory)): file.read_text(encoding="utf-8", errors="replace")
            for file in sorted(files) if file.is_file()}


def main() -> None:
    notices = ROOT / "build" / "notices"
    notices.mkdir(parents=True, exist_ok=True)
    metadata = json.loads(subprocess.check_output([
        "cargo", "metadata", "--locked", "--format-version", "1", "--manifest-path",
        str(ROOT / "src-tauri/Cargo.toml"),
    ], text=True, encoding="utf-8"))
    sections = []
    for package in sorted(metadata["packages"], key=lambda value: value["name"]):
        if package["source"] is None:
            continue
        directory = Path(package["manifest_path"]).parent
        texts = license_texts(directory)
        if package.get("license_file"):
            license_file = directory / package["license_file"]
            texts[str(license_file.name)] = license_file.read_text(encoding="utf-8", errors="replace")
        heading = f'{package["name"]} {package["version"]} — {package.get("license") or "see license"}'
        source = package.get("repository") or package.get("homepage") or ""
        sections.append(f"<section><h2>{html.escape(heading)}</h2><p>{html.escape(source)}</p>"
                        + "".join(f"<h3>{html.escape(name)}</h3><pre>{html.escape(text)}</pre>"
                                  for name, text in texts.items()) + "</section>")
    (notices / "rust-licenses.html").write_text(
        '<!doctype html><html lang="en"><meta charset="utf-8"><title>Rust dependency licenses</title>'
        '<style>body{max-width:1000px;margin:2rem auto;font:16px system-ui}pre{white-space:pre-wrap}'
        'section{border-top:1px solid #888;margin-top:2rem}</style><h1>Rust dependency licenses</h1>'
        + "".join(sections) + "</html>", encoding="utf-8")

    frontend = ROOT / "frontend"
    lock = json.loads((frontend / "package-lock.json").read_text(encoding="utf-8"))
    packages = []
    for location in lock["packages"]:
        if not location:
            continue
        directory = frontend / location
        manifest = directory / "package.json"
        if not manifest.is_file():  # Optional dependencies for other operating systems.
            continue
        data = json.loads(manifest.read_text(encoding="utf-8"))
        packages.append({"name": data["name"], "version": data["version"],
                         "license": data.get("license"), "repository": data.get("repository"),
                         "texts": license_texts(directory)})
    (notices / "frontend-licenses.json").write_text(
        json.dumps(packages, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
