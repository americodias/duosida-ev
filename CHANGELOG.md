# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.2] - 2026-11-07

### Changed

- Releases are built and published to PyPI by GitHub Actions
  (`.github/workflows/publish.yml`) when a GitHub release is published. The
  workflow uses PyPI trusted publishing, so no token is stored, and PyPI gets
  attestations. Before building, it checks that the release tag matches the
  package version and runs the lint and the tests. The code is unchanged
  since 0.2.1.

## [0.2.1] - 2026-11-07

### Changed

- `get_configuration()` waits up to 7 s by default (or the client's `timeout`
  if longer), resending every `retry_interval`: 4 sends instead of 3.
  Commands keep the shorter `timeout`. On one installation, 44 pulls got 33
  answers. 3 of those came 5-6 s after the first send, and the 5 s timeout
  had discarded them. `duosida get-config` uses the new default.

### Added

- Debug logging that explains what the charger did:
  - the answer to the handshake's `TriggerMessage(BootNotification)`, also in
    the "no BootNotification" warning;
  - answers that arrive after their request timed out, and repeated answers,
    with the delay since the request was sent;
  - keep-alive sends;
  - the close sequence: FIN sent, whether the charger closed its side within
    the cap, closed.

## [0.2.0] - 2026-11-07

A rewrite: async, and built on the finding that the charger speaks OCPP 1.6
encoded as protobuf (see [docs/PROTOCOL.md](docs/PROTOCOL.md)). Breaking
changes throughout; see "Migrating from 0.1" in the README.

### Fixed

- **The charger's clock was set to 2025-11-17 17:21:19 UTC on every
  connection.** The 0.1.x handshake replayed a captured `BootNotification.conf`
  that carries the current time. Session start times were therefore wrong, and
  `session_time` grew by a day per day. The client now sends the real time,
  and answers heartbeats with it too.
- The handshake no longer contains one particular charger's id. The id now
  comes from the charger's `BootNotification`, and `device_id` is no longer a
  parameter.
- The firmware string no longer picks up the next field's tag byte (`...+102`
  was really `...+10`). Identity is decoded as a nested message instead of by
  string slicing.
- Messages are split by structure: a message ends at its field 101. Reads that
  split or join messages no longer lose or garble status updates.
- Connections close gracefully: FIN, then reading until the charger's FIN.
  This also applies after a fatal error or an idle timeout. 0.1.x closed with
  unread data, which sends a RST; chargers have been reported to refuse
  connections for hours after one.
- `disconnect()` during a `connect()` makes the connect give up, so no session
  is left holding the charger's single connection slot.

### Added

- `DuosidaClient`: one persistent asyncio connection with a background reader.
  It keeps `status`, `connector_status` and `transaction_id` up to date from
  the charger's pushes and calls listeners on every change (`add_listener`,
  `wait_for_status`, `get_status`).
- Commands wait for the charger's confirmation. Setters return
  `ConfigurationStatus` (`ACCEPTED` / `REBOOT_REQUIRED`); rejections raise
  `CommandRejectedError`.
- The client answers the charger's requests the way the vendor app does:
  heartbeats, data transfers, status notifications, meter values and
  start/stop transactions. `respond_to_charger=False` turns this off. Before,
  unanswered requests made the charger retransmit them.
- A keep-alive `TriggerMessage(Heartbeat)` (`keepalive_interval`, 30 s) and an
  idle watchdog (`idle_timeout`, 60 s).
- Unanswered requests are resent with the same message id every
  `retry_interval` (2 s) until the timeout. The charger sometimes ignores a
  request, and the vendor app resends too.
- `stop_charging()` stops the active transaction id reported by the charger,
  not a made-up one.
- `get_configuration()` / `duosida get-config`: read the configuration
  (`DataPullAllConfigurationReq`). `max_current` is verified; the other names
  are provisional. The Wi-Fi SSID and password fields are redacted in output.
