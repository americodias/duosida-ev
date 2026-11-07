"""Find chargers on the local network."""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
from collections.abc import Iterable
from contextlib import suppress
from dataclasses import dataclass, replace

from . import protocol as p
from .client import CLOSE_TIMEOUT
from .exceptions import DiscoveryError, DuosidaConnectionError, DuosidaTimeoutError, ProtocolError
from .framing import FrameSplitter
from .models import ChargerInfo
from .protocol import Action

_LOGGER = logging.getLogger(__name__)

DISCOVERY_PORT = 48899
DISCOVERY_SOURCE_PORT = 48890
DISCOVERY_PROBE = b"smart_chargepile_search\x00"
BROADCAST = "255.255.255.255"


@dataclass(frozen=True, slots=True)
class DiscoveredCharger:
    """A charger that answered the UDP probe (``IP,MAC,type,firmware``)."""

    host: str
    mac: str | None = None
    module_type: str | None = None
    """Wi-Fi module type, e.g. ``smart_wifi``."""
    module_firmware: str | None = None
    """Wi-Fi module firmware, from the UDP reply."""
    device_id: str | None = None
    """Filled in by the TCP identification step."""
    model: str | None = None
    firmware: str | None = None
    """Charger firmware, from the TCP identification step."""
    raw: str = ""


class _Collector(asyncio.DatagramProtocol):
    def __init__(self) -> None:
        self.replies: list[tuple[bytes, str]] = []

    def datagram_received(self, data: bytes, addr: tuple[str | object, ...]) -> None:
        self.replies.append((data, str(addr[0])))

    def error_received(self, exc: Exception) -> None:
        _LOGGER.debug("discovery socket error: %s", exc)


def parse_reply(data: bytes, source: str) -> DiscoveredCharger | None:
    """Parse one UDP reply; ``None`` for our own probe or anything unexpected."""
    if data.rstrip(b"\x00") == DISCOVERY_PROBE.rstrip(b"\x00"):
        return None
    text = data.decode("utf-8", errors="replace").strip("\x00 \r\n")
    parts = [part.strip() for part in text.split(",")]
    if len(parts) < 4:
        return None
    try:
        host = str(ipaddress.IPv4Address(parts[0]))
    except ValueError:
        host = source
    return DiscoveredCharger(
        host=host,
        mac=parts[1].lower().replace("-", ":") or None,
        module_type=parts[2] or None,
        module_firmware=parts[3] or None,
        raw=text,
    )


def _socket(bind: str, port: int) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        if hasattr(socket, "SO_REUSEPORT"):
            with suppress(OSError):
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        try:
            sock.bind((bind, port))
        except OSError as err:
            if port == 0:
                raise
            _LOGGER.warning("cannot bind UDP %s (%s); using a random port", port, err)
            sock.bind((bind, 0))
        sock.setblocking(False)
    except OSError:
        sock.close()
        raise
    return sock


