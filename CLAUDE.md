# Duosida EV Charger - Direct TCP Control

## Project Overview

Python library for direct TCP communication with Duosida EV wall chargers, bypassing the cloud API. Provides real-time telemetry and control via the charger's native protobuf protocol.

## Key Files

- **duosida_direct.py** - Main library with `DuosidaCharger` class
- **quick_test.py** - Simple test script for single readings

## Usage

```python
from duosida_direct import DuosidaCharger

charger = DuosidaCharger(
    host="192.168.20.95",
    device_id="0310107112122360374"
)

charger.connect()
status = charger.get_status()
print(status)
charger.disconnect()
```

### CLI Commands

```bash
# Get status
python3 duosida_direct.py --host 192.168.20.95 --device-id YOUR_DEVICE_ID status

# Monitor continuously
python3 duosida_direct.py --host 192.168.20.95 --device-id YOUR_DEVICE_ID monitor

# Set max current (6-32A)
python3 duosida_direct.py --host 192.168.20.95 --device-id YOUR_DEVICE_ID set-current 16
```

## Telemetry Fields

| Field | Source | Description |
|-------|--------|-------------|
| voltage | Field 1 | Line voltage (V) |
| current | Field 2 | Charging current (A) |
| current2 | Field 15 | Secondary/average current (A) |
| temperature_station | Field 8 | Station temperature (°C) |
| temperature_internal | Field 7 | Internal temperature (°C) |
| conn_status | Field 17 | Connection status (0-6) |
| today_consumption | Field 20 | Daily energy (kWh) |
| session_energy | Field 9 | Session energy (kWh) |
| timestamp | Field 18 | Reading timestamp |
| power | Calculated | V × I (W) |

## Status Codes

Based on [official HA integration](https://github.com/jello1974/duosidaEV-home-assistant):

- 0: Available
- 1: Preparing
- 2: Charging
- 3: Cooling
- 4: SuspendedEV
- 5: Finished
- 6: Holiday

## Protocol Details

- **Port**: 9988 (TCP)
- **Handshake**: `7a0408006a00`
- **Message format**: Protobuf with nested fields
- **Telemetry path**: Field 16 → Field 10 (DataVendorStatusReq)

## Dependencies

- Python 3.6+
- No external dependencies (uses only stdlib)

## Development Environment

This project uses a local virtual environment managed with `uv`:

```bash
# Activate the virtual environment
source .venv/bin/activate

# Run scripts
python3 duosida_direct.py --help
python3 duosida_network_discovery.py --help

# Install new dependencies (if needed)
uv pip install <package>
```

Always use the local venv when running or testing scripts.

## Notes

- Device ID can be found in the official Duosida app or Home Assistant
- Max current setting is write-only (cached locally after set)
- Charger sends mix of DataVendorStatusReq (telemetry) and DataContinueReq (historical) - library handles this automatically
