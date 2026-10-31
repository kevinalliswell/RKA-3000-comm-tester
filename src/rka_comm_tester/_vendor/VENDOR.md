# Vendored protocol drivers

These are pinned, trimmed copies of upstream protocol libraries. Keep all local
corrections listed below and re-assess them on each upstream sync.

## RKA-local protocol corrections

The pinned copies now carry the following small, test-backed corrections. When re-syncing,
re-apply or drop each correction explicitly after comparing upstream behavior:

- `azbil_mfc/protocol.py`: strict CPL terminator, header, checksum-field and register-word
  validation plus outbound range checks.
- `azbil_mfc/bus.py`: reject replies from another address or with the wrong register count;
  never automatically retry a write.
- `azbil_mfc/client.py`: a forced live scale refresh cannot silently fall back to stale
  cached metadata.
- `taie_temp/modbus_rtu.py`: assemble fragmented serial reads, bound byte counts, verify
  the requested register count and write echo.
- `balance_serial/protocols/{gs_cpa,longteng}.py`: accept only documented full weight
  frames; Longteng `ST` frames are marked stable.
- `balance_serial/client.py`: tare confirmation requires a stable zero reading, not merely
  an ACK or an unstable zero.
- `balance_serial/models.py`: serial writes use the configured timeout so O1/O0 do not
  wait indefinitely for an unavailable output buffer.

This tester calls only read/probe methods. It requires live MFC scale metadata before
displaying engineering flow units and exposes no setpoint or tare action in its UI/CLI.
Copies were extracted from RKA-3000-desktop commit d2635df. The same-owner upstream
repositories and exact pinned revisions are listed below.

| Package | Upstream | Commit | License | Vendored modules | Omitted |
|---------|----------|--------|---------|------------------|---------|
| `azbil_mfc` | github.com/kevinalliswell/azbil-mfc | 83ddc491 | MIT | bus, client, protocol, constants, registers, exceptions, device_catalog, (trimmed) __init__ | dashboard/, mixer, recipe, scheduler, safety |
| `taie_temp` | github.com/kevinalliswell/taie-temp-controller | ab18b921 | Apache-2.0 | controller, modbus_rtu, register_addresses, __init__ | api, polling, acquisition |
| `balance_serial` | github.com/kevinalliswell/balance-serial | 496b7b76 | (no upstream LICENSE) | client, factory, models, exceptions, protocols/{base,gs_cpa,longteng}, __init__ | device, examples |

## Licensing note

`balance-serial` has no LICENSE file upstream. It is owned by the same owner as this
repository (kevinalliswell); vendoring here is under same-owner authorization. Recommend
adding an explicit LICENSE to the upstream repo. The upstream MIT and Apache-2.0
LICENSE files are kept in `azbil_mfc/LICENSE-MIT` and `taie_temp/LICENSE-APACHE-2.0`.
