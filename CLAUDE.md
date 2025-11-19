# Duosida EV Charger - Direct TCP Control

## Project Overview

Python library for direct TCP communication with Duosida EV wall chargers, bypassing the cloud API. Provides real-time telemetry and control via the charger's native protobuf protocol.

## Package Structure

- **src/duosida_ev/charger.py** - Main `DuosidaCharger` class
- **src/duosida_ev/cli.py** - Command-line interface
- **src/duosida_ev/discovery.py** - Network discovery
- **src/duosida_ev/exceptions.py** - Custom exceptions

## Usage

```python
from duosida_ev import DuosidaCharger, discover_chargers

# Discover chargers
devices = discover_chargers()
for device in devices:
    print(f"Found: {device['ip']} - {device['device_id']}")

# Connect and get status
charger = DuosidaCharger(
    host="192.168.1.100",
    device_id="YOUR_DEVICE_ID"
)

charger.connect()
status = charger.get_status()
print(status)
charger.disconnect()
```

### CLI Commands

```bash
# Auto-discover and get status
duosida status

# Get status with JSON output
duosida status --json

# Set max current (6-32A)
duosida set-current 16

# Start/stop charging
duosida start
duosida stop

# Monitor continuously
duosida monitor

# Configuration settings
duosida set-timeout 120          # 30-900 seconds
duosida set-max-temp 90          # 85-95°C
duosida set-max-voltage 280      # 265-290V
duosida set-min-voltage 90       # 70-110V
duosida set-direct-mode on       # on/off
duosida set-led-brightness 3     # 0=off, 1=low, 3=high
```

## Telemetry Fields

| Field | Source | Description |
|-------|--------|-------------|
| conn_status | Field 17 | Connection status (0-6) |
| cp_voltage | Field 9 | Control Pilot voltage (V) |
| voltage | Field 1 | Line voltage L1 (V) |
| voltage_l2 | - | Line voltage L2 (V) - not mapped yet |
| voltage_l3 | - | Line voltage L3 (V) - not mapped yet |
| current | Field 2 | Charging current L1 (A) |
| current_l2 | - | Charging current L2 (A) - not mapped yet |
| current_l3 | - | Charging current L3 (A) - not mapped yet |
| power | Calculated | V × I (W) |
| temperature_station | Field 8 | Station temperature (°C) |
| session_energy | Field 4 | Session energy (kWh) |
| session_time | Calculated | From timestamp (minutes) |
| device_id | Field 100 | Device identifier |
| model | Field 4.2 | Device model |
| manufacturer | Field 4.4 | Manufacturer |
| firmware | Field 4.5 | Firmware version |

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
- **Discovery**: UDP broadcast on port 48890/48899
- **Message format**: Protobuf with nested fields
- **Telemetry path**: Field 16 → Field 10 (DataVendorStatusReq)

## Dependencies

- Python 3.6+
- No external dependencies (uses only stdlib)

## Development Environment

```bash
# Activate the virtual environment
source .venv/bin/activate

# Run tests
python -m pytest tests/

# Install in development mode
pip install -e .
```

## Notes

- Device ID can be found via discovery or in the official Duosida app
- CLI auto-discovers charger if --host/--device-id not provided
- Use -v/--verbose for debug output
