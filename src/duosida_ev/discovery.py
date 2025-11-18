"""
Network discovery for Duosida EV chargers
"""

import socket
import time
import binascii
import re
import logging
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)


def _get_device_id_via_tcp(ip: str, port: int = 9988, timeout: float = 3.0) -> Optional[str]:
    """
    Connect to charger via TCP and retrieve the device ID

    The device ID is not in the UDP discovery response, but is sent
    via TCP after the initial handshake.
    """
    try:
        logger.debug(f"Retrieving device ID from {ip}:{port}")
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect((ip, port))

        # Send handshake message 1
        msg1 = binascii.unhexlify("a2030408001000a20603494f53a80600")
        sock.sendall(msg1)
        time.sleep(0.1)

        # Receive response (contains device info including device ID)
        response = sock.recv(4096)
        sock.close()

        # Extract device ID from protobuf response
        # Device ID is a 19-digit string, appears after \xa2\x06\x13 in protobuf
        # Pattern: field 100 (0xa2 0x06) + length 19 (0x13) + device_id
        if response:
            # Look for device ID pattern in response
            # Device IDs are typically 19 digits starting with 03
            match = re.search(rb'\xa2\x06\x13(\d{19})', response)
            if match:
                return match.group(1).decode('utf-8')

            # Alternative: look for any 19-digit number
            match = re.search(rb'(03\d{17})', response)
            if match:
                return match.group(1).decode('utf-8')

        return None

    except Exception:
        return None


def discover_chargers(timeout: int = 5, interface: str = '0.0.0.0',
                      get_device_id: bool = True) -> List[Dict]:
    """
    Discover Duosida chargers on the local network via UDP broadcast

    Args:
        timeout: How long to wait for responses (seconds)
        interface: Network interface to bind to
        get_device_id: If True, connect via TCP to retrieve device ID

    Returns:
        List of discovered devices, each with keys:
        - ip: Device IP address
        - mac: Device MAC address
        - type: Device type (e.g., 'smart_wifi')
        - firmware: Firmware version
        - device_id: Device ID (if get_device_id=True)
        - raw: Raw response string
    """
    SRC_PORT = 48890
    DST_PORT = 48899
    DISCOVERY_MESSAGE = b'smart_chargepile_search\x00'

    devices = []

    # Create UDP socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

    # Allow reuse on macOS/BSD
    if hasattr(socket, 'SO_REUSEPORT'):
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)

    try:
        # Bind to source port
        sock.bind((interface, SRC_PORT))
        sock.settimeout(1.0)

        # Get own IP for filtering
        try:
            test_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            test_sock.connect(('8.8.8.8', 80))
            own_ip = test_sock.getsockname()[0]
            test_sock.close()
        except:
            own_ip = None

        # Send discovery broadcast
        sock.sendto(DISCOVERY_MESSAGE, ('255.255.255.255', DST_PORT))

        # Listen for responses
        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                data, addr = sock.recvfrom(4096)

                # Filter own broadcasts
                if own_ip and addr[0] == own_ip:
                    continue

                # Parse response: "IP,MAC,type,firmware"
                try:
                    decoded = data.decode('utf-8').strip('\x00')
                    parts = decoded.split(',')
                    if len(parts) >= 4:
                        device = {
                            'ip': parts[0],
                            'mac': parts[1],
                            'type': parts[2],
                            'firmware': parts[3],
                            'device_id': None,
                            'raw': decoded
                        }
                        # Avoid duplicates
                        if not any(d['ip'] == device['ip'] for d in devices):
                            devices.append(device)
                except:
                    pass

            except socket.timeout:
                continue
            except Exception:
                break

    finally:
        sock.close()

    # Get device IDs via TCP connection
    if get_device_id:
        for device in devices:
            device_id = _get_device_id_via_tcp(device['ip'])
            if device_id:
                device['device_id'] = device_id

    return devices
