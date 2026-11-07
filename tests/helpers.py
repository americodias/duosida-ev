"""Shared test helpers: capture fixtures and a fake charger."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path

from duosida_ev import protocol as p
from duosida_ev.framing import FrameSplitter
from duosida_ev.protobuf import varint_field
from duosida_ev.protocol import Action, Frame

_FIXTURES = json.loads((Path(__file__).parent / "fixtures" / "captures.json").read_text())

DEVICE_ID: str = _FIXTURES["device_id"]
STREAMS: dict[str, bytes] = {k: bytes.fromhex(v) for k, v in _FIXTURES["streams"].items()}
MESSAGES: dict[str, bytes] = {k: bytes.fromhex(v) for k, v in _FIXTURES["messages"].items()}


def frame(name: str) -> Frame:
    """A captured message, decoded."""
    return p.decode_frame(MESSAGES[name])


def status_body(code: int) -> bytes:
    return varint_field(1, code)


Answer = Callable[[Frame], bytes | None]


class FakeCharger:
    """A TCP server that behaves like the charger, built from captured messages.

    It answers the handshake with the captured ``TriggerMessage.conf`` and
    ``BootNotification.req``, records every message the client sends, and
    answers requests through ``answers`` (field number -> body builder;
    returning ``None`` sends nothing).
    """

    def __init__(self, *, send_boot: bool = True, after_boot: tuple[bytes, ...] = ()) -> None:
        self.send_boot = send_boot
        self.after_boot = after_boot
        self.received: list[Frame] = []
        self.queue: asyncio.Queue[Frame] = asyncio.Queue()
        self.connections = 0
        #: One entry per finished connection: "eof" (client sent FIN and read on),
        #: "eof-then-reset" (client closed for good, our later data got a RST) or "reset".
        self.closures: list[str] = []
        #: Keep sending for a moment after the client's FIN, as the charger does.
        self.linger = False
        #: Answer the client's FIN with ours. The real charger did not, in the
        #: sessions observed.
        self.close_on_eof = True
        #: The ``TriggerMessage.conf`` that answers the handshake.
        self.trigger_conf = MESSAGES["trigger_conf"]
        self._released = asyncio.Event()
        self.answers: dict[int, Answer] = {
            Action.CHANGE_CONFIGURATION.request: lambda f: status_body(0),
            Action.REMOTE_START_TRANSACTION.request: lambda f: status_body(0),
            Action.REMOTE_STOP_TRANSACTION.request: lambda f: status_body(0),
            Action.TRIGGER_MESSAGE.request: lambda f: status_body(0),
            Action.DATA_TRANSFER.request: self._data_transfer,
        }
        self._server: asyncio.Server | None = None
        self._writer: asyncio.StreamWriter | None = None
        self.port = 0

    async def __aenter__(self) -> FakeCharger:
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.drop()
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

    @staticmethod
    def _data_transfer(request: Frame) -> bytes | None:
        transfer = p.parse_data_transfer(request.body)
        if transfer.message_id == p.DataMessage.PULL_ALL_CONFIGURATION:
            return frame("configuration_conf").body
        return None

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.connections += 1
        self._writer = writer
        splitter = FrameSplitter()
        try:
            while chunk := await reader.read(4096):
                for raw in splitter.feed(chunk):
                    received = p.decode_frame(raw)
                    self.received.append(received)
                    self.queue.put_nowait(received)
                    self._react(received)
                await writer.drain()
            outcome = "eof"
            if not self.close_on_eof:
                await self._released.wait()
            if self.linger:
                try:
                    for _ in range(3):
                        writer.write(MESSAGES["heartbeat_req"])
                        await writer.drain()
                        await asyncio.sleep(0.05)
                except (ConnectionError, OSError):
                    outcome = "eof-then-reset"
            self.closures.append(outcome)
        except (ConnectionError, OSError):
            self.closures.append("reset")
        finally:
            writer.close()

    def _react(self, received: Frame) -> None:
        if received.field == Action.TRIGGER_MESSAGE.request and received.message_id == 0:
            self.push(self.trigger_conf)
            if self.send_boot:
                self.push(MESSAGES["boot_notification"])
                for raw in self.after_boot:
                    self.push(raw)
            return
        answer = self.answers.get(received.field)
        if answer is None:
            return
        body = answer(received)
        if body is not None:
            self.push(p.encode_frame(received.field - 1, body, DEVICE_ID, received.message_id))

    def push(self, raw: bytes) -> None:
        """Send raw bytes to the connected client."""
        assert self._writer is not None, "no client connected"
        self._writer.write(raw)

    async def expect(self, field: int, timeout: float = 2.0) -> Frame:
        """Wait for the next message from the client with this outer field."""
        async with asyncio.timeout(timeout):
            while True:
                received = await self.queue.get()
                if received.field == field:
                    return received

    async def drop(self) -> None:
        """Close the current client connection."""
        self._released.set()
        writer, self._writer = self._writer, None
        if writer is not None:
            writer.close()
            with suppress(ConnectionError, OSError):
                await writer.wait_closed()
