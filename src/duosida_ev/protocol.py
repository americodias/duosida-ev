"""The charger's message protocol: OCPP 1.6 carried as protobuf over TCP.

Every message is three protobuf fields::

    <action field> { payload }   100: charge box id   101: message id

The action field numbers are the OCPP 1.6 actions in alphabetical order, with
the confirmation at ``2k - 1`` and the request at ``2k`` (BootNotification is
action 2: conf 3, req 4). A confirmation echoes the message id of its request.
The charger acts as the charge point; whoever connects to TCP port 9988 (the
vendor app, or this library) plays the central system.

Only actions seen in captures of the vendor app are defined here. See
``docs/PROTOCOL.md`` for the evidence behind each field.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import IntEnum
from typing import ClassVar

from .exceptions import ProtocolError
from .models import (
    ChargerConfiguration,
    ChargerInfo,
    ChargerState,
    ChargerStatus,
    ConnectorStatus,
    RawField,
    Scalar,
)
from .protobuf import (
    Field,
    Message,
    WireType,
    bytes_field,
    sint_field,
    string_field,
    varint_field,
)

DEFAULT_PORT = 9988
CHARGE_BOX_ID_FIELD = 100
MESSAGE_ID_FIELD = 101

#: ``vendorId`` of every ``DataTransfer`` message.
VENDOR_ID = "smartchargepile.x-cheng.com"
#: ``idTag`` the vendor app uses for remote starts.
REMOTE_ID_TAG = "XC_Remote_Tag"
#: Charge box id the vendor app uses before it knows the real one.
ANONYMOUS_CHARGE_BOX_ID = "IOS"

ACCEPTED = 0


class Action(IntEnum):
    """OCPP 1.6 actions observed on the wire, by alphabetical index."""

    BOOT_NOTIFICATION = 2
    CHANGE_CONFIGURATION = 5
    DATA_TRANSFER = 8
    HEARTBEAT = 15
    METER_VALUES = 16
    REMOTE_START_TRANSACTION = 17
    REMOTE_STOP_TRANSACTION = 18
    START_TRANSACTION = 23
    STATUS_NOTIFICATION = 24
    STOP_TRANSACTION = 25
    TRIGGER_MESSAGE = 26

    @property
    def request(self) -> int:
        """Outer field number of the ``.req`` message."""
        return 2 * self.value

    @property
    def confirmation(self) -> int:
        """Outer field number of the ``.conf`` message."""
        return 2 * self.value - 1


class TriggerTarget(IntEnum):
    """``TriggerMessage.requestedMessage`` values used by the vendor app."""

    BOOT_NOTIFICATION = 0
    HEARTBEAT = 3


class DataMessage:
    """``DataTransfer.messageId`` values and the field holding their data.

    The request carries its data in field *n*, the confirmation in *n + 1*.
    """

    VENDOR_STATUS = "DataVendorStatusReq"
    CONTINUE = "DataContinueReq"
    PULL_ALL_CONFIGURATION = "DataPullAllConfigurationReq"
    PULL_ALL_CHARGE_PROFILE = "DataPullAllChargeProfileReq"

    FIELDS: ClassVar[dict[str, int]] = {
        VENDOR_STATUS: 10,
        CONTINUE: 12,
        PULL_ALL_CONFIGURATION: 14,
        PULL_ALL_CHARGE_PROFILE: 16,
    }


# ------------------------------------------------------------------ envelope


@dataclass(frozen=True, slots=True)
class Frame:
    """One protocol message."""

    field: int
    """Outer field number: the action (even = request, odd = confirmation)."""
    body: bytes
    charge_box_id: str
    message_id: int

    @property
    def is_request(self) -> bool:
        return self.field % 2 == 0

    @property
    def action(self) -> Action | None:
        try:
            return Action((self.field + 1) // 2)
        except ValueError:
            return None

    def message(self) -> Message:
        return Message.decode(self.body)

    def describe(self) -> str:
        """Short text for logs, e.g. ``Heartbeat.req #134224395``."""
        action = self.action
        name = action.name.title().replace("_", "") if action else f"field{self.field}"
        kind = "req" if self.is_request else "conf"
        return f"{name}.{kind} #{self.message_id}"


