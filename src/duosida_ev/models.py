"""Immutable data models returned by the library."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import IntEnum, StrEnum
from typing import Any

Scalar = int | float | str | bytes

#: CP voltage below this means a vehicle is plugged in (IEC 61851 state A is 12 V).
VEHICLE_CP_THRESHOLD = 10.5


class ChargerState(StrEnum):
    """Connector state, using the OCPP 1.6 ``ChargePointStatus`` names.

    The charger reports the same numeric codes in ``DataVendorStatusReq`` and in
    ``StatusNotification``. Codes 0, 1, 2, 4 and 5 have been observed on a real
    charger; the others follow the OCPP enumeration order.
    """

    AVAILABLE = "Available"
    PREPARING = "Preparing"
    CHARGING = "Charging"
    SUSPENDED_EVSE = "SuspendedEVSE"
    SUSPENDED_EV = "SuspendedEV"
    FINISHING = "Finishing"
    RESERVED = "Reserved"
    UNAVAILABLE = "Unavailable"
    FAULTED = "Faulted"
    UNKNOWN = "Unknown"

    @classmethod
    def from_code(cls, code: int) -> ChargerState:
        """Map a numeric state code; unknown codes become :attr:`UNKNOWN`."""
        return _STATE_BY_CODE.get(code, cls.UNKNOWN)


_STATE_BY_CODE: dict[int, ChargerState] = dict(enumerate(list(ChargerState)[:9]))

_VEHICLE_STATES = frozenset(
    {
        ChargerState.PREPARING,
        ChargerState.CHARGING,
        ChargerState.SUSPENDED_EVSE,
        ChargerState.SUSPENDED_EV,
        ChargerState.FINISHING,
    }
)


class ConfigurationStatus(IntEnum):
    """Answer to a configuration change (OCPP ``ConfigurationStatus``)."""

    ACCEPTED = 0
    REJECTED = 1
    REBOOT_REQUIRED = 2
    NOT_SUPPORTED = 3


@dataclass(frozen=True, slots=True)
class RawField:
    """A field the library does not interpret, kept for diagnostics."""

    number: int
    value: Scalar


@dataclass(frozen=True, slots=True)
class ChargerInfo:
    """Identity the charger announces in its ``BootNotification``."""

    device_id: str
    model: str = ""
    manufacturer: str = ""
    firmware: str = ""


@dataclass(frozen=True, slots=True)
class ChargerStatus:
    """One ``DataVendorStatusReq`` snapshot, pushed by the charger every ~10 s."""

    state: ChargerState
    raw_state: int
    voltage: float
    """Line voltage, V."""
    current: float
    """Charging current, A."""
    power: float
    """``voltage × current``, W."""
    total_energy: float
    """The charger's own energy register, kWh (survives sessions)."""
    session_energy: float
    """Energy of the current session, kWh. Resets when the plug is removed."""
    temperature: float
    """Station temperature, °C."""
    cp_voltage: float
    """Control Pilot voltage, V (12 no vehicle, 9 connected, 6 charging)."""
    session_start: datetime | None
    """Start of the current session by the charger's clock, or ``None``."""
    transaction_id: int | None
    """Active OCPP transaction, or ``None``."""
    id_tag: str | None
    """Tag that started the session (``DirectWorkUser``, ``XC_Remote_Tag``...)."""
    received_at: datetime
    """When this snapshot was decoded (UTC)."""
    unknown: tuple[RawField, ...] = field(default=(), repr=False)

    @property
    def charging(self) -> bool:
        """Whether the charger reports active charging."""
        return self.state is ChargerState.CHARGING

    @property
    def vehicle_connected(self) -> bool:
        """Whether a vehicle is plugged in, from the CP voltage when available."""
        if self.cp_voltage > 0:
            return self.cp_voltage < VEHICLE_CP_THRESHOLD
        return self.state in _VEHICLE_STATES

    @property
    def session_duration(self) -> timedelta | None:
        """Time since :attr:`session_start`, as of :attr:`received_at`.

        Only meaningful if the charger's clock is right; the client sets it
        on every connection.
        """
        if self.session_start is None:
            return None
        return max(self.received_at - self.session_start, timedelta(0))

    def to_dict(self) -> dict[str, Any]:
        """JSON-friendly representation."""
        duration = self.session_duration
        return {
            "state": self.state.value,
            "raw_state": self.raw_state,
            "charging": self.charging,
            "vehicle_connected": self.vehicle_connected,
            "voltage": self.voltage,
            "current": self.current,
            "power": self.power,
            "total_energy": self.total_energy,
            "session_energy": self.session_energy,
            "temperature": self.temperature,
            "cp_voltage": self.cp_voltage,
            "session_start": self.session_start.isoformat() if self.session_start else None,
            "session_duration": duration.total_seconds() if duration is not None else None,
            "transaction_id": self.transaction_id,
            "id_tag": self.id_tag,
            "received_at": self.received_at.isoformat(),
        }

    def __str__(self) -> str:
        lines = [
            f"State:          {self.state.value}",
            f"Vehicle:        {'connected' if self.vehicle_connected else 'not connected'}",
            f"Voltage:        {self.voltage:.1f} V",
            f"Current:        {self.current:.2f} A",
            f"Power:          {self.power:.0f} W",
            f"Session energy: {self.session_energy:.3f} kWh",
            f"Total energy:   {self.total_energy:.3f} kWh",
            f"Temperature:    {self.temperature:.1f} °C",
            f"CP voltage:     {self.cp_voltage:.2f} V",
        ]
        if self.session_start is not None:
            lines.append(f"Session start:  {self.session_start.isoformat()} (charger clock)")
        if self.transaction_id is not None:
            lines.append(f"Transaction:    {self.transaction_id} ({self.id_tag or '-'})")
        return "\n".join(lines)


