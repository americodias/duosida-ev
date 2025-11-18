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
    voltage: float = 0.0
    voltage2: float = 0.0
    voltage3: float = 0.0
    current: float = 0.0
    current2: float = 0.0
    current3: float = 0.0
    temperature_internal: float = 0.0
    temperature_station: float = 0.0
    power: float = 0.0
    max_current: float = 0.0
    acc_energy: float = 0.0
    today_consumption: float = 0.0
    session_energy: float = 0.0
    timestamp: int = 0
    conn_status: int = 0
    device_id: str = ""
    model: str = ""
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
        """Get Control Pilot voltage from conn_status (IEC 61851-1)"""
        # CP signal states based on voltage levels
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

    @property
    def cp_state(self) -> str:
        """Get Control Pilot state description (IEC 61851-1)"""
        cp_states = {
            0: "No vehicle connected",
            1: "Vehicle connected, not ready",
            2: "Charging",
            3: "Charging (cooling)",
            4: "Vehicle connected, suspended",
            5: "Vehicle connected, finished",
            6: "Holiday mode",
        }
        return cp_states.get(int(self.conn_status), "Unknown")

    def to_dict(self) -> Dict[str, Any]:
        """Convert status to dictionary for JSON export"""
        return {
            "device_id": self.device_id,
            "model": self.model,
            "firmware": self.firmware,
            "state": self.state,
            "conn_status": self.conn_status,
            "cp_voltage": self.cp_voltage,
            "voltage": self.voltage,
            "voltage2": self.voltage2,
            "voltage3": self.voltage3,
            "current": self.current,
            "current2": self.current2,
            "current3": self.current3,
            "power": self.power,
            "temperature_station": self.temperature_station,
            "temperature_internal": self.temperature_internal,
            "today_consumption": self.today_consumption,
            "session_energy": self.session_energy,
            "timestamp": self.timestamp
        }

    def __str__(self) -> str:
        status_str = self.state
        voltage = float(self.voltage) if self.voltage else 0.0
        voltage2 = float(self.voltage2) if self.voltage2 else 0.0
        voltage3 = float(self.voltage3) if self.voltage3 else 0.0
        current = float(self.current) if self.current else 0.0
        current2 = float(self.current2) if self.current2 else 0.0
        current3 = float(self.current3) if self.current3 else 0.0
        power = float(self.power) if self.power else 0.0
        temp_station = float(self.temperature_station) if self.temperature_station else 0.0

        result = f"""Charger Status:
  Device ID: {self.device_id}
  Status: {status_str}
  CP Voltage: {self.cp_voltage:.0f}V"""

        if self.model or self.firmware:
            if self.model:
                result += f"\n  Model: {self.model}"
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
    Power: {power:.1f}W"""

        if self.max_current and float(self.max_current) > 0:
            result += f"""
    Max Current Limit: {float(self.max_current):.0f}A  [cached]"""

        result += f"""

  TEMPERATURE:
    Station: {temp_station:.1f}°C"""

        if self.today_consumption > 0.01 or self.session_energy > 0.01:
            result += f"""

  ENERGY:"""
            if self.today_consumption > 0.01:
                result += f"""
    Today's Consumption: {self.today_consumption:.2f} kWh"""
            if self.session_energy > 0.01:
                result += f"""
    Session Energy: {self.session_energy:.2f} kWh"""
            if self.timestamp > 0:
                from datetime import datetime
                dt = datetime.fromtimestamp(self.timestamp)
                result += f"""
    Session Start: {dt.strftime('%Y-%m-%d %H:%M:%S')}"""

        if self.acc_energy and float(self.acc_energy) > 0:
            result += f"""
  Accumulated Energy: {float(self.acc_energy):.2f}kWh"""

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
        self._cached_max_current: Optional[int] = None
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
            firmware = ""
            if 4 in outer_fields and isinstance(outer_fields[4], bytes):
                device_info = ProtobufDecoder.decode_message(outer_fields[4])
                model_val = device_info.get(2, "")
                model = model_val if isinstance(model_val, str) else ""
                firmware_val = device_info.get(5, "")
                firmware = firmware_val if isinstance(firmware_val, str) else ""

            device_id = outer_fields.get(100, "")

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
                voltage=get_float(1),
                voltage2=get_float(3),
                voltage3=0.0,
                current=get_float(2),
                current2=get_float(15),
                current3=0.0,
                temperature_internal=get_float(7),
                temperature_station=get_float(8),
                power=0.0,
                max_current=float(self._cached_max_current) if self._cached_max_current else 0.0,
                acc_energy=0.0,
                today_consumption=get_float(20) / 1000.0,
                session_energy=get_float(4),
                timestamp=get_int(18),
                conn_status=get_int(17),
                device_id=device_id if isinstance(device_id, str) else "",
                model=model if isinstance(model, str) else "",
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
            self._cached_max_current = amps
            time.sleep(0.5)
            return True

        except Exception:
            return False

    def get_max_current(self) -> Optional[int]:
        """Get the last set max current value (cached)"""
        return self._cached_max_current

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
