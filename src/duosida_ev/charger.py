"""
Duosida EV Charger - Direct TCP communication
"""

import socket
import struct
import time
import binascii
import logging
from dataclasses import dataclass
from typing import Optional, Dict, Any

from .exceptions import (
    ConnectionError as ChargerConnectionError,
    CommunicationError,
    CommandError,
    ValidationError,
    TimeoutError as ChargerTimeoutError,
)

logger = logging.getLogger(__name__)


class ProtobufEncoder:
    """Simple protobuf encoder for the messages we need"""

    @staticmethod
    def encode_varint(value: int) -> bytes:
        """Encode integer as protobuf varint"""
        result = bytearray()
        while value > 0x7F:
            result.append((value & 0x7F) | 0x80)
            value >>= 7
        result.append(value & 0x7F)
        return bytes(result)

    @staticmethod
    def encode_string(field_num: int, value: str) -> bytes:
        """Encode string field"""
        data = value.encode('utf-8')
        field_header = ProtobufEncoder.encode_varint((field_num << 3) | 2)
        length = ProtobufEncoder.encode_varint(len(data))
        return field_header + length + data

    @staticmethod
    def encode_float(field_num: int, value: float) -> bytes:
        """Encode float field (32-bit)"""
        field_header = ProtobufEncoder.encode_varint((field_num << 3) | 5)
        return field_header + struct.pack('<f', value)

    @staticmethod
    def encode_varint_field(field_num: int, value: int) -> bytes:
        """Encode varint field"""
        field_header = ProtobufEncoder.encode_varint((field_num << 3) | 0)
        return field_header + ProtobufEncoder.encode_varint(value)

    @staticmethod
    def encode_embedded_message(field_num: int, data: bytes) -> bytes:
        """Encode embedded message"""
        field_header = ProtobufEncoder.encode_varint((field_num << 3) | 2)
        length = ProtobufEncoder.encode_varint(len(data))
        return field_header + length + data


class ProtobufDecoder:
    """Simple protobuf decoder"""

    @staticmethod
    def decode_varint(data: bytes, offset: int) -> tuple:
        """Decode protobuf varint, returns (value, next_offset)"""
        result = 0
        shift = 0
        while offset < len(data):
            byte = data[offset]
            result |= (byte & 0x7F) << shift
            offset += 1
            if not (byte & 0x80):
                break
            shift += 7
        return result, offset

    @staticmethod
    def decode_message(data: bytes) -> Dict[int, Any]:
        """Decode protobuf message into field dictionary"""
        fields = {}
        offset = 0

        while offset < len(data):
            if offset >= len(data):
                break

            key, offset = ProtobufDecoder.decode_varint(data, offset)
            field_number = key >> 3
            wire_type = key & 0x07

            if wire_type == 0:  # Varint
                value, offset = ProtobufDecoder.decode_varint(data, offset)
                fields[field_number] = value

            elif wire_type == 1:  # 64-bit
                if offset + 8 <= len(data):
                    value = struct.unpack('<d', data[offset:offset+8])[0]
                    fields[field_number] = value
                    offset += 8

            elif wire_type == 2:  # Length-delimited
                length, offset = ProtobufDecoder.decode_varint(data, offset)
                if offset + length <= len(data):
                    value = data[offset:offset+length]
                    try:
                        decoded = value.decode('utf-8')
                        fields[field_number] = decoded
                    except:
                        fields[field_number] = value
                    offset += length

            elif wire_type == 5:  # 32-bit
                if offset + 4 <= len(data):
                    value = struct.unpack('<f', data[offset:offset+4])[0]
                    fields[field_number] = value
                    offset += 4

        return fields


