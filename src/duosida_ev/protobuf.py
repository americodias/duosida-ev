"""Minimal, schema-less Protocol Buffers wire-format codec.

The charger speaks protobuf without a published ``.proto``. This module only
knows the wire format; field meanings live in :mod:`duosida_ev.protocol`.
"""

from __future__ import annotations

import struct
from collections.abc import Iterator
from dataclasses import dataclass
from enum import IntEnum

from .exceptions import ProtocolError

_MAX_VARINT_BYTES = 10


class WireType(IntEnum):
    """Protobuf wire types used by the charger."""

    VARINT = 0
    I64 = 1
    LEN = 2
    I32 = 5


class IncompleteError(ProtocolError):
    """The data ends in the middle of a field."""


# --------------------------------------------------------------------- encode


def encode_varint(value: int) -> bytes:
    """Encode a non-negative integer as a base-128 varint."""
    if value < 0:
        raise ValueError("varint must be non-negative")
    out = bytearray()
    while value > 0x7F:
        out.append((value & 0x7F) | 0x80)
        value >>= 7
    out.append(value)
    return bytes(out)


def zigzag_encode(value: int) -> int:
    """Map a signed integer to its ``sint64`` zigzag representation."""
    return (value << 1) ^ (value >> 63)


def zigzag_decode(value: int) -> int:
    """Inverse of :func:`zigzag_encode`."""
    return (value >> 1) ^ -(value & 1)


def _key(number: int, wire_type: WireType) -> bytes:
    if number < 1:
        raise ValueError("field numbers start at 1")
    return encode_varint((number << 3) | wire_type)


def varint_field(number: int, value: int) -> bytes:
    """Encode a varint field (``int32``/``int64``/``uint``/``enum``/``bool``)."""
    return _key(number, WireType.VARINT) + encode_varint(value)


def sint_field(number: int, value: int) -> bytes:
    """Encode a zigzag ``sint64`` field."""
    return varint_field(number, zigzag_encode(value))


def bytes_field(number: int, value: bytes) -> bytes:
    """Encode a length-delimited field (bytes or a nested message)."""
    return _key(number, WireType.LEN) + encode_varint(len(value)) + value


def string_field(number: int, value: str) -> bytes:
    """Encode a UTF-8 string field."""
    return bytes_field(number, value.encode("utf-8"))


def float_field(number: int, value: float) -> bytes:
    """Encode a 32-bit float field."""
    return _key(number, WireType.I32) + struct.pack("<f", value)


# --------------------------------------------------------------------- decode


def decode_varint(data: bytes | bytearray | memoryview, offset: int = 0) -> tuple[int, int]:
    """Decode a varint at ``offset``; return ``(value, next_offset)``."""
    result = 0
    for index in range(_MAX_VARINT_BYTES):
        if offset >= len(data):
            raise IncompleteError("truncated varint")
        byte = data[offset]
        offset += 1
        result |= (byte & 0x7F) << (7 * index)
        if not byte & 0x80:
            return result, offset
    raise ProtocolError("varint longer than 10 bytes")


def read_field(
    data: bytes | bytearray | memoryview, offset: int
) -> tuple[int, WireType, int | bytes, int]:
    """Read one field at ``offset``; return ``(number, wire_type, value, end)``.

    Raises :class:`IncompleteError` if ``data`` ends inside the field and
    :class:`ProtocolError` if the bytes cannot be a protobuf field.
    """
    key, offset = decode_varint(data, offset)
    number, raw_type = key >> 3, key & 0x07
    if number == 0:
        raise ProtocolError("field number 0 is invalid")
    try:
        wire_type = WireType(raw_type)
    except ValueError:
        raise ProtocolError(f"unsupported wire type {raw_type}") from None
    value: int | bytes
    if wire_type is WireType.VARINT:
        value, offset = decode_varint(data, offset)
        return number, wire_type, value, offset
    if wire_type is WireType.LEN:
        length, offset = decode_varint(data, offset)
        size = length
    elif wire_type is WireType.I32:
        size = 4
    else:
        size = 8
    end = offset + size
    if end > len(data):
        raise IncompleteError("truncated field")
    return number, wire_type, bytes(data[offset:end]), end


@dataclass(frozen=True, slots=True)
class Field:
    """One decoded field. The meaning of ``value`` depends on the schema."""

    number: int
    wire_type: WireType
    value: int | bytes

    def as_int(self) -> int:
        """Return a varint field as an unsigned integer."""
        if self.wire_type is not WireType.VARINT or not isinstance(self.value, int):
            raise ProtocolError(f"field {self.number} is not a varint")
        return self.value

    def as_sint(self) -> int:
        """Return a varint field decoded as zigzag ``sint64``."""
        return zigzag_decode(self.as_int())

    def as_float(self) -> float:
        """Return a fixed32/fixed64 field as a float."""
        if self.wire_type is WireType.I32 and isinstance(self.value, bytes):
            return float(struct.unpack("<f", self.value)[0])
        if self.wire_type is WireType.I64 and isinstance(self.value, bytes):
            return float(struct.unpack("<d", self.value)[0])
        raise ProtocolError(f"field {self.number} is not a float")

    def as_bytes(self) -> bytes:
        """Return a length-delimited field as bytes."""
        if self.wire_type is not WireType.LEN or not isinstance(self.value, bytes):
            raise ProtocolError(f"field {self.number} is not length-delimited")
        return self.value

    def as_str(self) -> str:
        """Return a length-delimited field as UTF-8 text."""
        try:
            return self.as_bytes().decode("utf-8")
        except UnicodeDecodeError as err:
            raise ProtocolError(f"field {self.number} is not valid UTF-8") from err

    def as_message(self) -> Message:
        """Decode a length-delimited field as a nested message."""
        return Message.decode(self.as_bytes())

    def scalar(self) -> int | float | str | bytes:
        """Best-effort plain value, for logging and unknown-field dumps."""
        if self.wire_type is WireType.VARINT:
            return self.as_int()
        if self.wire_type in (WireType.I32, WireType.I64):
            return self.as_float()
        raw = self.as_bytes()
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            return raw
        return text if text.isprintable() else raw


class Message:
    """A decoded message: fields in wire order, repeats preserved."""

    __slots__ = ("fields",)

    def __init__(self, fields: tuple[Field, ...]) -> None:
        self.fields = fields

    @classmethod
    def decode(cls, data: bytes) -> Message:
        """Decode ``data`` completely; trailing garbage is an error."""
        fields: list[Field] = []
        offset = 0
        while offset < len(data):
            try:
                number, wire_type, value, offset = read_field(data, offset)
            except IncompleteError as err:
                raise ProtocolError(f"truncated message: {err}") from err
            fields.append(Field(number, wire_type, value))
        return cls(tuple(fields))

    def __iter__(self) -> Iterator[Field]:
        return iter(self.fields)

    def __len__(self) -> int:
        return len(self.fields)

    def __contains__(self, number: object) -> bool:
        return any(field.number == number for field in self.fields)

    def __repr__(self) -> str:
        inner = ", ".join(f"{f.number}={f.scalar()!r}" for f in self.fields)
        return f"Message({inner})"

    def get(self, number: int) -> Field | None:
        """Return the first field with ``number``, or ``None``."""
        return next((field for field in self.fields if field.number == number), None)

    def get_all(self, number: int) -> list[Field]:
        """Return every field with ``number``, in wire order."""
        return [field for field in self.fields if field.number == number]

    def require(self, number: int) -> Field:
        """Return the first field with ``number`` or raise :class:`ProtocolError`."""
        field = self.get(number)
        if field is None:
            raise ProtocolError(f"missing field {number}")
        return field
