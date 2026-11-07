# duosida-ev

Async Python library and `duosida` CLI for local control of Duosida EV
chargers. The charger speaks **OCPP 1.6 encoded as protobuf** on TCP 9988,
with itself as the charge point; this library is the central system. Read
[docs/PROTOCOL.md](docs/PROTOCOL.md) before touching protocol code: it records
what is verified and what is only inferred.

## Layout (`src/duosida_ev/`)

| Module | Role |
|---|---|
| `protobuf.py` | schema-less wire codec (`Message`, `Field`, encoders, zigzag) |
| `framing.py` | `FrameSplitter`: a message ends at its field 101 |
| `protocol.py` | OCPP action numbers (`Action`: conf `2k−1`, req `2k`), envelope (`Frame`, fields 100/101), builders and parsers |
| `models.py` | frozen dataclasses: `ChargerStatus`, `ChargerInfo`, `ConnectorStatus`, `ChargerConfiguration`; `ChargerState` (OCPP names) |
| `client.py` | `DuosidaClient`: connection, reader task, auto-answers, watchdog, commands |
| `discovery.py` | UDP probe 48890 → 48899, `identify_charger` |
| `cli.py` | argparse + `asyncio.run` |

No runtime dependencies. Python ≥ 3.11.

## Rules

- **Never run the CLI or the library against the real charger without the
  owner's OK** (see the workspace `CLAUDE.md`). Test with the fake charger in
  `tests/helpers.py`.
- Only name a protocol field when a capture backs it; keep the rest in `raw` /
  `unknown`. Update `docs/PROTOCOL.md` with the evidence.
- Builders must stay byte-identical to the vendor app's messages
  (`tests/test_protocol.py::BuildersMatchVendorAppTest`).
- `BootNotification.conf` must carry the current time: the charger sets its
  clock from it (the 0.1.x bug).
- No code from `matiaskunin/duosida-local`: it is GPL-3.0 and this is MIT.

## Commands

```bash
uv run --with pytest pytest -q            # 114 tests
uv run --python 3.11 --with pytest pytest -q
uvx ruff check src tests tools && uvx ruff format --check src tests tools
uvx mypy --strict src
python tools/pcap.py capture.pcap         # decode a capture
python tools/extract_fixtures.py ON_OFF.pcap DIRECT_MODE.pcap MAX_VOLTAGE.pcap
```

The captures (2025-11-18) are kept privately by the owner and are never
committed; the workspace `CLAUDE.md` says where they are. `tests/fixtures/captures.json` is a sanitized
extract, with the charger id replaced by `0123456789012345678`.

## Release

The version lives only in `src/duosida_ev/__init__.py` (hatch reads it).
Releasing to PyPI and pushing tags are public actions: confirm with the owner.
`duosida-ha` pins the version in `manifest.json` and `pyproject.toml`.