@dataclass
class ChargerStatus:
    """Charger status data"""
    # Connection status (first for quick access)
    conn_status: int = 0

    # Electrical measurements
    voltage: float = 0.0
    voltage2: float = 0.0  # L2 phase
    voltage3: float = 0.0  # L3 phase
    current: float = 0.0
    current2: float = 0.0  # L2 phase
    current3: float = 0.0  # L3 phase
    power: float = 0.0

    # Temperature
    temperature_station: float = 0.0
    temperature_internal: float = 0.0

    # Session data
    session_energy: float = 0.0
    timestamp: int = 0  # Session start timestamp

    # Control Pilot
    cp_voltage_raw: float = 0.0  # Field 9 - actual CP voltage reading

    # Device info (last)
    device_id: str = ""
    model: str = ""
    manufacturer: str = ""
    firmware: str = ""

    @property
    def state(self) -> str:
        """Get human-readable state from conn_status"""
        status_names = {
            -1: "Undefined",
            0: "Available",
            1: "Preparing",
            2: "Charging",
            3: "Cooling",
            4: "SuspendedEV",
            5: "Finished",
            6: "Holiday"
        }
        return status_names.get(int(self.conn_status), f"Unknown ({self.conn_status})")

    @property
    def cp_voltage(self) -> float:
        """Get Control Pilot voltage (IEC 61851-1)

        Returns the actual CP voltage reading from Field 9 if available,
        otherwise derives it from conn_status.
        """
        # Use actual reading if available
        if self.cp_voltage_raw > 0:
            return self.cp_voltage_raw

        # Fallback: derive from conn_status
        cp_voltages = {
            0: 12.0,  # State A: No vehicle connected
            1: 9.0,   # State B: Vehicle connected, not ready
            2: 6.0,   # State C: Charging
            3: 6.0,   # Cooling (still in charging state)
            4: 9.0,   # SuspendedEV (vehicle connected but paused)
            5: 9.0,   # Finished (vehicle still connected)
            6: 12.0,  # Holiday mode
        }
        return cp_voltages.get(int(self.conn_status), 0.0)


    def to_dict(self) -> Dict[str, Any]:
        """Convert status to dictionary for JSON export"""
        # Calculate session time in minutes from timestamp
        session_time = 0
        if self.timestamp > 0:
            import time
            session_time = int((time.time() - self.timestamp) / 60)

        return {
            # Connection status first
            "conn_status": self.conn_status,
            "cp_voltage": self.cp_voltage,
            "state": self.state,

            # Electrical measurements
            "voltage": self.voltage,
            "voltage_l2": self.voltage2,
            "voltage_l3": self.voltage3,
            "current": self.current,
            "current_l2": self.current2,
            "current_l3": self.current3,
            "power": self.power,

            # Temperature
            "temperature_station": self.temperature_station,

            # Session data
            "session_energy": self.session_energy,
            "session_time": session_time,

            # Device info last
            "device_id": self.device_id,
            "model": self.model,
            "manufacturer": self.manufacturer,
            "firmware": self.firmware,
        }

    def __str__(self) -> str:
        status_str = self.state
        voltage = float(self.voltage) if self.voltage else 0.0
        voltage3 = float(self.voltage3) if self.voltage3 else 0.0
        current = float(self.current) if self.current else 0.0
        current3 = float(self.current3) if self.current3 else 0.0
        power = float(self.power) if self.power else 0.0
        temp_station = float(self.temperature_station) if self.temperature_station else 0.0

        result = f"""Charger Status:
  Device ID: {self.device_id}
  Status: {status_str}
  CP Voltage: {self.cp_voltage:.0f}V"""

        if self.model or self.manufacturer or self.firmware:
            if self.model:
                result += f"\n  Model: {self.model}"
            if self.manufacturer:
                result += f"\n  Manufacturer: {self.manufacturer}"
            if self.firmware:
                result += f"\n  Firmware: {self.firmware}"

        result += f"""

  ELECTRICAL:
    Voltage (L1): {voltage:.1f}V"""

        if voltage3 > 0.01:
            result += f"""
    Voltage (L3): {voltage3:.1f}V"""

        result += f"""
    Current (L1): {current:.2f}A"""

        if current3 > 0.01:
            result += f"""
    Current (L3): {current3:.2f}A"""

        result += f"""
    Power: {power:.1f}W

  TEMPERATURE:
    Station: {temp_station:.1f}°C"""

        if self.session_energy > 0.01 or self.timestamp > 0:
            result += f"""

  SESSION:"""
            if self.session_energy > 0.01:
                result += f"""
    Energy: {self.session_energy:.2f} kWh"""
            if self.timestamp > 0:
                from datetime import datetime
                import time
                dt = datetime.fromtimestamp(self.timestamp)
                session_minutes = int((time.time() - self.timestamp) / 60)
                result += f"""
    Start: {dt.strftime('%Y-%m-%d %H:%M:%S')}
    Duration: {session_minutes} min"""

        return result


