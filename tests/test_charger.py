"""
Tests for ChargerStatus and DuosidaCharger
"""

import unittest
import sys
import os
from unittest.mock import Mock, patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from duosida_ev.charger import ChargerStatus, DuosidaCharger


class TestChargerStatus(unittest.TestCase):
    """Test ChargerStatus dataclass"""

    def test_default_values(self):
        """Test default status values"""
        status = ChargerStatus()
        self.assertEqual(status.voltage, 0.0)
        self.assertEqual(status.current, 0.0)
        self.assertEqual(status.power, 0.0)
        self.assertEqual(status.conn_status, 0)
        self.assertEqual(status.device_id, "")

    def test_state_property_available(self):
        """Test state property for Available"""
        status = ChargerStatus(conn_status=0)
        self.assertEqual(status.state, "Available")

    def test_state_property_charging(self):
        """Test state property for Charging"""
        status = ChargerStatus(conn_status=2)
        self.assertEqual(status.state, "Charging")

    def test_state_property_finished(self):
        """Test state property for Finished"""
        status = ChargerStatus(conn_status=5)
        self.assertEqual(status.state, "Finished")

    def test_state_property_unknown(self):
        """Test state property for unknown status"""
        status = ChargerStatus(conn_status=99)
        self.assertEqual(status.state, "Unknown (99)")

    def test_str_output(self):
        """Test string representation"""
        status = ChargerStatus(
            voltage=230.0,
            current=16.0,
            power=3680.0,
            temperature_station=25.0,
            conn_status=2,
            device_id="TEST123"
        )
        output = str(status)
        self.assertIn("TEST123", output)
        self.assertIn("Charging", output)
        self.assertIn("230.0V", output)
        self.assertIn("16.00A", output)
        self.assertIn("3680.0W", output)
        self.assertIn("25.0°C", output)

    def test_energy_in_output(self):
        """Test energy fields in string output"""
        status = ChargerStatus(
            voltage=230.0,
            current=16.0,
            today_consumption=10.5,
            session_energy=5.2
        )
        output = str(status)
        self.assertIn("10.50 kWh", output)
        self.assertIn("5.20 kWh", output)


class TestDuosidaCharger(unittest.TestCase):
    """Test DuosidaCharger class"""

    def test_init(self):
        """Test charger initialization"""
        charger = DuosidaCharger(
            host="192.168.1.100",
            device_id="TEST123",
            port=9988,
            timeout=10.0
        )
        self.assertEqual(charger.host, "192.168.1.100")
        self.assertEqual(charger.device_id, "TEST123")
        self.assertEqual(charger.port, 9988)
        self.assertEqual(charger.timeout, 10.0)
        self.assertIsNone(charger.sock)

    def test_set_max_current_valid(self):
        """Test setting valid max current"""
        charger = DuosidaCharger(host="192.168.1.100", device_id="TEST123")
        charger.sock = Mock()
        charger.sock.sendall = Mock()

        result = charger.set_max_current(16)
        self.assertTrue(result)
        self.assertEqual(charger._cached_max_current, 16)

    def test_set_max_current_invalid_low(self):
        """Test setting invalid low max current"""
        charger = DuosidaCharger(host="192.168.1.100", device_id="TEST123")
        result = charger.set_max_current(5)
        self.assertFalse(result)

    def test_set_max_current_invalid_high(self):
        """Test setting invalid high max current"""
        charger = DuosidaCharger(host="192.168.1.100", device_id="TEST123")
        result = charger.set_max_current(33)
        self.assertFalse(result)

    def test_get_max_current_cached(self):
        """Test getting cached max current"""
        charger = DuosidaCharger(host="192.168.1.100", device_id="TEST123")
        charger._cached_max_current = 16
        self.assertEqual(charger.get_max_current(), 16)

    def test_get_max_current_none(self):
        """Test getting max current when not set"""
        charger = DuosidaCharger(host="192.168.1.100", device_id="TEST123")
        self.assertIsNone(charger.get_max_current())

    @patch('socket.socket')
    def test_connect_success(self, mock_socket_class):
        """Test successful connection"""
        mock_socket = MagicMock()
        mock_socket_class.return_value = mock_socket
        mock_socket.recv.return_value = b''

        charger = DuosidaCharger(
            host="192.168.1.100",
            device_id="TEST123",
            debug=False
        )
        result = charger.connect()

        self.assertTrue(result)
        mock_socket.connect.assert_called_once_with(("192.168.1.100", 9988))

    @patch('socket.socket')
    def test_connect_failure(self, mock_socket_class):
        """Test connection failure"""
        mock_socket = MagicMock()
        mock_socket_class.return_value = mock_socket
        mock_socket.connect.side_effect = ConnectionRefusedError("Connection refused")

        charger = DuosidaCharger(
            host="192.168.1.100",
            device_id="TEST123",
            debug=False
        )
        result = charger.connect()

        self.assertFalse(result)

    def test_disconnect(self):
        """Test disconnection"""
        charger = DuosidaCharger(host="192.168.1.100", device_id="TEST123")
        mock_sock = Mock()
        charger.sock = mock_sock

        charger.disconnect()

        mock_sock.close.assert_called_once()
        self.assertIsNone(charger.sock)


if __name__ == '__main__':
    unittest.main()