def encode_frame(field: int, body: bytes, charge_box_id: str, message_id: int) -> bytes:
    """Serialize one message."""
    return (
        bytes_field(field, body)
        + string_field(CHARGE_BOX_ID_FIELD, charge_box_id)
        + varint_field(MESSAGE_ID_FIELD, message_id)
    )


def decode_frame(raw: bytes) -> Frame:
    """Parse one message produced by :class:`~duosida_ev.framing.FrameSplitter`."""
    message = Message.decode(raw)
    if not message.fields:
        raise ProtocolError("empty message")
    action = message.fields[0]
    if action.number in (CHARGE_BOX_ID_FIELD, MESSAGE_ID_FIELD):
        raise ProtocolError("message has no action field")
    charge_box = message.get(CHARGE_BOX_ID_FIELD)
    return Frame(
        field=action.number,
        body=action.as_bytes(),
        charge_box_id=charge_box.as_str() if charge_box else "",
        message_id=message.require(MESSAGE_ID_FIELD).as_int(),
    )


# ------------------------------------------------------------------ builders


def handshake() -> bytes:
    """First message after connecting: ``TriggerMessage(BootNotification)``.

    Byte-identical to what the vendor app sends; the charger answers with a
    ``BootNotification.req`` that carries its identity.
    """
    return encode_frame(
        Action.TRIGGER_MESSAGE.request,
        trigger_message(TriggerTarget.BOOT_NOTIFICATION),
        ANONYMOUS_CHARGE_BOX_ID,
        0,
    )


def trigger_message(target: int, connector_id: int = 0) -> bytes:
    return varint_field(1, target) + varint_field(2, connector_id)


def boot_notification_conf(now: int, interval: int = 0) -> bytes:
    """``BootNotification.conf``: current time (zigzag), interval, Accepted.

    The charger sets its clock from ``currentTime``.
    """
    return sint_field(1, now) + varint_field(2, interval) + varint_field(3, ACCEPTED)


def heartbeat_conf(now: int) -> bytes:
    """``Heartbeat.conf``: current time (plain varint, unlike BootNotification)."""
    return varint_field(1, now)


def data_transfer_conf(data_field: int | None) -> bytes:
    """Acknowledge a ``DataTransfer.req`` the way the vendor app does."""
    body = varint_field(1, ACCEPTED)
    if data_field is not None:
        body += bytes_field(data_field + 1, b"")
    return body


def start_transaction_conf(transaction_id: int) -> bytes:
    """``StartTransaction.conf``: idTagInfo(status=Accepted) and the transaction id."""
    return bytes_field(1, varint_field(3, ACCEPTED)) + varint_field(2, transaction_id)


def change_configuration(key: str, value: str) -> bytes:
    return string_field(1, key) + string_field(2, value)


def remote_start_transaction(connector_id: int = 1, id_tag: str = REMOTE_ID_TAG) -> bytes:
    return varint_field(1, connector_id) + bytes_field(2, string_field(1, id_tag))


def remote_stop_transaction(transaction_id: int) -> bytes:
    return varint_field(1, transaction_id)


def pull_all_configuration() -> bytes:
    """``DataTransfer.req`` asking for the whole configuration."""
    return (
        string_field(1, VENDOR_ID)
        + string_field(2, DataMessage.PULL_ALL_CONFIGURATION)
        + bytes_field(DataMessage.FIELDS[DataMessage.PULL_ALL_CONFIGURATION], b"")
    )


# ------------------------------------------------------------------- parsers


def _text(field: Field | None) -> str:
    return field.as_str() if field is not None else ""


def _float(message: Message, number: int) -> float:
    field = message.get(number)
    return field.as_float() if field is not None else 0.0


def _int(message: Message, number: int) -> int:
    field = message.get(number)
    return field.as_int() if field is not None else 0


def conf_status(body: bytes) -> int:
    """Field 1 of a confirmation; protobuf default (0, Accepted) if absent."""
    return _int(Message.decode(body), 1)


def parse_boot_notification(body: bytes, charge_box_id: str = "") -> ChargerInfo:
    """``BootNotification.req``: model (2), serial (3), vendor (4), firmware (5)."""
    message = Message.decode(body)
    return ChargerInfo(
        device_id=_text(message.get(3)) or charge_box_id,
        model=_text(message.get(2)),
        manufacturer=_text(message.get(4)),
        firmware=_text(message.get(5)),
    )


