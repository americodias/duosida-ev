"""Async client for one Duosida charger."""

from __future__ import annotations

import asyncio
import logging
import secrets
import time
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime
from types import TracebackType

from . import protocol as p
from .exceptions import (
    CommandRejectedError,
    DuosidaConnectionError,
    DuosidaError,
    DuosidaTimeoutError,
    ProtocolError,
    ValidationError,
)
from .framing import FrameSplitter
from .models import (
    ChargerConfiguration,
    ChargerInfo,
    ChargerStatus,
    ConfigurationStatus,
    ConnectorStatus,
)
from .protocol import Action, DataMessage, Frame

_LOGGER = logging.getLogger(__name__)

Listener = Callable[[], None]

_READ_SIZE = 4096
# The charger numbers its own messages from 0x08000000 up; stay below that.
_MAX_MESSAGE_ID = 0x07FFFFFF
_REMOTE_STATUS_NAMES = {0: "Accepted", 1: "Rejected"}
#: Seconds to wait for the charger's FIN after ours.
CLOSE_TIMEOUT = 3.0
#: Default seconds for :meth:`DuosidaClient.get_configuration`. Live, every
#: answer came within 2.2 s of one of the sends and at most 6.2 s after the
#: first; with resends every 2 s this is 4 sends (0, 2, 4, 6 s). More resends
#: of an ignored pull were never tried on a real charger.
CONFIGURATION_TIMEOUT = 7.0
#: How many finished requests to remember, to explain answers that come late.
_FINISHED_REQUESTS = 64


def _now() -> int:
    return int(time.time())


