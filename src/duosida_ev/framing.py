"""Split the charger's TCP byte stream into whole messages.

TCP gives no message boundaries and the protocol has no length prefix. Every
captured message, in both directions, is exactly three fields: the action,
field 100 (charge box id) and field 101 (message id, a varint). So a message
ends right after its field 101. The splitter walks the stream field by field
and cuts there, which survives reads that split or join messages.
"""

from __future__ import annotations

from .exceptions import ProtocolError
from .protobuf import IncompleteError, WireType, read_field

MESSAGE_ID_FIELD = 101
DEFAULT_MAX_PENDING = 64 * 1024


class FrameSplitter:
    """Incremental splitter: feed it chunks, get complete raw messages back."""

    def __init__(self, max_pending: int = DEFAULT_MAX_PENDING) -> None:
        self._buffer = bytearray()
        self._scan = 0  # offset of the next unread field in _buffer
        self._max_pending = max_pending

    @property
    def pending(self) -> int:
        """Bytes held that do not form a complete message yet."""
        return len(self._buffer)

    def reset(self) -> None:
        """Drop buffered bytes (e.g. after reconnecting)."""
        self._buffer.clear()
        self._scan = 0

    def feed(self, chunk: bytes) -> list[bytes]:
        """Add ``chunk`` and return every message it completes."""
        self._buffer += chunk
        frames: list[bytes] = []
        start = 0
        offset = self._scan
        try:
            while offset < len(self._buffer):
                try:
                    number, wire_type, _value, end = read_field(self._buffer, offset)
                except IncompleteError:
                    break
                offset = end
                if number == MESSAGE_ID_FIELD and wire_type is WireType.VARINT:
                    frames.append(bytes(self._buffer[start:end]))
                    start = end
        except ProtocolError:
            self.reset()
            raise
        del self._buffer[:start]
        self._scan = offset - start
        if len(self._buffer) > self._max_pending:
            self.reset()
            raise ProtocolError("no message boundary within the buffer limit")
        return frames