async def discover_chargers(
    timeout: float = 3.0,
    *,
    targets: Iterable[str] = (BROADCAST,),
    identify: bool = True,
    bind: str = "0.0.0.0",
    source_port: int = DISCOVERY_SOURCE_PORT,
    port: int = DISCOVERY_PORT,
    tcp_port: int = p.DEFAULT_PORT,
    probe_interval: float = 1.0,
) -> list[DiscoveredCharger]:
    """Broadcast the vendor probe and collect the replies.

    The probe is repeated every ``probe_interval`` seconds until ``timeout``,
    because a single datagram is easily lost on Wi-Fi.

    Args:
        timeout: Seconds to listen.
        targets: Where to send the probe. ``255.255.255.255`` only leaves the
            default interface; add a subnet broadcast (``192.168.1.255``) or a
            unicast address to reach a charger on another VLAN.
        identify: Also open a short TCP session to each charger to read its
            device id, model and firmware.
        bind: Local address to listen on.
        source_port: Local UDP port (the vendor app uses 48890).
        port: Charger UDP port (48899).
        tcp_port: Charger TCP port used for identification (9988).
        probe_interval: Seconds between probes.
    """
    destinations = tuple(dict.fromkeys(targets))
    if not destinations:
        raise DiscoveryError("no discovery targets")
    loop = asyncio.get_running_loop()
    try:
        sock = _socket(bind, source_port)
        transport, collector = await loop.create_datagram_endpoint(_Collector, sock=sock)
    except OSError as err:
        raise DiscoveryError(f"cannot open discovery socket: {err}") from err
    try:
        deadline = loop.time() + max(timeout, 0.0)
        while True:
            for target in destinations:
                try:
                    transport.sendto(DISCOVERY_PROBE, (target, port))
                except OSError as err:
                    _LOGGER.debug("probe to %s failed: %s", target, err)
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            await asyncio.sleep(min(probe_interval, remaining))
            if loop.time() >= deadline:
                break
    finally:
        transport.close()

    found: dict[str, DiscoveredCharger] = {}
    for data, source in collector.replies:
        charger = parse_reply(data, source)
        if charger is not None and charger.host not in found:
            found[charger.host] = charger

    if identify and found:
        results = await asyncio.gather(
            *(
                identify_charger(c.host, tcp_port, timeout=max(timeout, 2.0))
                for c in found.values()
            ),
            return_exceptions=True,
        )
        for charger, result in zip(list(found.values()), results, strict=True):
            if isinstance(result, ChargerInfo):
                found[charger.host] = replace(
                    charger,
                    device_id=result.device_id or None,
                    model=result.model or None,
                    firmware=result.firmware or None,
                )
            else:
                _LOGGER.debug("could not identify %s: %s", charger.host, result)
    return [found[host] for host in sorted(found, key=ipaddress.IPv4Address)]


async def _close_gracefully(
    reader: asyncio.StreamReader, writer: asyncio.StreamWriter, timeout: float
) -> None:
    """Send FIN and drain until the charger's FIN; a RST can lock the charger out."""
    with suppress(OSError, RuntimeError, TimeoutError):
        if writer.can_write_eof():
            writer.write_eof()
        async with asyncio.timeout(min(CLOSE_TIMEOUT, timeout)):
            while await reader.read(4096):
                pass
    writer.close()
    with suppress(OSError, TimeoutError):
        async with asyncio.timeout(1.0):
            await writer.wait_closed()


async def identify_charger(
    host: str, port: int = p.DEFAULT_PORT, *, timeout: float = 3.0
) -> ChargerInfo:
    """Read a charger's identity over a short TCP session.

    Sends the handshake, waits for the ``BootNotification`` and disconnects
    without answering anything, so the charger's state is left untouched.
    """
    try:
        async with asyncio.timeout(timeout):
            reader, writer = await asyncio.open_connection(host, port)
    except TimeoutError as err:
        raise DuosidaTimeoutError(f"timed out connecting to {host}:{port}") from err
    except OSError as err:
        raise DuosidaConnectionError(f"cannot connect to {host}:{port}: {err}") from err

    splitter = FrameSplitter()
    charge_box_id: str | None = None
    try:
        writer.write(p.handshake())
        async with asyncio.timeout(timeout):
            await writer.drain()
            while True:
                chunk = await reader.read(4096)
                if not chunk:
                    break
                for raw in splitter.feed(chunk):
                    try:
                        frame = p.decode_frame(raw)
                    except ProtocolError:
                        continue
                    if frame.charge_box_id not in ("", p.ANONYMOUS_CHARGE_BOX_ID):
                        charge_box_id = frame.charge_box_id
                    if frame.field == Action.BOOT_NOTIFICATION.request:
                        return p.parse_boot_notification(frame.body, frame.charge_box_id)
    except TimeoutError:
        pass
    except OSError as err:
        raise DuosidaConnectionError(f"identification of {host} failed: {err}") from err
    finally:
        await _close_gracefully(reader, writer, timeout)
    if charge_box_id is not None:
        return ChargerInfo(device_id=charge_box_id)
    raise DuosidaTimeoutError(f"{host} did not identify itself")