class DuosidaCharger:
    """Direct communication with Duosida EV Charger"""

    def __init__(self, host: str, port: int = 9988, device_id: str = "",
                 timeout: float = 5.0, debug: bool = False):
        self.host = host
        self.port = port
        self.device_id = device_id
        self.timeout = timeout
        self.sock: Optional[socket.socket] = None
        self.sequence = 2
        self._last_good_status: Optional[ChargerStatus] = None
        self.debug = debug

    def connect(self) -> bool:
        """Connect to charger"""
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.settimeout(self.timeout)
            self.sock.connect((self.host, self.port))
            logger.info(f"Connected to {self.host}:{self.port}")
            self._send_handshake()
            return True
        except socket.timeout as e:
            logger.error(f"Connection timed out: {e}")
            return False
        except socket.error as e:
            logger.error(f"Connection failed: {e}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error connecting: {e}")
            return False

    def disconnect(self):
        """Disconnect from charger"""
        if self.sock:
            self.sock.close()
            self.sock = None
            logger.info("Disconnected")

    def _send_handshake(self):
        """Send initial handshake messages"""
        msg1 = binascii.unhexlify("a2030408001000a20603494f53a80600")
        self._send_raw(msg1)
        time.sleep(0.1)

        try:
            self._recv_raw(timeout=1.0)
        except:
            pass

        msg2 = binascii.unhexlify("1a0a089ee6da910d10001800") + \
               binascii.unhexlify("a2061330333130313037313132313232333630333734") + \
               binascii.unhexlify("a8069e818040")
        self._send_raw(msg2)
        time.sleep(0.2)
        self.sequence += 1

    def _send_raw(self, data: bytes):
        """Send raw protobuf data"""
        if not self.sock:
            raise ConnectionError("Not connected")
        self.sock.sendall(data)

    def _recv_raw(self, timeout: Optional[float] = None) -> bytes:
        """Receive raw data from charger"""
        if not self.sock:
            raise ConnectionError("Not connected")

        old_timeout = self.sock.gettimeout()
        if timeout is not None:
            self.sock.settimeout(timeout)

        try:
            return self.sock.recv(4096)
        finally:
            self.sock.settimeout(old_timeout)

    def get_status(self, retries: int = 3, use_cache: bool = True) -> Optional[ChargerStatus]:
        """Get charger status"""
        for attempt in range(retries):
            try:
                status = self._get_status_once()
                if status:
                    self._last_good_status = status
                    return status
            except Exception as e:
                if attempt == retries - 1:
                    if use_cache and self._last_good_status:
                        return self._last_good_status
                    raise

        if use_cache and self._last_good_status:
            return self._last_good_status
        return None

    def _get_status_once(self) -> Optional[ChargerStatus]:
        """Internal method to get status once"""
        try:
            response = self._recv_raw(timeout=2.0)
            if not response:
                return None

            outer_fields = ProtobufDecoder.decode_message(response)

            model = ""
            manufacturer = ""
            firmware = ""
            device_id = outer_fields.get(100, "")
            if isinstance(device_id, bytes):
                device_id = device_id.decode('utf-8', errors='ignore')

            # Parse device info from field 4
            # It's a nested protobuf that may be decoded as string with embedded control chars
            if 4 in outer_fields:
                device_info = outer_fields[4]
                if isinstance(device_info, bytes):
                    device_info = device_info.decode('utf-8', errors='ignore')
                if isinstance(device_info, str):
                    # Parse embedded protobuf fields from the string
                    # Format: \x12\x11MODEL\x1a\x13DEVICEID"\x05MANUFACTURER*-FIRMWARE\x00:\x00
                    import re

                    # Extract model - between first control char and device_id
                    # Model starts after \x12\xNN
                    if '\x12' in device_info:
                        model_start = device_info.find('\x12') + 2  # Skip field marker and length
                        if device_id and device_id in device_info:
                            model_end = device_info.find(device_id)
                            # Find the actual start after control char
                            model_section = device_info[model_start:model_end]
                            # Remove leading control char (field 3 marker)
                            model = model_section.rstrip('\x1a\x13').strip()

                    # Extract manufacturer and firmware after device_id
                    if device_id and device_id in device_info:
                        after_id = device_info.split(device_id, 1)[1]
                        # Remove leading control chars and find manufacturer
                        # Format: "\x05UCHEN*-FIRMWARE\x00:\x00
                        if '*-' in after_id:
                            # Find manufacturer between " and *
                            parts = after_id.split('*-', 1)
                            # Manufacturer is in parts[0], strip control chars
                            mfr = parts[0]
                            # Remove control characters
                            manufacturer = ''.join(c for c in mfr if c.isprintable() and c not in '"')
                            # Firmware is in parts[1], strip trailing nulls and control chars
                            fw = parts[1]
                            firmware = ''.join(c for c in fw if c.isprintable() and c != ':').strip()

            fields = {}
            if 16 in outer_fields and isinstance(outer_fields[16], bytes):
                inner_data = outer_fields[16]
                inner_fields = ProtobufDecoder.decode_message(inner_data)
                msg_type = inner_fields.get(2, "")

                if msg_type == "DataVendorStatusReq":
                    if 10 in inner_fields and isinstance(inner_fields[10], bytes):
                        status_data = inner_fields[10]
                        fields = ProtobufDecoder.decode_message(status_data)
                elif msg_type == "DataContinueReq":
                    return None
                else:
                    if 10 in inner_fields and isinstance(inner_fields[10], bytes):
                        status_data = inner_fields[10]
                        fields = ProtobufDecoder.decode_message(status_data)
                    elif 12 in inner_fields and isinstance(inner_fields[12], bytes):
                        status_data = inner_fields[12]
                        fields = ProtobufDecoder.decode_message(status_data)
                    else:
                        fields = inner_fields
            else:
                fields = outer_fields

            has_key_fields = fields and any(field_num in fields for field_num in [1, 2, 8, 17])
            if not has_key_fields:
                return None

            def get_float(field_num, default=0.0):
                val = fields.get(field_num, default)
                return float(val) if isinstance(val, (int, float)) else default

            def get_int(field_num, default=0):
                val = fields.get(field_num, default)
                return int(val) if isinstance(val, (int, float)) else default

            status = ChargerStatus(
                conn_status=get_int(17),
                voltage=get_float(1),
                voltage2=0.0,  # L2 phase - not mapped yet
                voltage3=0.0,  # L3 phase - not mapped yet
                current=get_float(2),
                current2=0.0,  # L2 phase - not mapped yet
                current3=0.0,  # L3 phase - not mapped yet
                power=0.0,
                temperature_station=get_float(8),
                temperature_internal=get_float(7),
                session_energy=get_float(4),
                timestamp=get_int(18),
                cp_voltage_raw=get_float(9),  # Actual CP voltage reading
                device_id=device_id if isinstance(device_id, str) else "",
                model=model if isinstance(model, str) else "",
                manufacturer=manufacturer if isinstance(manufacturer, str) else "",
                firmware=firmware if isinstance(firmware, str) else ""
            )

            if status.voltage > 0 and status.current > 0:
                status.power = status.voltage * status.current

            return status

        except Exception as e:
            logger.error(f"Error getting status: {e}")
            raise

    def set_max_current(self, amps: int) -> bool:
        """Set maximum charging current (6-32A)"""
        if not 6 <= amps <= 32:
            return False

        try:
            command_data = (
                ProtobufEncoder.encode_string(1, "VendorMaxWorkCurrent") +
                ProtobufEncoder.encode_string(2, str(amps))
            )

            msg = (
                ProtobufEncoder.encode_embedded_message(10, command_data) +
                ProtobufEncoder.encode_string(100, self.device_id) +
                ProtobufEncoder.encode_varint_field(101, self.sequence)
            )

            self._send_raw(msg)
            self.sequence += 1
            time.sleep(0.5)
            return True

        except Exception:
            return False

    def set_config(self, key: str, value: str) -> bool:
        """Set a configuration value on the charger

        Args:
            key: Configuration key name (e.g., 'VendorMaxWorkCurrent')
            value: Configuration value as string

        Returns:
            True if command was sent successfully
        """
        try:
            command_data = (
                ProtobufEncoder.encode_string(1, key) +
                ProtobufEncoder.encode_string(2, value)
            )

            msg = (
                ProtobufEncoder.encode_embedded_message(10, command_data) +
                ProtobufEncoder.encode_string(100, self.device_id) +
                ProtobufEncoder.encode_varint_field(101, self.sequence)
            )

            self._send_raw(msg)
            self.sequence += 1
            time.sleep(0.5)
            return True

        except Exception as e:
            logger.error(f"Error setting config: {e}")
            return False

    def set_connection_timeout(self, seconds: int) -> bool:
        """Set connection timeout (30-900 seconds)

        Args:
            seconds: Timeout value in seconds

        Returns:
            True if command was sent successfully
        """
        if not 30 <= seconds <= 900:
            logger.warning("Connection timeout must be between 30 and 900 seconds")
            return False

        return self.set_config("ConnectionTimeOut", str(seconds))

    def set_max_temperature(self, celsius: int) -> bool:
        """Set maximum working temperature (85-95°C)

        Args:
            celsius: Temperature in Celsius

        Returns:
            True if command was sent successfully
        """
        if not 85 <= celsius <= 95:
            logger.warning("Max temperature must be between 85 and 95°C")
            return False

        return self.set_config("VendorMaxWorkTemperature", str(celsius))

    def set_max_voltage(self, voltage: int) -> bool:
        """Set maximum working voltage (265-290V)

        Args:
            voltage: Voltage in volts

        Returns:
            True if command was sent successfully
        """
        if not 265 <= voltage <= 290:
            logger.warning("Max voltage must be between 265 and 290V")
            return False

        return self.set_config("VendorMaxWorkVoltage", str(voltage))

    def set_min_voltage(self, voltage: int) -> bool:
        """Set minimum working voltage (70-110V)

        Args:
            voltage: Voltage in volts

        Returns:
            True if command was sent successfully
        """
        if not 70 <= voltage <= 110:
            logger.warning("Min voltage must be between 70 and 110V")
            return False

        return self.set_config("VendorMinWorkVoltage", str(voltage))

    def set_direct_work_mode(self, enabled: bool) -> bool:
        """Set direct work mode (plug and charge)

        When enabled, charging starts automatically when vehicle is plugged in.
        When disabled, authentication is required before charging.

        Args:
            enabled: True to enable, False to disable

        Returns:
            True if command was sent successfully
        """
        return self.set_config("VendorDirectWorkMode", "1" if enabled else "0")

    def set_led_brightness(self, level: int) -> bool:
        """Set LED/screen brightness level

        Args:
            level: Brightness level (0=off, 1=low, 3=high)

        Returns:
            True if command was sent successfully
        """
        if level not in (0, 1, 3):
            logger.warning("LED brightness must be 0, 1, or 3")
            return False

        return self.set_config("VendorLEDStrength", str(level))

    def set_stop_on_disconnect(self, enabled: bool) -> bool:
        """Set whether to stop transaction when EV side disconnects

        When enabled, the charging transaction automatically stops when
        the vehicle disconnects from the cable.
        When disabled, the transaction remains open.

        Args:
            enabled: True to enable auto-stop, False to disable

        Returns:
            True if command was sent successfully
        """
        return self.set_config("StopTransactionOnEVSideDisconnect", "1" if enabled else "0")

    def start_charging(self) -> bool:
        """Start a charging session

        Returns:
            True if command was sent successfully
        """
        try:
            # Build inner message: field 1 = "XC_Remote_Tag"
            inner_data = ProtobufEncoder.encode_string(1, "XC_Remote_Tag")

            # Build command: field 1 = 1, field 2 = inner message
            command_data = (
                ProtobufEncoder.encode_varint_field(1, 1) +
                ProtobufEncoder.encode_embedded_message(2, inner_data)
            )

            # Field 34 contains the start command
            msg = (
                ProtobufEncoder.encode_embedded_message(34, command_data) +
                ProtobufEncoder.encode_string(100, self.device_id) +
                ProtobufEncoder.encode_varint_field(101, self.sequence)
            )

            self._send_raw(msg)
            self.sequence += 1
            time.sleep(0.5)
            return True

        except Exception as e:
            logger.error(f"Error starting charge: {e}")
            return False

    def stop_charging(self, session_id: Optional[int] = None) -> bool:
        """Stop the current charging session

        Args:
            session_id: Optional session identifier. If not provided,
                        uses current timestamp as session ID.

        Returns:
            True if command was sent successfully
        """
        try:
            # Use timestamp as session ID if not provided
            if session_id is None:
                session_id = int(time.time() * 1000) % 0xFFFFFFFF

            # Build command: field 1 = session_id
            command_data = ProtobufEncoder.encode_varint_field(1, session_id)

            # Field 36 contains the stop command
            msg = (
                ProtobufEncoder.encode_embedded_message(36, command_data) +
                ProtobufEncoder.encode_string(100, self.device_id) +
                ProtobufEncoder.encode_varint_field(101, self.sequence)
            )

            self._send_raw(msg)
            self.sequence += 1
            time.sleep(0.5)
            return True

        except Exception as e:
            logger.error(f"Error stopping charge: {e}")
            return False

    def monitor(self, interval: float = 2.0, duration: Optional[float] = None,
                callback=None):
        """Monitor charger status continuously

        Args:
            interval: Polling interval in seconds
            duration: Total monitoring duration (None for indefinite)
            callback: Optional function to call with each status update
        """
        start_time = time.time()

        try:
            while True:
                if duration and (time.time() - start_time) > duration:
                    break

                try:
                    status = self.get_status()
                    if status:
                        if callback:
                            callback(status)
                        elif self.debug:
                            print("\n" + "="*60)
                            print(status)
                            print("="*60)
                except Exception as e:
                    logger.warning(f"Error during monitoring: {e}")

                time.sleep(interval)

        except KeyboardInterrupt:
            pass
