"""
Duosida EV Charger - Direct TCP communication
"""

import socket
import struct
import time
import binascii
from dataclasses import dataclass
from typing import Optional, Dict, Any


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
  Status: {status_str}"""

        if self.model or self.firmware:
            if self.model:
                result += f"\n  Model: {self.model}"
            if self.firmware:
                result += f"\n  Firmware: {self.firmware}"

        result += f"""

  ELECTRICAL:
    Voltage (L1): {voltage:.1f}V"""

        if voltage2 > 0.01:
            result += f"""
    Voltage (Field 3): {voltage2:.1f}V  [unknown field]"""
        if voltage3 > 0.01:
            result += f"""
    Voltage (L3): {voltage3:.1f}V"""

        result += f"""
    Current (L1): {current:.2f}A"""

        if current2 > 0.01:
            result += f"""
    Current (Avg/Historical): {current2:.2f}A"""
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
    Reading Time: {dt.strftime('%Y-%m-%d %H:%M:%S')}"""

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
            if self.debug:
                print(f"[+] Connected to {self.host}:{self.port}")
            self._send_handshake()
            return True
        except Exception as e:
            if self.debug:
                print(f"[-] Connection failed: {e}")
            return False

    def disconnect(self):
        """Disconnect from charger"""
        if self.sock:
            self.sock.close()
            self.sock = None
            if self.debug:
                print("[+] Disconnected")

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
                session_energy=get_float(9),
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
            if self.debug:
                print(f"[-] Error getting status: {e}")
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
                    if self.debug:
                        print(f"\n[!] Error: {e}")

                time.sleep(interval)

        except KeyboardInterrupt:
            pass
