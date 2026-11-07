# duosida-ev

Async Python library and CLI for local control of Duosida EV wall chargers:
no cloud, no polling. It talks to the charger on TCP port 9988 the way the
vendor app does.

The charger speaks **OCPP 1.6 encoded as protobuf**, with itself as the charge
point and the connecting client as the central system. This library plays the
central-system role: it answers the charger's requests, receives its pushes and
gets a confirmation for every command. See [docs/PROTOCOL.md](docs/PROTOCOL.md).

> **0.2.0 is a rewrite.** The API is now `async` and the sync `DuosidaCharger`
> class is gone. It also fixes a bug that set the charger's clock to
> 2025-11-17 on every connection. See [Migrating from 0.1](#migrating-from-01)
> and the [changelog](CHANGELOG.md).

## Features

- **Async, push-based**: one persistent connection. The charger pushes a
  status snapshot about every 10 s and a `StatusNotification` the moment the
  state changes.
- **Confirmed commands**: start, stop and every setting wait for the charger's
  answer and raise `CommandRejectedError` if it says no.
- **Reads the configuration** (max current, voltage limits...) instead of
  treating settings as write-only (field names provisional).
- **Keeps the charger's clock right**: answers `BootNotification` and
  heartbeats with the current time.
- **Discovery** by UDP broadcast, with directed probes for chargers on another
  VLAN.
- **Typed** (`py.typed`, mypy strict), immutable models, no dependencies.
- Tested against byte-exact messages captured from the vendor app and a real
  charger.

## Installation

```bash
pip install duosida-ev        # Python 3.11+
```

## Quick start

```python
import asyncio
from duosida_ev import DuosidaClient, discover_chargers


async def main() -> None:
    chargers = await discover_chargers()
    print(chargers)

    async with DuosidaClient("192.168.1.100") as charger:
        print(charger.info)                 # model, firmware, device id
        status = await charger.get_status()
        print(status.state, status.power, status.session_energy)

        await charger.set_max_current(16)   # raises if the charger rejects it
        await charger.start_charging()


asyncio.run(main())
```

The device id is no longer a parameter: the charger reports it on connect
(`charger.device_id`).

### Push updates

```python
charger = DuosidaClient("192.168.1.100")
await charger.connect()


def on_change() -> None:            # runs in the event loop; must not block
    if not charger.connected:
        print("connection lost")    # call connect() again to resume
    elif charger.status:
        print(charger.status.state, charger.status.current)


remove = charger.add_listener(on_change)
```

- `charger.status` is the latest `ChargerStatus` (every ~10 s).
- `charger.connector_status` is the latest `StatusNotification`, sent
  immediately on every state change.
- `await charger.wait_for_status()` waits for the next push.

The client sends a `TriggerMessage(Heartbeat)` every 30 s and drops the
connection if nothing arrives for 60 s. Both intervals are constructor options.
It does not reconnect by itself: call `connect()` again.

> **One client at a time.** Chargers have been reported to refuse connections
> for minutes after a quick reconnect, and to lock up until a power cycle with
> two parallel sessions. Keep one long-lived `DuosidaClient`. Back off for a
> minute or more before reconnecting. Do not run it next to another local
> client: the Home Assistant integration, the vendor app or a second script.
> See [Sessions](docs/PROTOCOL.md#sessions).

### Commands and settings

```python
await charger.start_charging()               # RemoteStartTransaction
await charger.stop_charging()                # stops the active transaction
await charger.set_max_current(16)            # 6-32 A
await charger.set_connection_timeout(120)    # 30-900 s
await charger.set_max_temperature(90)        # 85-95 °C
await charger.set_max_voltage(280)           # 265-290 V
await charger.set_min_voltage(90)            # 70-110 V
await charger.set_direct_work_mode(True)     # plug and charge
await charger.set_led_brightness(3)          # 0 off, 1 low, 3 high
await charger.set_stop_on_disconnect(True)
await charger.set_config("AnyKey", "value")  # raw ChangeConfiguration
```

Setters return `ConfigurationStatus.ACCEPTED` or `REBOOT_REQUIRED`. They raise
`ValidationError` for out-of-range values (nothing is sent) and
`CommandRejectedError` when the charger refuses. Every call can raise
`DuosidaTimeoutError` or `DuosidaConnectionError`. All of them derive from
`DuosidaError`.

`start_charging()` returning means the charger *accepted* the command. Whether
the car draws power shows in `status`.

### Reading the configuration

```python
config = await charger.get_configuration()
print(config.max_current, config.max_voltage)   # provisional names
print(config.raw)                               # every field, by number
```

The charger returns about 50 unnamed fields. `max_current` is verified on a
real charger; the other three named properties are inferred and still need
confirming ([details](docs/PROTOCOL.md#configuration)).

### Discovery

```python
await discover_chargers()                                  # broadcast, 3 s
await discover_chargers(targets=["192.168.1.255"])        # charger on another VLAN
await discover_chargers(identify=False)                    # UDP only, no TCP
```

The probe is repeated every second during the timeout. Identification opens a
short TCP session that reads the `BootNotification` and answers nothing.

## Command line

```bash
duosida discover [--target 192.168.1.255] [--json]
duosida status [--host IP] [--json]
duosida monitor [--duration 60] [--json]
duosida start
duosida stop [--transaction-id N]
duosida get-config [--json]
duosida set-current 16
duosida set-timeout 120
duosida set-max-temp 90
duosida set-max-voltage 280
duosida set-min-voltage 90
duosida set-direct-mode on
duosida set-led-brightness 3
duosida set-stop-on-disconnect off
duosida config KEY VALUE
```

Without `--host`, the CLI uses discovery when exactly one charger answers.
`-v` logs every message.

## Status fields

`ChargerStatus` decodes `DataVendorStatusReq`, the snapshot the charger pushes
about every 10 s.

| Field | Source | Unit / meaning |
|---|---|---|
| `state`, `raw_state` | field 17 | `ChargerState` (below) and its code |
| `voltage` | field 1 | V |
| `current` | field 2 | A |
| `power` | computed | `voltage × current`, W |
| `total_energy` | field 3 | the charger's own energy register, kWh |
| `session_energy` | field 4 | kWh; 0 when the plug is removed |
| `temperature` | field 8 | °C |
| `cp_voltage` | field 9 | Control Pilot: 12 V no car, 9 V connected, 6 V charging |
| `session_start` | field 18 | UTC `datetime` by the charger's clock, or `None` |
| `transaction_id` | field 21 | active OCPP transaction (zigzag), or `None` |
| `id_tag` | field 22 | `DirectWorkUser`, `XC_Remote_Tag`... |
| `vehicle_connected` | computed | CP voltage < 10.5 V |
| `session_duration` | computed | `received_at - session_start` |
| `unknown` | other fields | kept raw for diagnostics |

`to_dict()` returns all of these as JSON-friendly values.

## States

The codes are OCPP 1.6 `ChargePointStatus` values, used both in the snapshot
and in `StatusNotification`.

| Code | `ChargerState` | Seen on a real charger |
|---:|---|---|
| 0 | `Available` | yes |
| 1 | `Preparing` | yes (after a remote start) |
| 2 | `Charging` | yes |
| 3 | `SuspendedEVSE` | |
| 4 | `SuspendedEV` | yes (for a few seconds after plugging in) |
| 5 | `Finishing` | yes (stopped, car still plugged in) |
| 6 | `Reserved` | |
| 7 | `Unavailable` | |
| 8 | `Faulted` | |

0.1.x named codes 3, 5 and 6 "Cooling", "Finished" and "Holiday", after the
cloud API's labels.

## Migrating from 0.1

| 0.1 | 0.2 |
|---|---|
| `DuosidaCharger(host, device_id=...)` + `connect()` | `async with DuosidaClient(host)` (device id comes from the charger) |
| `get_status()` (blocking read, may return `None`) | `await get_status()` / `status` / `add_listener()` |
| `set_*()` → `bool` (sent, unconfirmed) | `await set_*()` → `ConfigurationStatus`, raises on rejection |
| `discover_chargers()` → `list[dict]` | `await discover_chargers()` → `list[DiscoveredCharger]` |
| `status.conn_status` | `status.raw_state` (`status.state` is a `ChargerState`) |
| `status.temperature_station` | `status.temperature` |
| `to_dict()["session_time"]` (minutes) | `status.session_duration` (`timedelta`) |
| `ConnectionError`, `TimeoutError` (shadowed the built-ins) | `DuosidaConnectionError`, `DuosidaTimeoutError` |
| `voltage2/3`, `current2/3` (always 0) | removed |
| `monitor()` | `add_listener()` / `wait_for_status()` |

## Development

```bash
uv run --with pytest pytest          # 114 tests, Python 3.11-3.14
uvx ruff check src tests tools
uvx mypy --strict src
```

`tests/fixtures/captures.json` holds sanitized messages from captures of the
vendor app driving a real charger. `tools/extract_fixtures.py` rebuilds it, and
`tools/pcap.py` decodes any capture:

```bash
python tools/pcap.py charger_capture.pcap
```

## Capturing Network Traffic

To capture traffic between the Duosida app and the charger, use Wireshark on a computer on the same network, or tcpdump on your router or access point. Decode the result with `python tools/pcap.py capture.pcap`.

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

## License

MIT License - see [LICENSE](LICENSE).

## Acknowledgments

This library comes from reverse-engineering the TCP traffic between the
official Duosida app (orange icon, local control) and the charger. Duosida has
two apps: the orange one talks to the charger directly, the blue one uses the
cloud API.

The reverse engineering and code were mostly done with
[Claude Code](https://claude.ai/claude-code) and
[Genspark](https://www.genspark.ai/).

**References:**

- [jello1974/duosidaEV-home-assistant](https://github.com/jello1974/duosidaEV-home-assistant):
  the cloud API integration, used for the original status labels.
- [matiaskunin/duosida-local](https://github.com/matiaskunin/duosida-local)
  (GPL-3.0): an independent async client whose observations prompted parts of
  0.2. These were the charger's own energy register (field 3), stream framing,
  repeated discovery probes and the Control Pilot connection test. No code was
  taken from it; every behaviour here was checked against our own captures.
