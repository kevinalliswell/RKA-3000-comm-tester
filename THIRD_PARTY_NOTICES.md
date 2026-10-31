# Third-party notices

The application license is in `LICENSE`. Third-party components retain their own
licenses; source and exact dependency versions are available at the release tag.

| Component | License / source |
|---|---|
| CPython runtime and standard library | PSF-2.0 and included notices; full text in `notices/PYTHON-LICENSE.txt` |
| pyserial | BSD-3-Clause; `notices/PYSERIAL-LICENSE.txt`, https://github.com/pyserial/pyserial |
| PyInstaller bootloader | GPL-2.0-or-later with the distribution exception; `notices/PYINSTALLER-COPYING.txt`, https://pyinstaller.org/en/stable/license.html |
| Tauri / wry / tao | MIT OR Apache-2.0; https://github.com/tauri-apps/tauri |
| Svelte | MIT; https://github.com/sveltejs/svelte |
| Rust and frontend transitive dependencies | Exact versions in `src-tauri/Cargo.lock` and `frontend/package-lock.json`; license texts in `notices/rust-licenses.html` and `notices/frontend-licenses.json` |
| Azbil CPL driver | MIT; `notices/AZBIL-MIT.txt` |
| TAIE Modbus driver | Apache-2.0; `notices/TAIE-APACHE-2.0.txt` |
| Balance driver | Same-owner authorization; see `notices/VENDOR.md` |

PyInstaller's distribution exception permits bundling a proprietary application.
It does not change the application's license. The build also records installed
Python dependencies in `notices/python-build-requirements.txt`.
