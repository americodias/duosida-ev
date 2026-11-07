import struct
import unittest

from duosida_ev.exceptions import ProtocolError
from duosida_ev.protobuf import (
    Field,
    IncompleteError,
    Message,
    WireType,
    bytes_field,
    decode_varint,
    encode_varint,
    float_field,
    sint_field,
    string_field,
    varint_field,
    zigzag_decode,
    zigzag_encode,
)


class VarintTest(unittest.TestCase):
    def test_round_trip(self) -> None:
        for value in (0, 1, 127, 128, 300, 2**31, 134224369, 2**63):
            encoded = encode_varint(value)
            self.assertEqual(decode_varint(encoded), (value, len(encoded)))

    def test_known_encodings(self) -> None:
        self.assertEqual(encode_varint(0), b"\x00")
        self.assertEqual(encode_varint(300), b"\xac\x02")

    def test_negative_rejected(self) -> None:
        with self.assertRaises(ValueError):
            encode_varint(-1)

    def test_truncated(self) -> None:
        with self.assertRaises(IncompleteError):
            decode_varint(b"\x80\x80")

    def test_too_long(self) -> None:
        with self.assertRaises(ProtocolError):
            decode_varint(b"\xff" * 11)


class ZigzagTest(unittest.TestCase):
    def test_values(self) -> None:
        for plain, encoded in ((0, 0), (-1, 1), (1, 2), (-2, 3), (2**31, 2**32)):
            self.assertEqual(zigzag_encode(plain), encoded)
            self.assertEqual(zigzag_decode(encoded), plain)

    def test_boot_notification_time(self) -> None:
        # The 0.1.x handshake hard-coded 3526800158 as BootNotification.conf
        # currentTime: zigzag for 2025-11-17 17:21:19 UTC.
        self.assertEqual(zigzag_decode(3526800158), 1763400079)


class MessageTest(unittest.TestCase):
    def test_decode_preserves_order_and_repeats(self) -> None:
        data = varint_field(1, 7) + float_field(3, 1.5) + float_field(3, 2.5) + string_field(2, "x")
        message = Message.decode(data)
        self.assertEqual([f.number for f in message], [1, 3, 3, 2])
        self.assertEqual([f.as_float() for f in message.get_all(3)], [1.5, 2.5])
        self.assertEqual(message.require(2).as_str(), "x")
        self.assertIn(1, message)
        self.assertNotIn(9, message)
        self.assertIsNone(message.get(9))
        self.assertEqual(len(message), 4)
        self.assertIn("1=7", repr(message))

    def test_nested(self) -> None:
        inner = string_field(1, "VendorMaxWorkCurrent") + string_field(2, "16")
        message = Message.decode(bytes_field(10, inner))
        self.assertEqual(message.require(10).as_message().require(2).as_str(), "16")

    def test_sint_field(self) -> None:
        self.assertEqual(Message.decode(sint_field(1, -5)).require(1).as_sint(), -5)

    def test_double(self) -> None:
        field = Field(1, WireType.I64, struct.pack("<d", 2.25))
        self.assertEqual(field.as_float(), 2.25)
        self.assertEqual(field.scalar(), 2.25)

    def test_scalar(self) -> None:
        self.assertEqual(Field(1, WireType.LEN, b"abc").scalar(), "abc")
        self.assertEqual(Field(1, WireType.LEN, b"\x00\x01").scalar(), b"\x00\x01")
        self.assertEqual(Field(1, WireType.LEN, b"\xff").scalar(), b"\xff")

    def test_type_errors(self) -> None:
        varint = Field(1, WireType.VARINT, 5)
        text = Field(2, WireType.LEN, b"\xff")
        with self.assertRaises(ProtocolError):
            varint.as_bytes()
        with self.assertRaises(ProtocolError):
            varint.as_float()
        with self.assertRaises(ProtocolError):
            text.as_int()
        with self.assertRaises(ProtocolError):
            text.as_str()

    def test_missing_field(self) -> None:
        with self.assertRaises(ProtocolError):
            Message.decode(b"").require(1)

    def test_invalid_data(self) -> None:
        for data in (b"\x00\x00", b"\x0b", b"\x0a\x05ab", b"\x0d\x00"):
            with self.subTest(data=data), self.assertRaises(ProtocolError):
                Message.decode(data)

    def test_field_number_must_be_positive(self) -> None:
        with self.assertRaises(ValueError):
            varint_field(0, 1)


if __name__ == "__main__":
    unittest.main()
