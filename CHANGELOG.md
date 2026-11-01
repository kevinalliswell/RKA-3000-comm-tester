# Changelog

## Unreleased

- Add an operator-confirmed balance tare test (`POST /api/tare`): one tare command, then a
  cancellable wait for a stable zero within a configurable timeout and tolerance.
- Bound tare confirmation reads by the remaining deadline, reject late zero readings,
  and report unknown responses as degraded rather than offline.
- Keep the window open with the failure reason when the backend cannot start, log desktop
  startup failures to `desktop.log`, and show a Windows error box if no window can be created.
- Shut the backend down when the desktop process exits unexpectedly (stdin EOF).
- Release builds run only the bundled `rka-backend.exe`; `RKA_PYTHON` and interpreter
  fallbacks are limited to development builds.
- Route API diagnostics to the user log also when started with `python -m`.

## 0.1.0 — 2026-10-31

- Promote the Tauri 2 + Svelte 5 + Python sidecar desktop to the first stable software release.
- Include protocol, architecture and layered-design documentation in the repository.
- Publish verified Windows x64 NSIS and portable assets with SHA-256 checksums.

Hardware acceptance and Windows code signing remain outstanding for deployments that require them.

## 0.1.0-rc.1 — 2026-10-31

- Migrate the desktop to Tauri 2, Svelte 5 and TypeScript with a Python API sidecar.
- Separate presentation, shell, API, application, domain and infrastructure layers.
- Validate protocol replies, serial parameters and live MFC scale metadata.
- Keep scans mutually exclusive and finish cancellation only after driver cleanup.
- Correct balance output cleanup and make diagnostic states explicit in the UI.
- Bundle Python into Windows x64 NSIS and portable distributions with dependency notices.
- Add package, backend, desktop lifecycle and install/uninstall checks plus tag-driven releases.

The candidate was superseded by `0.1.0` after the software CI and packaging checks completed.
