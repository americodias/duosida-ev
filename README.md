# duosida-ev

Python library for direct TCP control of Duosida EV wall chargers, bypassing the cloud API for local control and monitoring.

## Features

- **Network Discovery**: Find Duosida chargers on your local network
- **Real-time Telemetry**: Read voltage, current, power, temperature, and energy consumption
- **Current Control**: Set maximum charging current (6-32A)
- **No Cloud Required**: Direct TCP communication with the charger

## Installation

```bash
pip install duosida-ev
```

Or install from source:

```bash
git clone https://git.dias.pt/personal/duosida-ev.git
cd duosida-ev
pip install -e .
```

## Quick Start

### Discover Chargers

```python
from duosida_ev import discover_chargers

devices = discover_chargers()
for device in devices:
    print(f"Found: {device['ip']}")
    print(f"  Device ID: {device['device_id']}")
    print(f"  MAC: {device['mac']}")
```

### Get Status

```python
from duosida_ev import DuosidaCharger

charger = DuosidaCharger(
    host="192.168.20.95",
    device_id="0310107112122360374"
)

charger.connect()
status = charger.get_status()
print(f"Voltage: {status.voltage}V")
print(f"Current: {status.current}A")
print(f"Power: {status.power}W")
print(f"State: {status.state}")
charger.disconnect()
```

### Set Maximum Current

```python
charger.connect()
charger.set_max_current(16)  # Set to 16A
charger.disconnect()
```

### Monitor Continuously

```python
def on_status(status):
    print(f"Power: {status.power}W")

charger.connect()
charger.monitor(interval=2.0, callback=on_status)
charger.disconnect()
```

## Command Line Interface

```bash
# Discover chargers on the network
duosida discover

# Get charger status
duosida status --host 192.168.20.95 --device-id YOUR_DEVICE_ID

# Set maximum current
duosida set-current --host 192.168.20.95 --device-id YOUR_DEVICE_ID 16

# Monitor continuously
duosida monitor --host 192.168.20.95 --device-id YOUR_DEVICE_ID
```

## Telemetry Fields

| Field | Description |
|-------|-------------|
| `voltage` | Line voltage (V) |
| `current` | Charging current (A) |
| `power` | Power consumption (W) |
| `temperature_station` | Station temperature (°C) |
| `state` | Connection status (Available, Charging, Finished, etc.) |
| `today_consumption` | Daily energy consumption (kWh) |
| `session_energy` | Current session energy (kWh) |

## Status Codes

| Code | State |
|------|-------|
| 0 | Available |
| 1 | Preparing |
| 2 | Charging |
| 3 | Cooling |
| 4 | SuspendedEV |
| 5 | Finished |
| 6 | Holiday |

## Finding Your Device ID

The device ID can be found:
- On the QR code label on the left side of the charger
- Using the `duosida discover` command (when on the same network)
- In the official Duosida mobile app
- In Home Assistant integration settings

## Requirements

- Python 3.6+
- No external dependencies (uses only standard library)

## Protocol Details

- **Port**: 9988 (TCP)
- **Message Format**: Protobuf
- **Discovery**: UDP broadcast on port 48890/48899

## TODO

Features available in the [cloud API](https://github.com/jello1974/duosidaEV-home-assistant) but not yet implemented:

- [ ] **Start/Stop Charging** - Remote control to start or stop charging session
- [ ] **Direct Work Mode** - Toggle VendorDirectWorkMode setting
- [ ] **LED Brightness** - Adjust display brightness (VendorLEDStrength)
- [ ] **Level Detection** - Configure CheckCpN12V setting
- [ ] **3-Phase Support** - Read voltage/current for L2 and L3 phases
- [ ] **Charging Records** - Retrieve historical charging sessions
- [ ] **Accumulated Energy** - Total lifetime energy consumption (may be cloud-only)

## License

MIT License - see [LICENSE](LICENSE) file for details.

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## Acknowledgments

This library was created by reverse-engineering the TCP communication between the official Duosida app (orange icon, local control) and the charger. Note that Duosida has two apps - the orange one uses direct local communication while the blue one uses the cloud API.

The reverse engineering and code development was mostly done using [Claude Code](https://claude.ai/claude-code) and [Genspark](https://www.genspark.ai/).

**References:**
- [Home Assistant Duosida integration](https://github.com/jello1974/duosidaEV-home-assistant) - Cloud API integration, used as reference for status codes and feature identification
