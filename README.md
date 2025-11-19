# duosida-ev

Python library for direct TCP control of Duosida EV wall chargers, bypassing the cloud API for local control and monitoring.

## Features

- **Network Discovery**: Find Duosida chargers on your local network
- **Real-time Telemetry**: Read voltage, current, power, temperature, and energy consumption
- **Current Control**: Set maximum charging current (6-32A)
- **Start/Stop Charging**: Remote control to start or stop charging sessions
- **Configuration Settings**: Connection timeout, max/min voltage, max temperature, direct work mode
- **JSON Export**: Get telemetry data as JSON for integration with other tools
- **No Cloud Required**: Direct TCP communication with the charger

## Installation

```bash
pip install duosida-ev
```

Or install from source:

```bash
git clone https://github.com/americodias/duosida-ev.git
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
    host="192.168.1.100",
    device_id="YOUR_DEVICE_ID"
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

### Configure Charger Settings

```python
charger.connect()

# Connection timeout (30-900 seconds)
charger.set_connection_timeout(120)

# Temperature limits (85-95°C)
charger.set_max_temperature(90)

# Voltage limits
charger.set_max_voltage(280)    # 265-290V
charger.set_min_voltage(90)     # 70-110V

# Direct work mode (plug and charge)
charger.set_direct_work_mode(True)   # Enable
charger.set_direct_work_mode(False)  # Disable

# LED brightness (0=off, 1=low, 3=high)
charger.set_led_brightness(3)

# Generic config (any key/value)
charger.set_config("VendorMaxWorkCurrent", "16")

charger.disconnect()
```

### Start/Stop Charging

```python
charger.connect()

# Start charging
charger.start_charging()

# Stop charging
charger.stop_charging()

charger.disconnect()
```

### Get Telemetry as JSON

```python
import json

charger.connect()
status = charger.get_status()

# Convert to dictionary
data = status.to_dict()
print(json.dumps(data, indent=2))

# Access individual fields
print(f"CP Voltage: {status.cp_voltage}V")
print(f"State: {status.state}")

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
duosida status --host 192.168.1.100 --device-id YOUR_DEVICE_ID

# Get status in JSON format
duosida status --host 192.168.1.100 --device-id YOUR_DEVICE_ID --json

# Set maximum current
duosida set-current --host 192.168.1.100 --device-id YOUR_DEVICE_ID 16

# Start charging
duosida start --host 192.168.1.100 --device-id YOUR_DEVICE_ID

# Stop charging
duosida stop --host 192.168.1.100 --device-id YOUR_DEVICE_ID

# Monitor continuously
duosida monitor --host 192.168.1.100 --device-id YOUR_DEVICE_ID

# Configuration commands (host only - device ID auto-discovered)
duosida set-timeout --host 192.168.1.100 120          # 30-900 seconds
duosida set-max-temp --host 192.168.1.100 90          # 85-95°C
duosida set-max-voltage --host 192.168.1.100 280      # 265-290V
duosida set-min-voltage --host 192.168.1.100 90       # 70-110V
duosida set-direct-mode --host 192.168.1.100 on       # on/off
duosida set-led-brightness --host 192.168.1.100 3     # 0=off, 1=low, 3=high
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

See [Settings Reference](docs/SETTINGS.md) for detailed documentation on charger configuration options (Direct Work Mode, Level Detection, etc.).

## Capturing Network Traffic

To capture traffic between the Duosida app and charger for reverse engineering, you can use Wireshark on a computer connected to the same network, or use tcpdump on your router if it supports SSH access.

### Using Wireshark

1. Install [Wireshark](https://www.wireshark.org/)
2. Connect your computer to the same network as the charger
3. Start capture with filter: `host <charger-ip> and tcp port 9988`
4. Use the Duosida app to interact with the charger
5. Stop capture and save the `.pcap` file

### Using tcpdump on Router

If your router supports SSH (OpenWrt, DD-WRT, etc.):

```bash
ssh root@<router-ip>

# Capture traffic to/from charger
tcpdump -i br-lan -w /tmp/charger_capture.pcap \
  'host <charger-ip> and tcp port 9988'
```

Download the capture:

```bash
scp root@<router-ip>:/tmp/charger_capture.pcap ./charger_capture.pcap
```

<details>
<summary>UniFi Dream Machine Pro specific instructions</summary>

For UDM Pro with separate access points, you need to SSH into the Access Point where the phone or charger is connected (not the UDM Pro itself, since local traffic may not pass through the router):

```bash
# SSH into the Access Point (not the UDM Pro)
ssh root@<access-point-ip>

# Capture on the wireless interface (ra2) for your IoT VLAN
tcpdump -i ra2 -w /tmp/charger_capture.pcap \
  'host 192.168.1.100 and host 192.168.1.50 and tcp port 9988'
```

Options:
- `-i ra2` - Interface for 2.4GHz/5GHz AP (wireless)
- `host 192.168.1.100` - Charger IP
- `host 192.168.1.50` - Phone IP (update as needed)

Alternative - capture everything for 30 seconds:

```bash
timeout 30 tcpdump -i ra2 -w /tmp/charger_full.pcap \
  'host 192.168.1.100 and tcp port 9988'
```

Download the capture from the AP:

```bash
scp root@<access-point-ip>:/tmp/charger_capture.pcap ./charger_capture.pcap
```

</details>

## TODO

Features available in the [cloud API](https://github.com/jello1974/duosidaEV-home-assistant) but not yet implemented:

- [x] **Start/Stop Charging** - Remote control to start or stop charging session
- [x] **Level Detection** - CP voltage display based on charging state (IEC 61851-1)
- [x] **Direct Work Mode** - Toggle VendorDirectWorkMode setting (plug and charge)
- [x] **3-Phase Support** - Read voltage/current for L2 and L3 phases (voltage2, voltage3, current2, current3)
- [x] **Configuration Settings** - Connection timeout, max/min voltage, max temperature
- [x] **LED Brightness** - Adjust display brightness (0=off, 1=low, 3=high)
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
