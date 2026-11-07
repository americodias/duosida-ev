import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout

from duosida_ev import cli

from .helpers import DEVICE_ID, MESSAGES, FakeCharger


class ParserTest(unittest.TestCase):
    def test_on_off(self) -> None:
        args = cli.build_parser().parse_args(["set-direct-mode", "off", "--host", "h"])
        self.assertFalse(args.enabled)
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            cli.build_parser().parse_args(["set-direct-mode", "maybe"])

    def test_no_command(self) -> None:
        with redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main([]), 1)

    def test_error_exit_code(self) -> None:
        err = io.StringIO()
        with redirect_stderr(err):
            code = cli.main(["status", "--host", "127.0.0.1", "--port", "1", "--timeout", "0.5"])
        self.assertEqual(code, 1)
        self.assertIn("Error:", err.getvalue())


class CommandTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.charger = await FakeCharger(after_boot=(MESSAGES["status_charging"],)).__aenter__()

    async def asyncTearDown(self) -> None:
        await self.charger.__aexit__()

    async def run_cli(self, *argv: str) -> tuple[int, str]:
        args = cli.build_parser().parse_args(
            [*argv, "--host", "127.0.0.1", "--port", str(self.charger.port), "--timeout", "1"]
        )
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(io.StringIO()):
            code = await cli._run(args)
        return code, out.getvalue()

    async def test_status_json(self) -> None:
        code, out = await self.run_cli("status", "--json")
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(data["info"]["device_id"], DEVICE_ID)
        self.assertEqual(data["status"]["state"], "Charging")

    async def test_status_text(self) -> None:
        code, out = await self.run_cli("status")
        self.assertEqual(code, 0)
        self.assertIn("UCHEN DUOSIDA Mode3@32A", out)
        self.assertIn("State:          Charging", out)

    async def test_setters(self) -> None:
        for argv, expected in (
            (["set-current", "16"], "max current: accepted"),
            (["set-led-brightness", "0"], "LED brightness: accepted"),
            (["set-stop-on-disconnect", "on"], "stop on disconnect: accepted"),
            (["config", "VendorDirectWorkMode", "1"], "config: accepted"),
            (["start"], "Start accepted"),
            (["stop"], "Stop accepted"),
        ):
            code, out = await self.run_cli(*argv)
            self.assertEqual(code, 0, argv)
            self.assertIn(expected, out)

    async def test_get_config(self) -> None:
        code, out = await self.run_cli("get-config", "--json")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["provisional"]["max_current"], 16.0)
        code, out = await self.run_cli("get-config")
        self.assertIn("max_current", out)

    async def test_monitor(self) -> None:
        code, out = await self.run_cli("monitor", "--duration", "0.3", "--json")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out.splitlines()[0])["state"], "Charging")

    async def test_ignored_device_id(self) -> None:
        args = cli.build_parser().parse_args(
            ["start", "--host", "127.0.0.1", "--port", str(self.charger.port), "--device-id", "x"]
        )
        err = io.StringIO()
        with redirect_stdout(io.StringIO()), redirect_stderr(err):
            self.assertEqual(await cli._run(args), 0)
        self.assertIn("--device-id is ignored", err.getvalue())


if __name__ == "__main__":
    unittest.main()