- `ChargerStatus.total_energy` (the charger's own register, field 3),
  `session_start`, `session_duration`, `transaction_id`, `id_tag`,
  `vehicle_connected` and raw `unknown` fields.
- `ConnectorStatus` from `StatusNotification`, sent the moment the state
  changes.
- States 7 `Unavailable` and 8 `Faulted`.
- Discovery: async, repeats the probe every second, accepts `targets` for
  chargers on another subnet or VLAN, identifies chargers in parallel and
  returns `DiscoveredCharger` objects.
- `py.typed`, mypy strict, ruff, 114 tests (Python 3.11–3.14) built from
  captures of the vendor app, `tools/pcap.py` and `tools/extract_fixtures.py`.

### Changed

- Requires Python 3.11 or later.
- `ChargerState` is a `StrEnum` with OCPP names: 3 `SuspendedEVSE` (was
  "Cooling"), 5 `Finishing` (was "Finished"), 6 `Reserved` (was "Holiday").
- `ChargerStatus` is immutable. `conn_status` → `raw_state`,
  `temperature_station` → `temperature`, `to_dict()` keys renamed.
- Exceptions: `DuosidaConnectionError` and `DuosidaTimeoutError` replace
  `ConnectionError` and `TimeoutError`, which shadowed the built-ins. Added
  `ProtocolError` and `CommandRejectedError`; `CommunicationError` is gone.
- CLI: `--device-id` is ignored, and `monitor` prints pushed updates.

### Removed

- The synchronous `DuosidaCharger` class and `monitor()`.
- `voltage2`/`voltage3`/`current2`/`current3`, which were always 0, and
  `temperature_internal`.

## [0.1.3] - 2025-11-20

### Removed

- **Removed get_max_current() method**: The charger does not return configuration values, so caching and getting config is misleading
- **Removed _cached_max_current**: No longer cache max current setting since charger doesn't report it back

### Documentation

- **Clarified configuration behavior**: All configuration settings are write-only. The charger does not return configuration values via the status protocol.
- Updated README.md and SETTINGS.md to note that configuration methods only write values and cannot read them back

### Technical Details

- Configuration methods (set_max_current, set_connection_timeout, etc.) remain write-only
- Use get_status() for telemetry data (voltage, current, power, temperature, etc.)
- Configuration settings must be tracked externally if needed

## [0.1.2] - 2024-11-19

### Added

- **Actual CP Voltage Reading**: Now reads real Control Pilot voltage from Field 9 instead of deriving from status
- **Manufacturer Field**: Extract manufacturer info from device data (e.g., "UCHEN")
- **Improved Device Info Parsing**: Better extraction of model, manufacturer, and firmware from device response

### Changed

- **Reordered ChargerStatus fields**: conn_status first, device_id/model/manufacturer/firmware last
- **Removed cp_state property**: Redundant with state property
- **Removed unused fields**: max_current, today_consumption, acc_energy
- **Set L2/L3 readings to 0**: voltage_l2, voltage_l3, current_l2, current_l3 always return 0 (field mappings uncertain)
- Updated CLI to display manufacturer in status output

### Fixed

- **Firmware parsing**: Fixed trailing control characters in firmware string
- **Model extraction**: Improved parsing of model name from device info

### Technical Details

- 35 unit tests (4 new)
- cp_voltage now uses actual Field 9 reading with fallback to derived value

## [0.1.1] - 2024-11-19

### Fixed

- Updated README.md to use the GitHub repository URL instead of a self-hosted mirror

## [0.1.0] - 2024-11-18

### Added

- **Network Discovery**: UDP broadcast discovery of Duosida chargers on local network
- **Direct TCP Communication**: Bypass cloud API for local control
- **Real-time Telemetry**: Read voltage, current, power, temperature, energy consumption
- **Charging Control**:
  - Start/stop charging sessions
  - Set maximum charging current (6-32A)
- **Configuration Settings**:
  - Connection timeout (30-900s)
  - Max/min voltage limits (265-290V / 70-110V)
  - Max temperature (85-95°C)
  - Direct work mode (plug and charge)
  - LED brightness (0=off, 1=low, 3=high)
- **JSON Export**: Get telemetry data as JSON for integration
- **CLI Tool**: Full-featured command-line interface with auto-discovery
- **Proper Logging**: Python logging module with verbose mode
- **Custom Exceptions**: DuosidaError hierarchy for error handling
- **3-Phase Support**: Read voltage/current for all phases

### Technical Details

- Protocol: Protobuf over TCP port 9988
- Discovery: UDP broadcast on port 48890/48899
- 46 unit tests
- Python 3.6+ compatible
- No external dependencies (stdlib only)

[0.2.2]: https://github.com/americodias/duosida-ev/releases/tag/v0.2.2
[0.2.1]: https://github.com/americodias/duosida-ev/releases/tag/v0.2.1
[0.2.0]: https://github.com/americodias/duosida-ev/releases/tag/v0.2.0
[0.1.3]: https://github.com/americodias/duosida-ev/releases/tag/v0.1.3
[0.1.2]: https://github.com/americodias/duosida-ev/releases/tag/v0.1.2
[0.1.1]: https://github.com/americodias/duosida-ev/releases/tag/v0.1.1
[0.1.0]: https://github.com/americodias/duosida-ev/releases/tag/v0.1.0
