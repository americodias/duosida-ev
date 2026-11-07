"""Read Duosida traffic from classic ``.pcap`` files (no third-party deps).

    python tools/pcap.py capture.pcap [more.pcap ...]

prints every message on TCP 9988 in both directions, decoded with the
library's own codec. Import :func:`tcp_streams` to get the reassembled byte
streams (used by ``tools/extract_fixtures.py``).
"""

from __future__ import annotations

import struct
import sys
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from duosida_ev import protocol
from duosida_ev.exceptions import ProtocolError
from duosida_ev.framing import FrameSplitter
from duosida_ev.protobuf import Message, WireType

CHARGER_PORT = 9988
_LINK_OFFSETS = {0: 4, 101: 0, 113: 16, 276: 20}  # null/loopback, raw, SLL, SLL2


@dataclass(frozen=True, slots=True)
class Segment:
    time: float
    src: str
    sport: int
    dst: str
    dport: int
    seq: int
    flags: int
    payload: bytes


def _packets(path: Path) -> Iterator[tuple[float, int, bytes]]:
    data = path.read_bytes()
    magic = data[:4]
    if magic == b"\xd4\xc3\xb2\xa1":
        endian = "<"
    elif magic == b"\xa1\xb2\xc3\xd4":
        endian = ">"
    else:
        raise SystemExit(f"{path}: not a classic pcap file (pcapng is not supported)")
    linktype = struct.unpack(endian + "I", data[20:24])[0]
    offset = 24
    while offset + 16 <= len(data):
        seconds, micros, length, _ = struct.unpack(endian + "IIII", data[offset : offset + 16])
        offset += 16
        yield seconds + micros / 1e6, linktype, data[offset : offset + length]
        offset += length


def _ipv4(linktype: int, frame: bytes) -> bytes | None:
    if linktype == 1:  # Ethernet, with optional 802.1Q tags
        ethertype, offset = struct.unpack(">H", frame[12:14])[0], 14
        while ethertype == 0x8100:
            ethertype, offset = struct.unpack(">H", frame[offset + 2 : offset + 4])[0], offset + 4
        return frame[offset:] if ethertype == 0x0800 else None
    if linktype in _LINK_OFFSETS:
        return frame[_LINK_OFFSETS[linktype] :]
    raise SystemExit(f"unsupported link type {linktype}")


def segments(path: Path) -> Iterator[Segment]:
    """TCP segments to or from the charger port."""
    for time, linktype, frame in _packets(path):
        ip = _ipv4(linktype, frame)
        if not ip or ip[0] >> 4 != 4 or ip[9] != 6:
            continue
        header = (ip[0] & 0x0F) * 4
        total = struct.unpack(">H", ip[2:4])[0]
        tcp = ip[header:total]
        sport, dport, seq = struct.unpack(">HHI", tcp[:8])
        if CHARGER_PORT not in (sport, dport):
            continue
        yield Segment(
            time=time,
            src=".".join(map(str, ip[12:16])),
            sport=sport,
            dst=".".join(map(str, ip[16:20])),
            dport=dport,
            seq=seq,
            flags=tcp[13],
            payload=tcp[(tcp[12] >> 4) * 4 :],
        )


def tcp_streams(path: Path) -> dict[tuple[str, int, str, int], bytes]:
    """Reassembled payload per direction, retransmissions removed."""
    by_flow: dict[tuple[str, int, str, int], list[Segment]] = defaultdict(list)
    for segment in segments(path):
        by_flow[(segment.src, segment.sport, segment.dst, segment.dport)].append(segment)
    streams: dict[tuple[str, int, str, int], bytes] = {}
    for flow, flow_segments in by_flow.items():
        data = bytearray()
        expected: int | None = None
        for segment in flow_segments:
            if segment.flags & 0x02:  # SYN
                expected = (segment.seq + 1) & 0xFFFFFFFF
                continue
            payload = segment.payload
            if not payload:
                continue
            if expected is None:
                expected = segment.seq
            ahead = (segment.seq - expected) & 0xFFFFFFFF
            if ahead >= 0x80000000:  # starts before what we have: retransmission
                overlap = (expected - segment.seq) & 0xFFFFFFFF
                if overlap >= len(payload):
                    continue
                payload = payload[overlap:]
                ahead = 0
            elif ahead:
                print(f"warning: {ahead} bytes missing in {flow}", file=sys.stderr)
            data += payload
            expected = (expected + ahead + len(payload)) & 0xFFFFFFFF
        streams[flow] = bytes(data)
    return streams


def frames(stream: bytes) -> list[bytes]:
    """Split a reassembled stream into messages."""
    splitter = FrameSplitter(max_pending=len(stream) + 1)
    out = splitter.feed(stream)
    if splitter.pending:
        print(f"warning: {splitter.pending} trailing bytes", file=sys.stderr)
    return out


def render(message: Message, indent: int = 1) -> list[str]:
    """Pretty-print a message, recursing into nested messages."""
    pad = "  " * indent
    lines: list[str] = []
    for field in message:
        if field.wire_type is not WireType.LEN:
            value = field.scalar()
            shown = f"{value:.4f}" if isinstance(value, float) else str(value)
            lines.append(f"{pad}{field.number}: {shown}")
            continue
        raw = field.as_bytes()
        text = field.scalar()
        if isinstance(text, str) and text:
            lines.append(f"{pad}{field.number}: {text!r}")
            continue
        try:
            nested = Message.decode(raw) if raw else None
        except ProtocolError:
            nested = None
        if nested is not None and len(nested):
            lines.append(f"{pad}{field.number} {{")
            lines.extend(render(nested, indent + 1))
            lines.append(f"{pad}}}")
        else:
            lines.append(f"{pad}{field.number}: 0x{raw.hex()}")
    return lines


def main(argv: list[str]) -> int:
    for name in argv:
        path = Path(name)
        print(f"# {path.name}")
        for (src, sport, dst, dport), stream in tcp_streams(path).items():
            direction = "app -> charger" if dport == CHARGER_PORT else "charger -> app"
            print(f"## {src}:{sport} -> {dst}:{dport} ({direction}, {len(stream)} bytes)")
            for raw in frames(stream):
                frame = protocol.decode_frame(raw)
                print(f"{frame.describe()}  [{frame.charge_box_id}]")
                print("\n".join(render(frame.message())))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
