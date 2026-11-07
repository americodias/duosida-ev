import asyncio
import unittest

from duosida_ev import DiscoveryError, DuosidaConnectionError, DuosidaTimeoutError
from duosida_ev.discovery import DISCOVERY_PROBE, discover_chargers, identify_charger, parse_reply

from .helpers import DEVICE_ID, FakeCharger

REPLY = b"127.0.0.1,AA-BB-CC-DD-EE-FF,smart_wifi,V1.0.4\x00"


class _Responder(asyncio.DatagramProtocol):
    """Answers every probe, like the charger's Wi-Fi module."""

    def __init__(self, reply: bytes) -> None:
        self.reply = reply
        self.probes = 0
        self.transport: asyncio.DatagramTransport | None = None

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        # Not always a DatagramTransport subclass (Python 3.11 selector loop).
        self.transport = transport  # type: ignore[assignment]

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        if data == DISCOVERY_PROBE and self.transport is not None:
            self.probes += 1
            self.transport.sendto(self.reply, addr)


class ParseReplyTest(unittest.TestCase):
    def test_reply(self) -> None:
        charger = parse_reply(b"192.168.1.100,AA-BB-CC-DD-EE-FF,smart_wifi,V1.0\x00", "10.0.0.1")
        assert charger is not None
        self.assertEqual(charger.host, "192.168.1.100")
        self.assertEqual(charger.mac, "aa:bb:cc:dd:ee:ff")
        self.assertEqual(charger.module_type, "smart_wifi")
        self.assertEqual(charger.module_firmware, "V1.0")

    def test_bad_ip_uses_source(self) -> None:
        charger = parse_reply(b"x,AA:BB,smart_wifi,1", "192.168.1.100")
        assert charger is not None
        self.assertEqual(charger.host, "192.168.1.100")

    def test_ignored(self) -> None:
        self.assertIsNone(parse_reply(DISCOVERY_PROBE, "10.0.0.1"))
        self.assertIsNone(parse_reply(b"hello", "10.0.0.1"))


class DiscoverTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        loop = asyncio.get_running_loop()
        self.responder = _Responder(REPLY)
        self.udp, _ = await loop.create_datagram_endpoint(
            lambda: self.responder, local_addr=("127.0.0.1", 0)
        )
        self.udp_port = self.udp.get_extra_info("sockname")[1]

    async def asyncTearDown(self) -> None:
        self.udp.close()

    async def discover(self, **kwargs: object) -> list:  # type: ignore[type-arg]
        options: dict[str, object] = {
            "targets": ["127.0.0.1"],
            "bind": "127.0.0.1",
            "source_port": 0,
            "port": self.udp_port,
            "probe_interval": 0.1,
            "identify": False,
        }
        options.update(kwargs)
        return await discover_chargers(0.35, **options)  # type: ignore[arg-type]

    async def test_repeats_probe_and_deduplicates(self) -> None:
        found = await self.discover()
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].host, "127.0.0.1")
        self.assertEqual(found[0].mac, "aa:bb:cc:dd:ee:ff")
        self.assertIsNone(found[0].device_id)
        self.assertGreaterEqual(self.responder.probes, 3)

    async def test_identify(self) -> None:
        async with FakeCharger() as charger:
            found = await self.discover(identify=True, tcp_port=charger.port)
        self.assertEqual(found[0].device_id, DEVICE_ID)
        self.assertEqual(found[0].model, "DUOSIDA Mode3@32A")
        self.assertIn("V2.5@46ab776+266", found[0].firmware or "")

    async def test_identify_failure_keeps_udp_data(self) -> None:
        async with FakeCharger() as charger:
            port = charger.port
        found = await self.discover(identify=True, tcp_port=port)
        self.assertEqual(found[0].host, "127.0.0.1")
        self.assertIsNone(found[0].device_id)

    async def test_no_targets(self) -> None:
        with self.assertRaises(DiscoveryError):
            await self.discover(targets=[])

    async def test_bind_failure(self) -> None:
        with self.assertRaises(DiscoveryError):
            await self.discover(bind="203.0.113.1")


class IdentifyTest(unittest.IsolatedAsyncioTestCase):
    async def test_identify(self) -> None:
        async with FakeCharger() as charger:
            info = await identify_charger("127.0.0.1", charger.port, timeout=1.0)
            await asyncio.sleep(0.05)
            # Identification answers nothing, so the charger state is untouched.
            self.assertEqual([f.field for f in charger.received], [52])
        self.assertEqual(info.device_id, DEVICE_ID)
        self.assertEqual(info.manufacturer, "UCHEN")
        self.assertEqual(charger.closures, ["eof"])

    async def test_id_only(self) -> None:
        async with FakeCharger(send_boot=False) as charger:
            info = await identify_charger("127.0.0.1", charger.port, timeout=0.3)
        self.assertEqual(info.device_id, DEVICE_ID)
        self.assertEqual(info.model, "")

    async def test_refused(self) -> None:
        async with FakeCharger() as charger:
            port = charger.port
        with self.assertRaises(DuosidaConnectionError):
            await identify_charger("127.0.0.1", port, timeout=0.5)

    async def test_silent(self) -> None:
        held: list[asyncio.StreamWriter] = []
        server = await asyncio.start_server(lambda r, w: held.append(w), "127.0.0.1", 0)
        try:
            with self.assertRaises(DuosidaTimeoutError):
                await identify_charger("127.0.0.1", server.sockets[0].getsockname()[1], timeout=0.3)
        finally:
            for writer in held:
                writer.close()
            server.close()
            await server.wait_closed()


if __name__ == "__main__":
    unittest.main()
