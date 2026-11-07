"""Build ``tests/fixtures/captures.json`` from the private protocol captures.

    python tools/extract_fixtures.py ON_OFF.pcap DIRECT_MODE.pcap MAX_VOLTAGE.pcap

The captures are of the vendor app talking to a real charger (2025-11-18):
a power cycle with remote start/stop, a direct-mode toggle (which also pulls
the configuration) and a max-voltage change. Private values are replaced by
dummies of the same length, so every length prefix stays valid: the charger's
19-digit id everywhere, and the Wi-Fi SSID and password fields (7, 8, 9) of
the configuration answer.
"""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Callable
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from duosida_ev import protocol
from duosida_ev.protobuf import WireType, read_field
from duosida_ev.protocol import Frame
from pcap import CHARGER_PORT, frames, tcp_streams

DUMMY_ID = b"0123456789012345678"
OUT = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "captures.json"


WIFI_FIELDS = (7, 8, 9)  # SSID and password fields of the configuration


def sanitize(data: bytes) -> bytes:
    return re.sub(rb"(?<![0-9])[0-9]{19}(?![0-9])", DUMMY_ID, data)


def _value_span(data: bytes, start: int, end: int, number: int) -> tuple[int, int] | None:
    """Offsets of the value of the first length-delimited field ``number``."""
    offset = start
    while offset < end:
        found, wire_type, value, offset = read_field(data, offset)
        if found == number and wire_type is WireType.LEN:
            assert isinstance(value, bytes)
            return offset - len(value), offset
    return None


def scrub_configuration(raw: bytes) -> bytes:
    """Blank the Wi-Fi fields of a configuration answer, keeping every length."""
    out = bytearray(raw)
    body = _value_span(raw, 0, len(raw), 15)  # DataTransfer.conf
    config = _value_span(raw, *body, 15) if body else None  # its configuration data
    if config is None:
        raise SystemExit("not a configuration answer")
    for number in WIFI_FIELDS:
        span = _value_span(raw, *config, number)
        if span is not None:
            out[span[0] : span[1]] = b"x" * (span[1] - span[0])
    return bytes(out)


def load(path: Path) -> tuple[bytes, bytes]:
    """(charger -> app, app -> charger) streams of the first connection."""
    charger, app = b"", b""
    for (_src, sport, _dst, dport), stream in tcp_streams(path).items():
        if sport == CHARGER_PORT and not charger:
            charger = sanitize(stream)
        elif dport == CHARGER_PORT and not app:
            app = sanitize(stream)
    return charger, app


def pick(stream: bytes, test: Callable[[Frame], bool], what: str) -> str:
    for raw in frames(stream):
        if test(protocol.decode_frame(raw)):
            return raw.hex()
    raise SystemExit(f"no message for {what}")


def is_field(number: int) -> Callable[[Frame], bool]:
    return lambda frame: frame.field == number


def data_transfer(message_id: str, state: int | None = None) -> Callable[[Frame], bool]:
    def test(frame: Frame) -> bool:
        if frame.field != 16:
            return False
        transfer = protocol.parse_data_transfer(frame.body)
        if transfer.message_id != message_id:
            return False
        if state is None or transfer.data is None:
            return state is None
        return protocol.parse_vendor_status(transfer.data.as_bytes()).raw_state == state

    return test


def status_notification(state: int) -> Callable[[Frame], bool]:
    return lambda f: f.field == 48 and protocol.parse_status_notification(f.body).raw_state == state


def main(on_off: str, direct_mode: str, max_voltage: str) -> int:
    boot_w, boot_a = load(Path(on_off))
    cfg_w, _ = load(Path(direct_mode))
    volt_w, volt_a = load(Path(max_voltage))
    vendor = protocol.DataMessage

    messages = {
        # charger -> app
        "trigger_conf": pick(boot_w, is_field(51), "TriggerMessage.conf"),
        "boot_notification": pick(boot_w, is_field(4), "BootNotification.req"),
        "status_charging": pick(boot_w, data_transfer(vendor.VENDOR_STATUS, 2), "VS charging"),
        "status_finishing": pick(boot_w, data_transfer(vendor.VENDOR_STATUS, 5), "VS finishing"),
        "status_suspended_ev": pick(boot_w, data_transfer(vendor.VENDOR_STATUS, 4), "VS EV"),
        "status_available": pick(volt_w, data_transfer(vendor.VENDOR_STATUS, 0), "VS idle"),
        "data_continue": pick(boot_w, data_transfer(vendor.CONTINUE), "DataContinueReq"),
        "status_notification_preparing": pick(boot_w, status_notification(1), "SN preparing"),
        "heartbeat_req": pick(boot_w, is_field(30), "Heartbeat.req"),
        "meter_values": pick(boot_w, is_field(32), "MeterValues.req"),
        "start_transaction_req": pick(boot_w, is_field(46), "StartTransaction.req"),
        "stop_transaction_req": pick(boot_w, is_field(50), "StopTransaction.req"),
        "remote_start_conf": pick(boot_w, is_field(33), "RemoteStartTransaction.conf"),
        "remote_stop_conf": pick(boot_w, is_field(35), "RemoteStopTransaction.conf"),
        "configuration_conf": scrub_configuration(
            bytes.fromhex(
                pick(cfg_w, lambda f: f.field == 15 and f.message_id == 1, "configuration")
            )
        ).hex(),
        "change_configuration_conf": pick(cfg_w, is_field(9), "ChangeConfiguration.conf"),
        # app -> charger
        "app_handshake": pick(boot_a, is_field(52), "handshake"),
        "app_boot_notification_conf": pick(boot_a, is_field(3), "BootNotification.conf"),
        "app_heartbeat_conf": pick(boot_a, is_field(29), "Heartbeat.conf"),
        "app_data_transfer_conf": pick(boot_a, is_field(15), "DataTransfer.conf"),
        "app_start_transaction_conf": pick(boot_a, is_field(45), "StartTransaction.conf"),
        "app_remote_start": pick(boot_a, is_field(34), "RemoteStartTransaction.req"),
        "app_remote_stop": pick(boot_a, is_field(36), "RemoteStopTransaction.req"),
        "app_pull_configuration": pick(
            boot_a, data_transfer(vendor.PULL_ALL_CONFIGURATION), "pull configuration"
        ),
        "app_change_configuration": pick(volt_a, is_field(10), "ChangeConfiguration.req"),
        "app_trigger_heartbeat": pick(
            volt_a, lambda f: f.field == 52 and f.message_id != 0, "TriggerMessage(Heartbeat)"
        ),
    }
    fixture = {
        "source": "vendor app <-> DUOSIDA Mode3@32A, firmware V2.5@46ab776+266, 2025-11-18",
        "device_id": DUMMY_ID.decode(),
        "streams": {"power_cycle_charger": boot_w.hex(), "power_cycle_app": boot_a.hex()},
        "messages": messages,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(fixture, indent=1) + "\n")
    print(f"wrote {OUT} ({len(messages)} messages)")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 4:
        raise SystemExit(__doc__)
    sys.exit(main(*sys.argv[1:]))