class DuosidaClient:
    """One TCP connection to a charger, playing the OCPP central system.

    The charger pushes its state (a ``DataVendorStatusReq`` about every 10 s and
    a ``StatusNotification`` on every state change); the client keeps the latest
    of each and calls the registered listeners. Commands wait for the charger's
    confirmation and raise :class:`~duosida_ev.CommandRejectedError` if it says no.

    Like the vendor app, the client answers the charger's requests (heartbeats
    with the current time, data transfers, transactions) unless
    ``respond_to_charger`` is ``False``. It always answers ``BootNotification``
    with the current time, which keeps the charger's clock right.

    Example::

        async with DuosidaClient("192.168.1.50") as charger:
            status = await charger.get_status()
            await charger.set_max_current(16)
    """

    def __init__(
        self,
        host: str,
        port: int = p.DEFAULT_PORT,
        *,
        timeout: float = 5.0,
        keepalive_interval: float | None = 30.0,
        idle_timeout: float | None = 60.0,
        respond_to_charger: bool = True,
        retry_interval: float | None = 2.0,
    ) -> None:
        """Create a client; nothing happens on the network until :meth:`connect`.

        Args:
            host: Charger IP address or host name.
            port: TCP port (9988).
            timeout: Seconds to wait for the connection and for each answer.
            keepalive_interval: Seconds between ``TriggerMessage(Heartbeat)``
                probes (the vendor app sends one every 5 s); ``None`` disables them.
            idle_timeout: Drop the connection if nothing arrives for this many
                seconds; ``None`` disables the check.
            respond_to_charger: Answer the charger's requests like the vendor app.
            retry_interval: Resend an unanswered request, with the same message
                id, every this many seconds until ``timeout`` (the charger
                sometimes ignores one; the vendor app resends too). ``None``
                sends once.
        """
        self.host = host
        self.port = port
        self.timeout = timeout
        self.keepalive_interval = keepalive_interval
        self.idle_timeout = idle_timeout
        self.respond_to_charger = respond_to_charger
        self.retry_interval = retry_interval

        self._writer: asyncio.StreamWriter | None = None
        self._reader: asyncio.StreamReader | None = None
        self._linger_task: asyncio.Task[None] | None = None
        self._disconnects = 0  # bumped by disconnect(); a connect() in flight gives up
        self._reader_task: asyncio.Task[None] | None = None
        self._watchdog_task: asyncio.Task[None] | None = None
        self._splitter = FrameSplitter()
        self._pending: dict[int, tuple[int, asyncio.Future[Frame]]] = {}
        self._status_waiters: set[asyncio.Future[ChargerStatus]] = set()
        self._boot: asyncio.Future[ChargerInfo] | None = None
        self._listeners: list[Listener] = []
        self._connect_lock = asyncio.Lock()
        self._next_message_id = 1
        self._last_rx = 0.0
        self._heard_id = False  # the charger sent its id on the current connection
        self._trigger_status: int | None = None  # answer to the handshake's trigger
        self._trigger_message_id: int | None = None  # the charger's id on that answer
        # message id -> (action, sent at, answered): explains late or repeated confs.
        self._finished: dict[int, tuple[str, float, bool]] = {}

        self._charge_box_id: str | None = None
        self._info: ChargerInfo | None = None
        self._status: ChargerStatus | None = None
        self._connector: ConnectorStatus | None = None
        self._transaction_id: int | None = None

    # ------------------------------------------------------------ properties

    @property
    def connected(self) -> bool:
        """Whether the TCP connection and its reader are alive."""
        return (
            self._writer is not None
            and not self._writer.is_closing()
            and self._reader_task is not None
            and not self._reader_task.done()
        )

    @property
    def info(self) -> ChargerInfo | None:
        """Identity from the last ``BootNotification``."""
        return self._info

    @property
    def device_id(self) -> str | None:
        """The charger's 19-digit id (its OCPP charge box id)."""
        return self._charge_box_id

    @property
    def status(self) -> ChargerStatus | None:
        """Latest status snapshot; kept after a disconnect."""
        return self._status

    @property
    def connector_status(self) -> ConnectorStatus | None:
        """Latest ``StatusNotification``."""
        return self._connector

    @property
    def transaction_id(self) -> int | None:
        """Active transaction, as last reported by the charger."""
        return self._transaction_id

    # ------------------------------------------------------------ lifecycle

    async def __aenter__(self) -> DuosidaClient:
        await self.connect()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.disconnect()

    async def connect(self) -> ChargerInfo:
        """Connect, identify the charger and set its clock.

        Returns immediately if already connected.
        """
        async with self._connect_lock:
            if self.connected and self._info is not None:
                return self._info
            await self._close(DuosidaConnectionError("reconnecting"))
            generation = self._disconnects
            try:
                async with asyncio.timeout(self.timeout):
                    reader, writer = await asyncio.open_connection(self.host, self.port)
            except TimeoutError as err:
                raise DuosidaTimeoutError(
                    f"timed out connecting to {self.host}:{self.port}"
                ) from err
            except OSError as err:
                raise DuosidaConnectionError(
                    f"cannot connect to {self.host}:{self.port}: {err}"
                ) from err

            if self._disconnects != generation:
                writer.close()  # nothing was sent yet
                raise DuosidaConnectionError("disconnect() was called while connecting")
            loop = asyncio.get_running_loop()
            self._writer = writer
            self._reader = reader
            self._splitter.reset()
            self._heard_id = False
            self._trigger_status = None
            self._trigger_message_id = None
            self._last_rx = loop.time()
            boot: asyncio.Future[ChargerInfo] = loop.create_future()
            self._boot = boot
            self._reader_task = asyncio.create_task(
                self._read_loop(reader), name=f"duosida-ev reader {self.host}"
            )
            try:
                self._write(p.handshake())
                async with asyncio.timeout(self.timeout):
                    info = await asyncio.shield(boot)
            except TimeoutError:
                boot.cancel()
                if not self._heard_id or self._charge_box_id is None or not self.connected:
                    await self._close(DuosidaConnectionError("handshake failed"))
                    raise DuosidaTimeoutError("charger did not identify itself") from None
                if self._disconnects == generation:  # else closed and raised below
                    _LOGGER.warning(
                        "%s: no BootNotification (trigger answered %s), confirming the boot "
                        "unasked and continuing with id only",
                        self.host,
                        _trigger_status_text(self._trigger_status),
                    )
                    self._confirm_boot_unasked()
                info = ChargerInfo(device_id=self._charge_box_id)
                self._info = info
            except BaseException:
                boot.cancel()
                await self._close(DuosidaConnectionError("handshake failed"))
                raise
            finally:
                self._boot = None
            if self._disconnects != generation:
                await self._close(DuosidaConnectionError("disconnected"))
                raise DuosidaConnectionError("disconnect() was called while connecting")

            period = self.keepalive_interval or (
                self.idle_timeout / 3 if self.idle_timeout else None
            )
            if period:
                self._watchdog_task = asyncio.create_task(
                    self._watchdog(period), name=f"duosida-ev watchdog {self.host}"
                )
            _LOGGER.debug("%s: connected to %s", self.host, info)
            self._notify()
            return info

    async def disconnect(self) -> None:
        """Close the connection. Safe to call more than once.

        A ``connect()`` in progress gives up instead of leaving a session open.
        """
        self._disconnects += 1
        async with self._connect_lock:
            await self._close(DuosidaConnectionError("disconnected"))

    def add_listener(self, callback: Listener) -> Callable[[], None]:
        """Call ``callback()`` on every status, connector or connection change.

        Callbacks run in the event loop and must not block. Returns a function
        that removes the listener.
        """
        self._listeners.append(callback)

        def remove() -> None:
            with suppress(ValueError):
                self._listeners.remove(callback)

        return remove

    # -------------------------------------------------------------- reading

    async def get_status(self, *, max_age: float = 30.0, timeout: float = 30.0) -> ChargerStatus:
        """Latest status if newer than ``max_age`` seconds, else wait for the next one."""
        status = self._status
        if status is not None and self.connected:
            age = (datetime.now(UTC) - status.received_at).total_seconds()
            if age <= max_age:
                return status
        return await self.wait_for_status(timeout=timeout)

    async def wait_for_status(self, *, timeout: float | None = 30.0) -> ChargerStatus:
        """Wait for the next status push."""
        if not self.connected:
            raise DuosidaConnectionError("not connected")
        waiter: asyncio.Future[ChargerStatus] = asyncio.get_running_loop().create_future()
        self._status_waiters.add(waiter)
        try:
            async with asyncio.timeout(timeout):
                return await waiter
        except TimeoutError as err:
            raise DuosidaTimeoutError("no status from the charger") from err
        finally:
            self._status_waiters.discard(waiter)

    async def get_configuration(self, *, timeout: float | None = None) -> ChargerConfiguration:
        """Read the whole configuration (``DataPullAllConfigurationReq``).

        The field names in :class:`~duosida_ev.ChargerConfiguration` are
        provisional; see its docstring.

        The charger is slow to answer this request and sometimes ignores a
        send, so ``timeout`` defaults to 7 s (or the client's ``timeout`` if
        longer) rather than the command timeout. Unanswered sends are
        repeated every ``retry_interval``.
        """
        if timeout is None:
            timeout = max(CONFIGURATION_TIMEOUT, self.timeout)
        conf = await self._request(Action.DATA_TRANSFER, p.pull_all_configuration(), timeout)
        return p.parse_configuration(conf.body)

    # ------------------------------------------------------------- commands

    async def start_charging(self, *, connector_id: int = 1, id_tag: str = p.REMOTE_ID_TAG) -> None:
        """Send ``RemoteStartTransaction``; returns once the charger accepts it.

        Acceptance means the charger will try; the car may still not draw power.
        Watch :attr:`status` for the actual state.
        """
        conf = await self._request(
            Action.REMOTE_START_TRANSACTION, p.remote_start_transaction(connector_id, id_tag)
        )
        self._check_remote(conf, "start charging")

    async def stop_charging(self, *, transaction_id: int | None = None) -> None:
        """Send ``RemoteStopTransaction`` for the active (or given) transaction."""
        tx = transaction_id if transaction_id is not None else self._transaction_id
        if tx is None:
            tx = _now() & 0x7FFFFFFF
            _LOGGER.warning("%s: no active transaction known, stopping with id %s", self.host, tx)
        conf = await self._request(Action.REMOTE_STOP_TRANSACTION, p.remote_stop_transaction(tx))
        self._check_remote(conf, "stop charging")

    async def set_config(self, key: str, value: str) -> ConfigurationStatus:
        """Send ``ChangeConfiguration``.

        Returns :attr:`~ConfigurationStatus.ACCEPTED` or
        :attr:`~ConfigurationStatus.REBOOT_REQUIRED`; raises
        :class:`~duosida_ev.CommandRejectedError` otherwise.
        """
        conf = await self._request(Action.CHANGE_CONFIGURATION, p.change_configuration(key, value))
        code = p.conf_status(conf.body)
        try:
            status = ConfigurationStatus(code)
        except ValueError:
            raise CommandRejectedError(f"set {key}", code) from None
        if status in (ConfigurationStatus.REJECTED, ConfigurationStatus.NOT_SUPPORTED):
            raise CommandRejectedError(f"set {key}", code, status.name)
        return status

    async def set_max_current(self, amps: int) -> ConfigurationStatus:
        """Maximum charging current, 6–32 A (``VendorMaxWorkCurrent``)."""
        return await self.set_config("VendorMaxWorkCurrent", _ranged("amps", amps, 6, 32))

    async def set_connection_timeout(self, seconds: int) -> ConfigurationStatus:
        """Seconds to wait for authorization after plug-in, 30–900 (``ConnectionTimeOut``)."""
        return await self.set_config("ConnectionTimeOut", _ranged("seconds", seconds, 30, 900))

    async def set_max_temperature(self, celsius: int) -> ConfigurationStatus:
        """Maximum working temperature, 85–95 °C (``VendorMaxWorkTemperature``)."""
        return await self.set_config(
            "VendorMaxWorkTemperature", _ranged("celsius", celsius, 85, 95)
        )

    async def set_max_voltage(self, volts: int) -> ConfigurationStatus:
        """Over-voltage limit, 265–290 V (``VendorMaxWorkVoltage``)."""
        return await self.set_config("VendorMaxWorkVoltage", _ranged("volts", volts, 265, 290))

    async def set_min_voltage(self, volts: int) -> ConfigurationStatus:
        """Under-voltage limit, 70–110 V (``VendorMinWorkVoltage``)."""
        return await self.set_config("VendorMinWorkVoltage", _ranged("volts", volts, 70, 110))

    async def set_direct_work_mode(self, enabled: bool) -> ConfigurationStatus:
        """Plug-and-charge without authorization (``VendorDirectWorkMode``)."""
        return await self.set_config("VendorDirectWorkMode", "1" if enabled else "0")

    async def set_led_brightness(self, level: int) -> ConfigurationStatus:
        """LED/screen brightness: 0 off, 1 low, 3 high (``VendorLEDStrength``)."""
        if isinstance(level, bool) or level not in (0, 1, 3):
            raise ValidationError("LED brightness must be 0, 1 or 3")
        return await self.set_config("VendorLEDStrength", str(level))

    async def set_stop_on_disconnect(self, enabled: bool) -> ConfigurationStatus:
        """End the transaction when the EV unplugs (``StopTransactionOnEVSideDisconnect``)."""
        return await self.set_config("StopTransactionOnEVSideDisconnect", "1" if enabled else "0")

    # ------------------------------------------------------------ internals

    def _check_remote(self, conf: Frame, command: str) -> None:
        code = p.conf_status(conf.body)
        if code != p.ACCEPTED:
            raise CommandRejectedError(command, code, _REMOTE_STATUS_NAMES.get(code))

    def _new_message_id(self) -> int:
        message_id = self._next_message_id
        self._next_message_id = message_id + 1 if message_id < _MAX_MESSAGE_ID else 1
        return message_id

    def _write(self, data: bytes) -> None:
        writer = self._writer
        if writer is None or writer.is_closing():
            raise DuosidaConnectionError("not connected")
        writer.write(data)

    async def _request(self, action: Action, body: bytes, timeout: float | None = None) -> Frame:
        if not self.connected or self._charge_box_id is None:
            raise DuosidaConnectionError("not connected")
        writer = self._writer
        assert writer is not None
        message_id = self._new_message_id()
        loop = asyncio.get_running_loop()
        future: asyncio.Future[Frame] = loop.create_future()
        self._pending[message_id] = (action.confirmation, future)
        frame = p.encode_frame(action.request, body, self._charge_box_id, message_id)
        sent_at = loop.time()
        try:
            self._write(frame)
            _LOGGER.debug("%s >> %s.req #%s", self.host, action.name, message_id)
            async with asyncio.timeout(timeout if timeout is not None else self.timeout):
                await writer.drain()
                while self.retry_interval is not None:
                    try:
                        return await asyncio.wait_for(asyncio.shield(future), self.retry_interval)
                    except TimeoutError:
                        self._write(frame)
                        _LOGGER.debug("%s >> %s.req #%s again", self.host, action.name, message_id)
                return await future
        except TimeoutError as err:
            raise DuosidaTimeoutError(f"no answer to {action.name}") from err
        except (ConnectionError, OSError) as err:
            raise DuosidaConnectionError(f"write failed: {err}") from err
        finally:
            self._pending.pop(message_id, None)
            answered = future.done() and not future.cancelled() and future.exception() is None
            self._finished[message_id] = (action.name, sent_at, answered)
            while len(self._finished) > _FINISHED_REQUESTS:
                del self._finished[next(iter(self._finished))]

    def _reply(self, request: Frame, body: bytes, *, always: bool = False) -> None:
        if not (always or self.respond_to_charger):
            return
        charge_box_id = self._charge_box_id or request.charge_box_id
        try:
            self._write(p.encode_frame(request.field - 1, body, charge_box_id, request.message_id))
        except DuosidaConnectionError:
            return
        _LOGGER.debug("%s >> conf for %s", self.host, request.describe())

    async def _read_loop(self, reader: asyncio.StreamReader) -> None:
        loop = asyncio.get_running_loop()
        try:
            while True:
                try:
                    chunk = await reader.read(_READ_SIZE)
                except OSError as err:
                    raise DuosidaConnectionError(f"read failed: {err}") from err
                if not chunk:
                    raise DuosidaConnectionError("charger closed the connection")
                self._last_rx = loop.time()
                for raw in self._splitter.feed(chunk):
                    self._handle(raw)
        except asyncio.CancelledError:
            raise
        except DuosidaError as err:
            self._fail(err)
        except Exception as err:
            _LOGGER.exception("%s: reader crashed", self.host)
            self._fail(DuosidaConnectionError(f"reader crashed: {err}"))

    def _handle(self, raw: bytes) -> None:
        try:
            frame = p.decode_frame(raw)
        except ProtocolError as err:
            _LOGGER.warning("%s: undecodable message (%s): %s", self.host, err, raw.hex())
            return
        _LOGGER.debug("%s << %s", self.host, frame.describe())
        cbid = frame.charge_box_id
        if cbid and cbid != p.ANONYMOUS_CHARGE_BOX_ID:
            self._charge_box_id = cbid
            self._heard_id = True
        try:
            self._dispatch(frame)
        except ProtocolError as err:
            _LOGGER.warning("%s: bad %s (%s): %s", self.host, frame.describe(), err, raw.hex())

    def _dispatch(self, frame: Frame) -> None:
        if not frame.is_request:
            entry = self._pending.get(frame.message_id)
            if entry is not None and entry[0] == frame.field and not entry[1].done():
                entry[1].set_result(frame)
            else:
                self._on_unmatched_confirmation(frame)
            return
        if frame.field == Action.BOOT_NOTIFICATION.request:
            self._on_boot(frame)
        elif frame.field == Action.DATA_TRANSFER.request:
            self._on_data_transfer(frame)
        elif frame.field == Action.STATUS_NOTIFICATION.request:
            self._on_status_notification(frame)
        elif frame.field == Action.HEARTBEAT.request:
            self._reply(frame, p.heartbeat_conf(_now()))
        elif frame.field == Action.METER_VALUES.request:
            if (tx := p.meter_values_transaction_id(frame.body)) is not None:
                self._transaction_id = tx
            self._reply(frame, b"")
        elif frame.field == Action.START_TRANSACTION.request:
            if self.respond_to_charger:
                tx = secrets.randbelow(0x7FFFFFFE) + 1
                self._reply(frame, p.start_transaction_conf(tx))
                self._transaction_id = tx
        elif frame.field == Action.STOP_TRANSACTION.request:
            self._transaction_id = None
            self._reply(frame, b"")
        else:
            _LOGGER.debug("%s: ignoring %s", self.host, frame.describe())

    def _on_unmatched_confirmation(self, frame: Frame) -> None:
        """A conf no request is waiting for: the handshake's, or late, or repeated."""
        if (
            frame.field == Action.TRIGGER_MESSAGE.confirmation
            and self._boot is not None
            and not self._boot.done()
        ):
            # The handshake's trigger is not a tracked request, and the charger
            # answers it with its own message id.
            self._trigger_status = p.conf_status(frame.body)
            self._trigger_message_id = frame.message_id
            _LOGGER.debug(
                "%s: BootNotification trigger answered %s",
                self.host,
                _trigger_status_text(self._trigger_status),
            )
            return
        if frame.field == Action.TRIGGER_MESSAGE.confirmation:
            return  # a keep-alive's answer; keep-alives are not tracked requests
        pending = self._pending.get(frame.message_id)
        if pending is not None and pending[1].done():
            # A second answer in the same read, before the request finished.
            _LOGGER.debug("%s: duplicate answer %s", self.host, frame.describe())
            return
        finished = self._finished.get(frame.message_id)
        if finished is None:
            _LOGGER.debug("%s: %s answers no request of ours", self.host, frame.describe())
            return
        action, sent_at, answered = finished
        delay = asyncio.get_running_loop().time() - sent_at
        if answered:
            _LOGGER.debug(
                "%s: duplicate answer to %s #%s, %.1f s after it was sent",
                self.host,
                action,
                frame.message_id,
                delay,
            )
        else:
            _LOGGER.debug(
                "%s: late answer to %s #%s, %.1f s after it was sent (no longer awaited)",
                self.host,
                action,
                frame.message_id,
                delay,
            )

    def _confirm_boot_unasked(self) -> None:
        """Send a ``BootNotification.conf`` that answers no request we received.

        Twice, after a reconnect, the charger accepted the trigger but sent
        neither its ``BootNotification`` nor any status. 0.1 sent this
        confirmation after every trigger, whether the request had come or
        not, with a fixed id that matched no live request, and the charger
        set its clock from it. In the capture the charger's
        ``BootNotification.req`` carries the id just below its answer to the
        trigger, so that is the id used here; 0 if the trigger was not
        answered.
        """
        assert self._charge_box_id is not None
        trigger_id = self._trigger_message_id
        message_id = trigger_id - 1 if trigger_id else 0
        try:
            self._write(
                p.encode_frame(
                    Action.BOOT_NOTIFICATION.confirmation,
                    p.boot_notification_conf(_now()),
                    self._charge_box_id,
                    message_id,
                )
            )
        except DuosidaConnectionError:
            return
        _LOGGER.debug("%s >> BootNotification.conf #%s (unasked)", self.host, message_id)

    def _on_boot(self, frame: Frame) -> None:
        info = p.parse_boot_notification(frame.body, frame.charge_box_id)
        if info.device_id:
            self._charge_box_id = info.device_id
        self._info = info
        # Always answered: the charger sets its clock from currentTime.
        self._reply(frame, p.boot_notification_conf(_now()), always=True)
        if self._boot is not None and not self._boot.done():
            self._boot.set_result(info)
        self._notify()

    def _on_data_transfer(self, frame: Frame) -> None:
        transfer = p.parse_data_transfer(frame.body)
        self._reply(frame, p.data_transfer_conf(transfer.data_field))
        if transfer.message_id != DataMessage.VENDOR_STATUS or transfer.data is None:
            return
        status = p.parse_vendor_status(transfer.data.as_bytes())
        self._status = status
        self._transaction_id = status.transaction_id
        for waiter in tuple(self._status_waiters):
            if not waiter.done():
                waiter.set_result(status)
        self._notify()

    def _on_status_notification(self, frame: Frame) -> None:
        self._connector = p.parse_status_notification(frame.body)
        self._reply(frame, b"")
        self._notify()

    async def _watchdog(self, period: float) -> None:
        loop = asyncio.get_running_loop()
        while True:
            await asyncio.sleep(period)
            idle = loop.time() - self._last_rx
            if self.idle_timeout is not None and idle > self.idle_timeout:
                self._fail(DuosidaConnectionError(f"nothing received for {idle:.0f} s"))
                return
            # Without answers the triggered Heartbeat would only be retransmitted.
            if self.keepalive_interval and self.respond_to_charger and self._charge_box_id:
                body = p.trigger_message(p.TriggerTarget.HEARTBEAT)
                message_id = self._new_message_id()
                frame = p.encode_frame(
                    Action.TRIGGER_MESSAGE.request, body, self._charge_box_id, message_id
                )
                try:
                    self._write(frame)
                except DuosidaConnectionError:
                    return
                _LOGGER.debug("%s >> TRIGGER_MESSAGE.req #%s (keep-alive)", self.host, message_id)

    def _fail(self, err: DuosidaError) -> None:
        """Tear down after a fatal error detected by a background task."""
        writer, self._writer = self._writer, None
        reader, self._reader = self._reader, None
        if writer is None:
            # Already closing: this is how the reader saw the session end.
            _LOGGER.debug("%s: while closing: %s", self.host, err)
            return
        _LOGGER.info("%s: connection lost: %s", self.host, err)
        current = asyncio.current_task()
        for task in (self._reader_task, self._watchdog_task):
            if task is not None and task is not current:
                task.cancel()
        # Close gracefully in the background: the charger may still be sending.
        self._linger_task = asyncio.create_task(
            self._linger(reader, writer), name=f"duosida-ev close {self.host}"
        )
        self._abort_waiters(err)
        self._notify()

    async def _linger(
        self, reader: asyncio.StreamReader | None, writer: asyncio.StreamWriter
    ) -> None:
        """FIN, read until the charger's FIN (or the cap), then close."""
        cap = min(CLOSE_TIMEOUT, self.timeout)
        try:
            with suppress(OSError, RuntimeError):
                if not writer.is_closing() and writer.can_write_eof():
                    writer.write_eof()
                    _LOGGER.debug("%s: FIN sent, reading for up to %.1f s", self.host, cap)
            if reader is not None:
                try:
                    async with asyncio.timeout(cap):
                        while await reader.read(_READ_SIZE):
                            pass
                    _LOGGER.debug("%s: charger closed the connection", self.host)
                except TimeoutError:
                    _LOGGER.debug("%s: no FIN from the charger within %.1f s", self.host, cap)
                except (OSError, RuntimeError) as err:
                    _LOGGER.debug("%s: read failed while closing: %s", self.host, err)
        finally:
            writer.close()
            _LOGGER.debug("%s: closed", self.host)

    async def _close(self, err: DuosidaError) -> None:
        linger, self._linger_task = self._linger_task, None
        if linger is not None and linger is not asyncio.current_task():
            with suppress(asyncio.CancelledError, Exception):
                await linger  # finish closing the old session before anything else
        self._reader = None
        writer, self._writer = self._writer, None
        if writer is not None:
            _LOGGER.debug("%s: closing (%s)", self.host, err)
        reader_task = self._reader_task
        tasks = [t for t in (reader_task, self._watchdog_task) if t is not None]
        self._reader_task = self._watchdog_task = None
        current = asyncio.current_task()
        if writer is not None and reader_task is not None and reader_task is not current:
            await self._shutdown(writer, reader_task)
        for task in tasks:
            if task is not current:
                task.cancel()
        for task in tasks:
            if task is not current:
                with suppress(asyncio.CancelledError, Exception):
                    await task
        if writer is not None:
            writer.close()
            with suppress(OSError, TimeoutError):
                async with asyncio.timeout(self.timeout):
                    await writer.wait_closed()
        self._abort_waiters(err)
        if writer is not None:
            _LOGGER.debug("%s: closed", self.host)
            self._notify()

    async def _shutdown(
        self, writer: asyncio.StreamWriter, reader_task: asyncio.Task[None]
    ) -> None:
        """Close gracefully: send FIN, keep reading until the charger's FIN.

        Closing a socket with unread data sends a RST, and chargers have been
        seen refusing connections for hours after one. The reader keeps
        draining (its answers are dropped, ``_writer`` is already ``None``).
        """
        if reader_task.done() or writer.is_closing():
            return
        try:
            if writer.can_write_eof():
                writer.write_eof()
        except (OSError, RuntimeError):
            return
        cap = min(CLOSE_TIMEOUT, self.timeout)
        _LOGGER.debug("%s: FIN sent, reading for up to %.1f s", self.host, cap)
        loop = asyncio.get_running_loop()
        started = loop.time()
        await asyncio.wait({reader_task}, timeout=cap)
        if reader_task.done():
            _LOGGER.debug(
                "%s: reader finished %.1f s after our FIN", self.host, loop.time() - started
            )
        else:
            _LOGGER.debug("%s: no FIN from the charger within %.1f s", self.host, cap)

    def _abort_waiters(self, err: DuosidaError) -> None:
        futures: list[asyncio.Future[Frame] | asyncio.Future[ChargerStatus]] = [
            future for _, future in self._pending.values()
        ]
        futures.extend(self._status_waiters)
        for future in futures:
            if not future.done():
                future.set_exception(DuosidaConnectionError(str(err)))
        if self._boot is not None and not self._boot.done():
            self._boot.set_exception(DuosidaConnectionError(str(err)))

    def _notify(self) -> None:
        for listener in tuple(self._listeners):
            try:
                listener()
            except Exception:
                _LOGGER.exception("%s: listener failed", self.host)


def _trigger_status_text(status: int | None) -> str:
    if status is None:
        return "no answer"
    return f"{status} ({p.TRIGGER_STATUS_NAMES.get(status, 'unknown')})"


def _ranged(name: str, value: int, low: int, high: int) -> str:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError(f"{name} must be an integer")
    if not low <= value <= high:
        raise ValidationError(f"{name} must be between {low} and {high}, got {value}")
    return str(value)
