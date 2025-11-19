# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.1] - 2024-11-19

### Fixed

- Updated README.md to use GitHub repository URL instead of git.dias.pt

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

[0.1.1]: https://github.com/americodias/duosida-ev/releases/tag/v0.1.1
[0.1.0]: https://github.com/americodias/duosida-ev/releases/tag/v0.1.0
