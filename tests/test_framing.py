import random
import unittest

from duosida_ev.exceptions import ProtocolError
from duosida_ev.framing import FrameSplitter
from duosida_ev.protobuf import varint_field

from .helpers import MESSAGES, STREAMS


def split(stream: bytes, sizes: list[int]) -> list[bytes]:
    splitter = FrameSplitter()
    frames: list[bytes] = []
    offset = 0
    for size in sizes:
        frames += splitter.feed(stream[offset : offset + size])
        offset += size
    frames += splitter.feed(stream[offset:])
    assert splitter.pending == 0
    return frames


class FrameSplitterTest(unittest.TestCase):
    def test_captured_streams(self) -> None:
        # Whole power-cycle capture: 135 charger and 116 app messages.
        self.assertEqual(len(split(STREAMS["power_cycle_charger"], [])), 135)
        self.assertEqual(len(split(STREAMS["power_cycle_app"], [])), 116)

    def test_frames_concatenate_to_stream(self) -> None:
        stream = STREAMS["power_cycle_charger"]
        self.assertEqual(b"".join(split(stream, [])), stream)

    def test_byte_by_byte(self) -> None:
        stream = STREAMS["power_cycle_app"]
        self.assertEqual(split(stream, [1] * len(stream)), split(stream, []))

    def test_random_chunks(self) -> None:
        stream = STREAMS["power_cycle_charger"]
        expected = split(stream, [])
        rng = random.Random(9988)
        for _ in range(20):
            sizes = [rng.randint(1, 700) for _ in range(len(stream) // 50)]
            self.assertEqual(split(stream, sizes), expected)

    def test_partial_message_is_kept(self) -> None:
        message = MESSAGES["boot_notification"]
        splitter = FrameSplitter()
        self.assertEqual(splitter.feed(message[:-1]), [])
        self.assertEqual(splitter.pending, len(message) - 1)
        self.assertEqual(splitter.feed(message[-1:]), [message])
        self.assertEqual(splitter.pending, 0)

    def test_two_messages_in_one_read(self) -> None:
        one, two = MESSAGES["heartbeat_req"], MESSAGES["status_charging"]
        self.assertEqual(FrameSplitter().feed(one + two), [one, two])

    def test_garbage_raises_and_resets(self) -> None:
        splitter = FrameSplitter()
        with self.assertRaises(ProtocolError):
            splitter.feed(b"\x0f\x00")  # wire type 7
        self.assertEqual(splitter.pending, 0)

    def test_buffer_limit(self) -> None:
        splitter = FrameSplitter(max_pending=64)
        with self.assertRaises(ProtocolError):
            splitter.feed(varint_field(1, 1) * 40)  # valid fields, never a field 101

    def test_reset(self) -> None:
        splitter = FrameSplitter()
        splitter.feed(MESSAGES["boot_notification"][:10])
        splitter.reset()
        self.assertEqual(splitter.pending, 0)
        self.assertEqual(splitter.feed(MESSAGES["heartbeat_req"]), [MESSAGES["heartbeat_req"]])


if __name__ == "__main__":
    unittest.main()