@dataclass(frozen=True, slots=True)
class DataTransfer:
    """A decoded ``DataTransfer`` request or confirmation."""

    vendor_id: str
    message_id: str
    status: int | None
    data_field: int | None
    data: Field | None


def parse_data_transfer(body: bytes, *, confirmation: bool = False) -> DataTransfer:
    """Split a ``DataTransfer`` into its header and its data field."""
    message = Message.decode(body)
    if confirmation:
        header = {1}
        vendor, message_id, status = "", "", _int(message, 1)
    else:
        header = {1, 2}
        vendor, message_id, status = _text(message.get(1)), _text(message.get(2)), None
    data = next((f for f in message if f.number not in header), None)
    return DataTransfer(
        vendor_id=vendor,
        message_id=message_id,
        status=status,
        data_field=data.number if data else None,
        data=data,
    )


_STATUS_FIELDS = {1, 2, 3, 4, 8, 9, 17, 18, 21, 22}


def parse_vendor_status(data: bytes, received_at: datetime | None = None) -> ChargerStatus:
    """Decode the data of a ``DataVendorStatusReq``."""
    message = Message.decode(data)
    if 17 not in message:
        raise ProtocolError("DataVendorStatusReq without a state (field 17)")
    received = received_at or datetime.now(UTC)
    voltage = _float(message, 1)
    current = _float(message, 2)
    raw_state = _int(message, 17)

    session_start = None
    if (started := _int(message, 18)) > 0:
        try:
            session_start = datetime.fromtimestamp(started, UTC)
        except (OverflowError, OSError, ValueError):
            session_start = None

    transaction_id = None
    if (tx := message.get(21)) is not None and (value := tx.as_sint()) > 0:
        transaction_id = value

    tag = message.get(22)
    id_tag = _id_tag(tag.as_bytes()) if tag is not None else None

    unknown = tuple(
        RawField(field.number, field.scalar())
        for field in message
        if field.number not in _STATUS_FIELDS
    )
    return ChargerStatus(
        state=ChargerState.from_code(raw_state),
        raw_state=raw_state,
        voltage=voltage,
        current=current,
        power=voltage * current,
        total_energy=_float(message, 3),
        session_energy=_float(message, 4),
        temperature=_float(message, 8),
        cp_voltage=_float(message, 9),
        session_start=session_start,
        transaction_id=transaction_id,
        id_tag=id_tag,
        received_at=received,
        unknown=unknown,
    )


def _id_tag(raw: bytes) -> str | None:
    """Field 22 holds the tag as plain text (empty when idle)."""
    if not raw:
        return None
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None
    return text if text.isprintable() else None


def parse_status_notification(body: bytes, received_at: datetime | None = None) -> ConnectorStatus:
    """``StatusNotification.req``: connector (1), errorCode (2), info (3), status (4)."""
    message = Message.decode(body)
    raw_state = _int(message, 4)
    info = _text(message.get(3)) or None
    return ConnectorStatus(
        connector_id=_int(message, 1),
        state=ChargerState.from_code(raw_state),
        raw_state=raw_state,
        error_code=_int(message, 2),
        info=info,
        received_at=received_at or datetime.now(UTC),
    )


def meter_values_transaction_id(body: bytes) -> int | None:
    """``MeterValues.req`` field 2 (transactionId), if present."""
    field = Message.decode(body).get(2)
    return field.as_int() if field is not None and field.wire_type is WireType.VARINT else None


def stop_transaction_id(body: bytes) -> int | None:
    """``StopTransaction.req`` field 4 (transactionId), if present."""
    field = Message.decode(body).get(4)
    return field.as_int() if field is not None and field.wire_type is WireType.VARINT else None


def parse_configuration(body: bytes) -> ChargerConfiguration:
    """Decode the ``DataTransfer.conf`` answering ``DataPullAllConfigurationReq``."""
    transfer = parse_data_transfer(body, confirmation=True)
    if transfer.status not in (None, ACCEPTED):
        raise ProtocolError(f"configuration request answered with status {transfer.status}")
    if transfer.data is None or transfer.data.wire_type is not WireType.LEN:
        raise ProtocolError("configuration answer has no data")
    raw: dict[int, Scalar] = {}
    for field in transfer.data.as_message():
        raw.setdefault(field.number, field.scalar())
    return ChargerConfiguration(raw=raw)
