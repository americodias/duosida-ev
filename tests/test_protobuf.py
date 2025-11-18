"""
Tests for protobuf encoder/decoder
"""

import unittest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from duosida_ev.charger import ProtobufEncoder, ProtobufDecoder


class TestProtobufEncoder(unittest.TestCase):
    """Test protobuf encoding functions"""

    def test_encode_varint_small(self):
        """Test encoding small varint values"""
        self.assertEqual(ProtobufEncoder.encode_varint(0), b'\x00')
        self.assertEqual(ProtobufEncoder.encode_varint(1), b'\x01')
        self.assertEqual(ProtobufEncoder.encode_varint(127), b'\x7f')

    def test_encode_varint_large(self):
        """Test encoding larger varint values"""
        self.assertEqual(ProtobufEncoder.encode_varint(128), b'\x80\x01')
        self.assertEqual(ProtobufEncoder.encode_varint(300), b'\xac\x02')

    def test_encode_string(self):
        """Test encoding string fields"""
        result = ProtobufEncoder.encode_string(1, "test")
        # Field 1, wire type 2 = 0x0a, length 4
        self.assertEqual(result[:2], b'\x0a\x04')
        self.assertEqual(result[2:], b'test')

    def test_encode_varint_field(self):
        """Test encoding varint fields"""
        result = ProtobufEncoder.encode_varint_field(1, 150)
        # Field 1, wire type 0 = 0x08
        self.assertEqual(result[0:1], b'\x08')

    def test_encode_embedded_message(self):
        """Test encoding embedded messages"""
        inner = b'test'
        result = ProtobufEncoder.encode_embedded_message(1, inner)
        # Field 1, wire type 2 = 0x0a, length 4
        self.assertEqual(result[:2], b'\x0a\x04')
        self.assertEqual(result[2:], inner)


class TestProtobufDecoder(unittest.TestCase):
    """Test protobuf decoding functions"""

    def test_decode_varint_small(self):
        """Test decoding small varint values"""
        value, offset = ProtobufDecoder.decode_varint(b'\x00', 0)
        self.assertEqual(value, 0)
        self.assertEqual(offset, 1)

        value, offset = ProtobufDecoder.decode_varint(b'\x7f', 0)
        self.assertEqual(value, 127)
        self.assertEqual(offset, 1)

    def test_decode_varint_large(self):
        """Test decoding larger varint values"""
        value, offset = ProtobufDecoder.decode_varint(b'\x80\x01', 0)
        self.assertEqual(value, 128)
        self.assertEqual(offset, 2)

        value, offset = ProtobufDecoder.decode_varint(b'\xac\x02', 0)
        self.assertEqual(value, 300)
        self.assertEqual(offset, 2)

    def test_decode_message_varint(self):
        """Test decoding message with varint field"""
        # Field 1, wire type 0, value 150
        data = b'\x08\x96\x01'
        fields = ProtobufDecoder.decode_message(data)
        self.assertEqual(fields[1], 150)

    def test_decode_message_string(self):
        """Test decoding message with string field"""
        # Field 1, wire type 2, length 4, "test"
        data = b'\x0a\x04test'
        fields = ProtobufDecoder.decode_message(data)
        self.assertEqual(fields[1], 'test')

    def test_decode_message_multiple_fields(self):
        """Test decoding message with multiple fields"""
        # Field 1 = 1, Field 2 = "hi"
        data = b'\x08\x01\x12\x02hi'
        fields = ProtobufDecoder.decode_message(data)
        self.assertEqual(fields[1], 1)
        self.assertEqual(fields[2], 'hi')


if __name__ == '__main__':
    unittest.main()
