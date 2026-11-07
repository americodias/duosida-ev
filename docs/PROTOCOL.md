# Duosida local protocol

Notes from captures of the vendor app (orange icon, local mode) talking to a
**DUOSIDA Mode3@32A** (vendor `UCHEN`, firmware `V2.5@46ab776+266`) on
2025-11-18: a power cycle with remote start and stop, and one capture per
setting. Nothing here comes from a vendor specification.

**Confidence:** *verified* means repeated in captures and consistent across
them, or confirmed by the library on the same charger (*live*, 2026-11-06,
while charging); *inferred* means one observation or a match with the OCPP
standard; *unknown* means kept raw.

## Transport

| Item | Value | |
|---|---|---|
| TCP port | 9988, charger listens | verified |
| Discovery | UDP probe `smart_chargepile_search\0` from port 48890 to 48899; reply `IP,MAC,type,firmware` | verified |
| Encoding | protobuf, no `.proto`, no length prefix | verified |

## Messages are OCPP 1.6

Every message, in both directions, is exactly three protobuf fields:

```text
<action> { payload }    100: charge box id (string)    101: message id (varint)
```

The **action field numbers are the OCPP 1.6 actions in alphabetical order**,
with the confirmation at `2k − 1` and the request at `2k`:

| k | Action | conf | req | Direction of req | |
|---:|---|---:|---:|---|---|
| 2 | BootNotification | 3 | 4 | charger → app | verified |
| 5 | ChangeConfiguration | 9 | 10 | app → charger | verified |
| 8 | DataTransfer | 15 | 16 | both | verified |
| 15 | Heartbeat | 29 | 30 | charger → app | verified |
| 16 | MeterValues | 31 | 32 | charger → app | verified |
| 17 | RemoteStartTransaction | 33 | 34 | app → charger | verified |
| 18 | RemoteStopTransaction | 35 | 36 | app → charger | verified |
| 23 | StartTransaction | 45 | 46 | charger → app | verified |
| 24 | StatusNotification | 47 | 48 | charger → app | verified |
| 25 | StopTransaction | 49 | 50 | charger → app | verified |
| 26 | TriggerMessage | 51 | 52 | app → charger | verified |

So the charger is the OCPP **charge point** and whoever connects (the app, or
this library) is the **central system**. Fields inside each payload follow the
OCPP field order (e.g. `BootNotification.req`: 2 model, 3 serial, 4 vendor,
5 firmware).

- **Framing.** A message ends right after its field 101. This held for all 512
  messages in seven captures, including TCP segments that carried several
  messages or part of one. See `duosida_ev/framing.py`. *Verified.*
- **Message ids.** A confirmation echoes its request's id. The app numbers from
  1 upwards; the charger numbers its own requests from about `0x08000000`.
  *Verified.*
- **Charge box id** (field 100) is the 19-digit serial. The app's first message
  uses `IOS` and id 0 because it does not know the serial yet. *Verified.*
- **Unanswered requests are retransmitted** by the charger after about 4–15 s
  (seen for `Heartbeat`, `DataTransfer` and `StartTransaction`). *Verified.*
- **The charger sometimes ignores a request.** Live, a
  `DataPullAllConfigurationReq` sent right after a `ChangeConfiguration` got no
  answer in 15 s, while every other message flowed normally. The vendor app
  resends unanswered requests with the same message id after about 0.7 s, and
  the library resends every 2 s (`retry_interval`). *Verified.*

## Sessions

