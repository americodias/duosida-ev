"""
Tests for network discovery
"""

import unittest
import sys
import os
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from duosida_ev.discovery import discover_chargers


class TestDiscovery(unittest.TestCase):
    """Test discovery functions"""

    @patch('socket.socket')
    def test_discover_single_device(self, mock_socket_class):
        """Test discovering a single device"""
        mock_socket = MagicMock()
        mock_socket_class.return_value = mock_socket

        # Mock the own IP detection
        mock_socket.getsockname.return_value = ('192.168.1.100', 0)

        # Simulate receiving a response then timeout
        response = b'192.168.1.200,AA:BB:CC:DD:EE:FF,smart_wifi,V1.0\x00'
        mock_socket.recvfrom.side_effect = [
            (response, ('192.168.1.200', 48899)),
            Exception("timeout")
        ]

        # Disable TCP device ID lookup for unit test
        devices = discover_chargers(timeout=1, get_device_id=False)

        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0]['ip'], '192.168.1.200')
        self.assertEqual(devices[0]['mac'], 'AA:BB:CC:DD:EE:FF')
        self.assertEqual(devices[0]['type'], 'smart_wifi')
        self.assertEqual(devices[0]['firmware'], 'V1.0')
        self.assertIsNone(devices[0]['device_id'])

    @patch('socket.socket')
    def test_discover_no_devices(self, mock_socket_class):
        """Test discovery with no devices"""
        mock_socket = MagicMock()
        mock_socket_class.return_value = mock_socket

        # Mock the own IP detection
        mock_socket.getsockname.return_value = ('192.168.1.100', 0)

        # Only timeouts
        import socket
        mock_socket.recvfrom.side_effect = socket.timeout()

        devices = discover_chargers(timeout=1, get_device_id=False)

        self.assertEqual(len(devices), 0)

    @patch('socket.socket')
    def test_discover_filters_own_ip(self, mock_socket_class):
        """Test that discovery filters out own broadcasts"""
        mock_socket = MagicMock()
        mock_socket_class.return_value = mock_socket

        # Mock the own IP detection
        mock_socket.getsockname.return_value = ('192.168.1.100', 0)

        # Simulate receiving own broadcast then timeout
        response = b'192.168.1.100,AA:BB:CC:DD:EE:FF,smart_wifi,V1.0\x00'
        import socket
        mock_socket.recvfrom.side_effect = [
            (response, ('192.168.1.100', 48899)),
            socket.timeout()
        ]

        devices = discover_chargers(timeout=1, get_device_id=False)

        # Should filter out own IP
        self.assertEqual(len(devices), 0)

    @patch('socket.socket')
    def test_discover_multiple_devices(self, mock_socket_class):
        """Test discovering multiple devices"""
        mock_socket = MagicMock()
        mock_socket_class.return_value = mock_socket

        # Mock the own IP detection
        mock_socket.getsockname.return_value = ('192.168.1.100', 0)

        # Simulate multiple responses
        response1 = b'192.168.1.200,AA:BB:CC:DD:EE:01,smart_wifi,V1.0\x00'
        response2 = b'192.168.1.201,AA:BB:CC:DD:EE:02,smart_wifi,V1.1\x00'
        mock_socket.recvfrom.side_effect = [
            (response1, ('192.168.1.200', 48899)),
            (response2, ('192.168.1.201', 48899)),
            Exception("timeout")
        ]

        devices = discover_chargers(timeout=1, get_device_id=False)

        self.assertEqual(len(devices), 2)
        self.assertEqual(devices[0]['ip'], '192.168.1.200')
        self.assertEqual(devices[1]['ip'], '192.168.1.201')

    @patch('socket.socket')
    def test_discover_deduplicates(self, mock_socket_class):
        """Test that discovery deduplicates responses"""
        mock_socket = MagicMock()
        mock_socket_class.return_value = mock_socket

        # Mock the own IP detection
        mock_socket.getsockname.return_value = ('192.168.1.100', 0)

        # Simulate same device responding twice
        response = b'192.168.1.200,AA:BB:CC:DD:EE:FF,smart_wifi,V1.0\x00'
        mock_socket.recvfrom.side_effect = [
            (response, ('192.168.1.200', 48899)),
            (response, ('192.168.1.200', 48899)),
            Exception("timeout")
        ]

        devices = discover_chargers(timeout=1, get_device_id=False)

        # Should only have one device
        self.assertEqual(len(devices), 1)

    @patch('duosida_ev.discovery._get_device_id_via_tcp')
    @patch('socket.socket')
    def test_discover_with_device_id(self, mock_socket_class, mock_get_device_id):
        """Test discovery with device ID retrieval"""
        mock_socket = MagicMock()
        mock_socket_class.return_value = mock_socket

        # Mock the own IP detection
        mock_socket.getsockname.return_value = ('192.168.1.100', 0)

        # Simulate receiving a response
        response = b'192.168.1.200,AA:BB:CC:DD:EE:FF,smart_wifi,V1.0\x00'
        mock_socket.recvfrom.side_effect = [
            (response, ('192.168.1.200', 48899)),
            Exception("timeout")
        ]

        # Mock TCP device ID retrieval
        mock_get_device_id.return_value = '0310107112122360374'

        devices = discover_chargers(timeout=1, get_device_id=True)

        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0]['device_id'], '0310107112122360374')
        mock_get_device_id.assert_called_once_with('192.168.1.200')


if __name__ == '__main__':
    unittest.main()