@dataclass(frozen=True, slots=True)
class ConnectorStatus:
    """A ``StatusNotification``: sent by the charger as soon as the state changes."""

    connector_id: int
    state: ChargerState
    raw_state: int
    error_code: int
    """OCPP ``ChargePointErrorCode``; 6 (``NoError``) is the only value observed."""
    info: str | None
    received_at: datetime

    @property
    def has_error(self) -> bool:
        """Whether the charger reports an error code other than ``NoError``."""
        return self.error_code != NO_ERROR


NO_ERROR = 6


#: Configuration fields reported to hold the Wi-Fi SSID (8) and passwords (7, 9).
SENSITIVE_CONFIGURATION_FIELDS = frozenset({7, 8, 9})
REDACTED = "<redacted>"


@dataclass(frozen=True, slots=True)
class ChargerConfiguration:
    """Configuration returned by ``DataPullAllConfigurationReq``.

    The message carries about 50 unnamed fields. :attr:`max_current` (field 3)
    is verified on a real charger. The other named properties are
    **provisional**: they are inferred by matching values to the ranges each
    setting accepts. Check them against ``raw`` before relying on them.

    Fields 7, 8 and 9 are reported to hold the Wi-Fi passwords and SSID. They
    stay in ``raw``; :meth:`redacted` and :meth:`to_dict` hide them.
    """

    raw: dict[int, Scalar]

    def _float(self, number: int) -> float | None:
        value = self.raw.get(number)
        return float(value) if isinstance(value, int | float) else None

    @property
    def max_temperature(self) -> float | None:
        """``VendorMaxWorkTemperature`` (provisional: field 1)."""
        return self._float(1)

    @property
    def max_voltage(self) -> float | None:
        """``VendorMaxWorkVoltage`` (provisional: field 2)."""
        return self._float(2)

    @property
    def max_current(self) -> float | None:
        """``VendorMaxWorkCurrent``, A (field 3, verified)."""
        return self._float(3)

    @property
    def min_voltage(self) -> float | None:
        """``VendorMinWorkVoltage`` (provisional: field 4)."""
        return self._float(4)

    def redacted(self) -> dict[int, Scalar]:
        """``raw`` with the possible Wi-Fi SSID and passwords hidden."""
        return {
            number: REDACTED if number in SENSITIVE_CONFIGURATION_FIELDS and value else value
            for number, value in self.raw.items()
        }

    def to_dict(self) -> dict[str, Any]:
        """JSON-friendly representation (bytes as hex, passwords hidden)."""
        return {
            "provisional": {
                "max_temperature": self.max_temperature,
                "max_voltage": self.max_voltage,
                "max_current": self.max_current,
                "min_voltage": self.min_voltage,
            },
            "raw": {
                str(number): value.hex() if isinstance(value, bytes) else value
                for number, value in sorted(self.redacted().items())
            },
        }