Not verified on our unit; these are reports from
[ZaBug/duosida-lan](https://github.com/ZaBug/duosida-lan) (MIT), observed on
a real charger:

- The charger has **a single TCP session slot**, still busy for about 30 s
  after a close.
- Fast reconnects were refused for minutes.
- A close with a **RST** (unread data) brought refusals for over 2 h.
- A second parallel session once **locked the port until a power cycle**.

Hence this library keeps one persistent session and closes gracefully: FIN,
then reading until the charger's FIN, then close. Live, the charger did not
answer our FIN within 3 s and kept sending, so the library closes after
`min(3 s, timeout)` anyway. New connections were accepted a minute later both
times. It leaves reconnect
back-off to the caller (the CLI waits 60, 120, then 300 s). Do not run two
clients at once, e.g. this library next to the Home Assistant integration or
the vendor app.

duosida-ha 1.0 (on duosida-ev 0.1.x) connects, reads one status and
disconnects every poll (10 s). Each poll resets the clock (see below) and
closes a socket with unread data, which is a RST. This is a likely cause of
its bursts of `unavailable`; not yet confirmed.

## Session start

```text
app → TriggerMessage.req {1: 0 (BootNotification), 2: 0}  id 0, charge box "IOS"
charger → TriggerMessage.conf {1: 0}
charger → BootNotification.req {2: model, 3: serial, 4: vendor, 5: firmware}
app → BootNotification.conf {1: currentTime, 2: interval 0, 3: status 0}
```

**`BootNotification.conf.currentTime` is a zigzag `sint64` Unix time, and the
charger sets its clock from it** (*verified*, also live: the timestamps in
`DataContinueReq` jumped from 2025-11-17 to the real time on connecting). Sessions that start after the
app's answer carry the right start time in status field 18.

duosida-ev 0.1.x replayed a captured `BootNotification.conf` whose time was
fixed at 2025-11-17 17:21:19 UTC, so every connection wound the charger's
clock back to that moment. Session start times then fell on 2025-11-17, and
"session time" grew by a day per day (about 8 500 h a year later). The
captures show it directly: a session recorded as starting at 17:22:56 that
day, 97 s after the fixed time, was really in progress on 2025-11-18. 0.2 sends
the current time.

`Heartbeat.conf.currentTime`, in contrast, is a plain varint (*verified*: the
app's values are the real time).

## Keep-alive and acknowledgements

The app sends `TriggerMessage(requestedMessage = 3, Heartbeat)` every 5 s; the
charger answers with `TriggerMessage.conf` and a `Heartbeat.req`, which the
app confirms with the time. The app confirms **every** request from the
charger:

| Charger request | App's confirmation |
|---|---|
| `Heartbeat.req` (empty) | `{1: unix time}` |
| `StatusNotification.req` | empty |
| `MeterValues.req` | empty |
| `StopTransaction.req` | empty |
| `StartTransaction.req` | `{1: idTagInfo {3: 0 Accepted}, 2: transactionId}` (the app assigns the id) |
| `DataTransfer.req` with data in field *n* | `{1: 0 Accepted, n+1: empty}` |

The library does the same unless `respond_to_charger=False`, and sends the
heartbeat trigger every 30 s.

## Status: `DataTransfer` / `DataVendorStatusReq`

`DataTransfer.req {1: "smartchargepile.x-cheng.com", 2: messageId, n: data}`.
Each `messageId` carries its data in a fixed field, and the confirmation uses
the next one:

| messageId | data field | from | |
|---|---:|---|---|
| `DataVendorStatusReq` | 10 | charger, about every 10 s | verified |
| `DataContinueReq` | 12 | charger: sample series (voltage, current, temperature, and the CP waveform at ~+6/−12 V) | verified (not decoded) |
| `DataPullAllConfigurationReq` | 14 | app → charger | verified |
| `DataPullAllChargeProfileReq` | 16 | app → charger | verified (not decoded) |

`DataVendorStatusReq` payload:

| Field | Type | Meaning | |
|---:|---|---|---|
| 1 | float | voltage, V | verified |
| 2 | float | current, A | verified |
| 3 | float | the charger's energy register, kWh (26.8 → 29.5 over the day) | verified |
| 4 | float | session energy, kWh | verified |
| 5 | float | about −12.4 while the CP PWM runs, 0 otherwise: likely the CP negative level | inferred |
| 7 | float | 50.0, constant: maybe the mains frequency | unknown |
| 8 | float | station temperature, °C | verified |
| 9 | float | CP voltage: ~11.8 no car, ~8.8 connected, ~5.9 charging | verified |
| 10 | float | 1000.0, constant: likely the CP PWM frequency (IEC 61851: 1 kHz) | inferred |
| 11 | float | 1.0 without PWM; while it runs, always a multiple of 1/127 (e.g. 20/127, 21/127): likely the share of high samples in the charger's own 127-sample CP window, i.e. the measured PWM duty | inferred |
| 12 | varint | 1 no car, 5 connected, 6 charging | inferred (CP state) |
| 15, 16 | float | ~12.0 and ~5.0: likely supply rails | inferred |
| 17 | varint | state, OCPP `ChargePointStatus` (0 Available, 2 Charging, 4 SuspendedEV, 5 Finishing seen) | verified |
| 18 | varint | session start, Unix time by the charger's clock; 0 when none | verified |
| 20 | varint | slowly rising counter | unknown |
| 21 | varint (zigzag) | transaction id; −1 when none (equals `StartTransaction.conf` / `MeterValues` id) | verified |
| 22 | string | id tag: `DirectWorkUser` (plug and charge), `XC_Remote_Tag` (remote start) | verified |

`StatusNotification.req`: 1 connector (1), 2 error code (6 = `NoError`),
3 info, 4 status (the same codes as field 17). The charger sends one on every
state change, e.g. Preparing → SuspendedEV → Charging after a remote start.

`MeterValues.req`: 1 connector, 2 transaction id, 3 meter value {1 time,
2 sampled values {1 value as text, 2 context, 4 measurand, 7 unit}}. Measurands
seen: 14 current (A), 5 interval energy and 1 register energy (kWh), 9 power (W),
19 temperature (°C), 17 voltage (V). Not decoded by the library beyond the
transaction id.

## Commands

| Command | Request | Confirmation | |
|---|---|---|---|
| Start | `RemoteStartTransaction.req {1: connector 1, 2: {1: "XC_Remote_Tag"}}` | `{1: 0 Accepted / 1 Rejected}` | verified |
| Stop | `RemoteStopTransaction.req {1: transactionId}` | `{1: status}` | verified with the real id |
| Setting | `ChangeConfiguration.req {1: key, 2: value}` | `{1: 0 Accepted, 1 Rejected, 2 RebootRequired, 3 NotSupported}` | verified (Accepted) |

The CP samples in `DataContinueReq` (series 3, 127 samples per message) show
the PWM duty the charger advertises. Live, with the limit at 8 A, the high
share was about 15 % (± 1.5 %: one window holds under two periods). That is at
least the 13.3 % IEC 61851 gives for 8 A. The car drew 6.4 A, confirmed by an
independent meter: EVs may draw less than the pilot allows.

`VendorMaxWorkCurrent` was verified live: `"12"` (sent by duosida-ha), then
`"10"` and `"8"` from this library, were each Accepted, applied and read back
in configuration field 3. The car's current followed within one status push
(9.97 → 8.13 → 6.33 A).

The app sends setting values as integers (`"85"`, `"280"`, `"110"`, `"0"`/`"1"`)
for `VendorMaxWorkTemperature`, `VendorMaxWorkVoltage`, `VendorMinWorkVoltage`,
`ConnectionTimeOut`, `VendorDirectWorkMode` and
`StopTransactionOnEVSideDisconnect`. All were Accepted, although the charger
stores the first three as floats. `VendorMaxWorkCurrent` and `VendorLEDStrength`
were not captured. The library sends integers for them too and reports the
charger's answer. ZaBug reports `VendorLEDStrength` as Rejected over the local
protocol on firmware V2.5 for every value tried. It also reports the
remote-start idTag as a plain string; the vendor app on our unit sends it
nested (`{1: "XC_Remote_Tag"}`), and the library follows the app.

Live (10-minute soak, two stop/start cycles while charging):

- Stop with no transaction known: the session had been started without anyone
  answering `StartTransaction`, so the library sent a made-up id. It was
  Accepted, and charging stopped within 2 s (Finishing, 0 A).
- Start: Accepted. The library answered `StartTransaction` with its own
  transaction id, which then appeared in status field 21. Charging resumed
  after 16–55 s, depending on the car.
- Stop with the real transaction id: Accepted, stopped within 2 s, followed by
  `StopTransaction.req`.
- `VendorLEDStrength = 3` and `StopTransactionOnEVSideDisconnect = 1` (the
  values already set) were Accepted.
- Of 14 configuration pulls, some were ignored at first; resending with the
  same id recovered all of them (6 resends). The charger's clock stayed within
  1.3 s of real time, and a session started after connecting had the correct
  start time.

Remote start answers `Accepted` before anything happens. The charger then
sends `StatusNotification` Preparing → SuspendedEV → Charging over about 2 s,
then `StartTransaction.req`.

## Configuration

`DataPullAllConfigurationReq` is answered with `DataTransfer.conf {1: 0, 15:
{...}}`, about 50 fields. Some OCPP keys are recognisable by value:
`MeterValuesSampledData` (22), `MeterValuesAlignedData` (23),
`StopTxnSampledData` (30), `StopTxnAlignedData` (31) and
`SupportedFeatureProfiles` (32: `Core,LocalAuthListManagement,Reservation,SmartCharging,RemoteTrigger`).

The vendor settings are **inferred from one pull** by matching values against
each setting's allowed range:

| Field | Value | Probably | |
|---:|---:|---|---|
| 1 | 90.0 | `VendorMaxWorkTemperature` (85–95) | inferred |
| 2 | 280.0 | `VendorMaxWorkVoltage` (265–290) | inferred |
| 3 | 16.0 | `VendorMaxWorkCurrent` (6–32) | **verified live** (12 → 10 → 8) |
| 4 | 80.0 | `VendorMinWorkVoltage` (70–110) | inferred (read 110 live, the value Home Assistant had set) |
| 34 | 120 | maybe `ConnectionTimeOut`, though 17, 19 and 24 also fit 30–900 | unknown |

Booleans such as direct mode and stop-on-disconnect are among fields 10–15,
20, 21, 29, 35, 36 and 39, but are not mapped. Other projects report field 10
as direct mode, fields 7 and 9 as Wi-Fi passwords (empty on our unit) and
field 8 as the SSID, which fits the value on our unit. The library redacts all
three in its output, and they are scrubbed from the fixtures. To confirm a mapping: pull,
change one setting, pull again, and compare (`duosida get-config --json`).
