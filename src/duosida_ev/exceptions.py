"""Exceptions raised by :mod:`duosida_ev`."""

from __future__ import annotations


class DuosidaError(Exception):
    """Base class for every error raised by this library."""


class DuosidaConnectionError(DuosidaError):
    """The charger cannot be reached, or the connection was lost."""


class DuosidaTimeoutError(DuosidaError, TimeoutError):
    """The charger did not answer in time."""


class ProtocolError(DuosidaError):
    """The charger sent data that does not match the known protocol."""


class CommandError(DuosidaError):
    """A command could not be completed."""


class CommandRejectedError(CommandError):
    """The charger answered a command with a status other than *Accepted*."""

    def __init__(self, command: str, status: int, status_name: str | None = None) -> None:
        self.command = command
        self.status = status
        self.status_name = status_name or str(status)
        super().__init__(f"charger rejected {command}: {self.status_name}")


class ValidationError(DuosidaError, ValueError):
    """A parameter is outside the range the charger accepts."""


class DiscoveryError(DuosidaError):
    """Network discovery could not run."""
