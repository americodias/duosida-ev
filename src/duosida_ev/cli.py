"""``duosida`` command-line tool."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from collections.abc import Awaitable, Callable, Sequence
from contextlib import suppress
from dataclasses import asdict

from . import __version__
from .client import DuosidaClient
from .discovery import BROADCAST, discover_chargers
from .exceptions import DuosidaConnectionError, DuosidaError, DuosidaTimeoutError
from .models import ChargerStatus, ConfigurationStatus

_RECONNECT_DELAYS = (60, 120, 300)
_ON_OFF = {"on": True, "1": True, "true": True, "off": False, "0": False, "false": False}


def _on_off(value: str) -> bool:
    try:
        return _ON_OFF[value.lower()]
    except KeyError:
        raise argparse.ArgumentTypeError("expected on/off") from None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="duosida", description="Local control of Duosida EV chargers."
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    disc = sub.add_parser("discover", help="find chargers on the network")
    disc.add_argument("--timeout", type=float, default=3.0, help="seconds to listen (3)")
    disc.add_argument(
        "--target",
        action="append",
        help="probe destination, repeatable (default 255.255.255.255); "
        "use a subnet broadcast or IP to reach another VLAN",
    )
    disc.add_argument("--no-identify", action="store_true", help="skip the TCP identification")
    disc.add_argument("--json", action="store_true", help="JSON output")

    conn = argparse.ArgumentParser(add_help=False)
    conn.add_argument("--host", help="charger IP (discovered if omitted)")
    conn.add_argument("--port", type=int, default=9988, help="TCP port (9988)")
    conn.add_argument("--timeout", type=float, default=5.0, help="seconds per answer (5)")
    # Accepted for compatibility with 0.1.x; the id now comes from the charger.
    conn.add_argument("--device-id", help=argparse.SUPPRESS)

    def command(name: str, text: str) -> argparse.ArgumentParser:
        return sub.add_parser(name, parents=[conn], help=text)

    status = command("status", "show the current status")
    status.add_argument("--json", action="store_true", help="JSON output")

    monitor = command("monitor", "print every status update")
    monitor.add_argument("--duration", type=float, help="stop after N seconds")
    monitor.add_argument("--json", action="store_true", help="one JSON object per line")

    command("start", "start charging")
    stop = command("stop", "stop charging")
    stop.add_argument("--transaction-id", type=int, help="override the active transaction")

    config = command("get-config", "read the configuration (field names provisional)")
    config.add_argument("--json", action="store_true", help="JSON output")

    setter = command("config", "send any configuration key")
    setter.add_argument("key")
    setter.add_argument("value")

    for name, dest, text in (
        ("set-current", "amps", "max charging current, 6-32 A"),
        ("set-timeout", "seconds", "authorization timeout after plug-in, 30-900 s"),
        ("set-max-temp", "celsius", "max working temperature, 85-95 °C"),
        ("set-max-voltage", "volts", "over-voltage limit, 265-290 V"),
        ("set-min-voltage", "volts", "under-voltage limit, 70-110 V"),
    ):
        command(name, text).add_argument(dest, type=int)
    command("set-led-brightness", "LED brightness: 0 off, 1 low, 3 high").add_argument(
        "level", type=int, choices=[0, 1, 3]
    )
    command("set-direct-mode", "plug-and-charge on/off").add_argument("enabled", type=_on_off)
    command("set-stop-on-disconnect", "stop when the EV unplugs, on/off").add_argument(
        "enabled", type=_on_off
    )
    return parser


async def _resolve_host(args: argparse.Namespace) -> str | None:
    if args.host:
        return str(args.host)
    print("Discovering chargers...", file=sys.stderr)
    found = await discover_chargers(identify=False)
    if len(found) == 1:
        print(f"Using {found[0].host}", file=sys.stderr)
        return found[0].host
    if not found:
        print("No charger found; pass --host.", file=sys.stderr)
    else:
        print("Several chargers found; pass --host:", file=sys.stderr)
        for charger in found:
            print(f"  {charger.host}  {charger.mac or ''}", file=sys.stderr)
    return None


async def _discover(args: argparse.Namespace) -> int:
    found = await discover_chargers(
        args.timeout, targets=args.target or (BROADCAST,), identify=not args.no_identify
    )
    if args.json:
        print(json.dumps([asdict(charger) for charger in found], indent=2))
        return 0 if found else 1
    if not found:
        print("No chargers found. Is the charger on this subnet? Try --target <subnet broadcast>.")
        return 1
    for charger in found:
        print(charger.host)
        for label, value in (
            ("Device ID", charger.device_id),
            ("Model", charger.model),
            ("Firmware", charger.firmware),
            ("MAC", charger.mac),
            ("Module", f"{charger.module_type} {charger.module_firmware}"),
        ):
            if value:
                print(f"  {label + ':':11}{value}")
    return 0


def _print_config_result(what: str, status: ConfigurationStatus) -> None:
    note = " (takes effect after a reboot)" if status is ConfigurationStatus.REBOOT_REQUIRED else ""
    print(f"{what}: accepted{note}")


async def _connected(args: argparse.Namespace) -> int:
    host = await _resolve_host(args)
    if host is None:
        return 1
    if args.device_id:
        print("note: --device-id is ignored; the charger reports its own id", file=sys.stderr)
    async with DuosidaClient(host, args.port, timeout=args.timeout) as client:
        info = client.info
        if info is not None and not getattr(args, "json", False):
            print(f"{info.manufacturer} {info.model} {info.device_id} ({info.firmware})")
        handler = _HANDLERS[args.command]
        return await handler(client, args)


async def _status(client: DuosidaClient, args: argparse.Namespace) -> int:
    status = await client.get_status()
    if args.json:
        info = client.info
        out = {"info": asdict(info) if info else None, "status": status.to_dict()}
        print(json.dumps(out, indent=2))
    else:
        print(status)
    return 0


async def _monitor(client: DuosidaClient, args: argparse.Namespace) -> int:
    loop = asyncio.get_running_loop()
    end = loop.time() + args.duration if args.duration else None

    def show(status: ChargerStatus) -> None:
        if args.json:
            print(json.dumps(status.to_dict()), flush=True)
        else:
            print(f"--- {status.received_at:%H:%M:%S}\n{status}", flush=True)

    failures = 0
    if client.status is not None:  # often arrives during connect()
        show(client.status)
    while end is None or loop.time() < end:
        wait = 60.0 if end is None else max(end - loop.time(), 0.0)
        try:
            show(await client.wait_for_status(timeout=wait))
        except DuosidaTimeoutError:
            if end is None:
                print("no status for 60 s", file=sys.stderr)
        except DuosidaConnectionError as err:
            # The charger refuses quick reconnects; back off like duosida-lan does.
            delay = _RECONNECT_DELAYS[min(failures, len(_RECONNECT_DELAYS) - 1)]
            failures += 1
            print(f"connection lost ({err}); reconnecting in {delay} s", file=sys.stderr)
            await asyncio.sleep(delay)
            with suppress(DuosidaError):
                await client.connect()
                failures = 0
    return 0


async def _start(client: DuosidaClient, args: argparse.Namespace) -> int:
    await client.start_charging()
    print("Start accepted")
    return 0


async def _stop(client: DuosidaClient, args: argparse.Namespace) -> int:
    await client.stop_charging(transaction_id=args.transaction_id)
    print("Stop accepted")
    return 0


async def _get_config(client: DuosidaClient, args: argparse.Namespace) -> int:
    config = await client.get_configuration()
    if args.json:
        print(json.dumps(config.to_dict(), indent=2))
        return 0
    print("Provisional names (inferred from one capture):")
    for name, value in config.to_dict()["provisional"].items():
        print(f"  {name:16} {value}")
    print("Raw fields:")
    for number, value in sorted(config.redacted().items()):
        shown = value.hex() if isinstance(value, bytes) else value
        print(f"  {number:3} {shown!r}")
    return 0


def _setter(
    label: str, call: Callable[[DuosidaClient, argparse.Namespace], Awaitable[ConfigurationStatus]]
) -> Callable[[DuosidaClient, argparse.Namespace], Awaitable[int]]:
    async def run(client: DuosidaClient, args: argparse.Namespace) -> int:
        _print_config_result(label, await call(client, args))
        return 0

    return run


_HANDLERS: dict[str, Callable[[DuosidaClient, argparse.Namespace], Awaitable[int]]] = {
    "status": _status,
    "monitor": _monitor,
    "start": _start,
    "stop": _stop,
    "get-config": _get_config,
    "config": _setter("config", lambda c, a: c.set_config(a.key, a.value)),
    "set-current": _setter("max current", lambda c, a: c.set_max_current(a.amps)),
    "set-timeout": _setter("connection timeout", lambda c, a: c.set_connection_timeout(a.seconds)),
    "set-max-temp": _setter("max temperature", lambda c, a: c.set_max_temperature(a.celsius)),
    "set-max-voltage": _setter("max voltage", lambda c, a: c.set_max_voltage(a.volts)),
    "set-min-voltage": _setter("min voltage", lambda c, a: c.set_min_voltage(a.volts)),
    "set-led-brightness": _setter("LED brightness", lambda c, a: c.set_led_brightness(a.level)),
    "set-direct-mode": _setter("direct mode", lambda c, a: c.set_direct_work_mode(a.enabled)),
    "set-stop-on-disconnect": _setter(
        "stop on disconnect", lambda c, a: c.set_stop_on_disconnect(a.enabled)
    ),
}


async def _run(args: argparse.Namespace) -> int:
    if args.command == "discover":
        return await _discover(args)
    return await _connected(args)


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 1
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        return asyncio.run(_run(args))
    except KeyboardInterrupt:
        return 130
    except DuosidaError as err:
        print(f"Error: {err}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
