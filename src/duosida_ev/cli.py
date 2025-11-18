"""
Command-line interface for Duosida EV Charger
"""

import sys
import argparse
import time
import json
import logging

from .charger import DuosidaCharger
from .discovery import discover_chargers, _get_device_id_via_tcp
from .exceptions import DuosidaError

logger = logging.getLogger(__name__)


def setup_logging(verbose: bool = False):
    """Configure logging for CLI"""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format='%(message)s' if not verbose else '%(levelname)s: %(message)s'
    )


def main():
    parser = argparse.ArgumentParser(
        description="Duosida EV Charger - Direct Control Tool",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument('-v', '--verbose', action='store_true',
                        help='Enable verbose output')

    subparsers = parser.add_subparsers(dest='command', help='Command to execute')

    # Discover command
    discover_parser = subparsers.add_parser('discover', help='Discover chargers on the network')
    discover_parser.add_argument('--timeout', type=int, default=5,
                                  help='Discovery timeout in seconds (default: 5)')

    # Status command
    status_parser = subparsers.add_parser('status', help='Get charger status')
    status_parser.add_argument('--host', help='Charger IP address (auto-discovered if not provided)')
    status_parser.add_argument('--device-id', help='Device ID (auto-discovered if not provided)')
    status_parser.add_argument('--port', type=int, default=9988, help='Port (default: 9988)')
    status_parser.add_argument('--json', action='store_true', help='Output in JSON format')

    # Set current command
    current_parser = subparsers.add_parser('set-current', help='Set maximum charging current')
    current_parser.add_argument('--host', help='Charger IP address (auto-discovered if not provided)')
    current_parser.add_argument('--device-id', help='Device ID (auto-discovered if not provided)')
    current_parser.add_argument('--port', type=int, default=9988, help='Port (default: 9988)')
    current_parser.add_argument('amps', type=int, help='Current in amperes (6-32)')

    # Monitor command
    monitor_parser = subparsers.add_parser('monitor', help='Monitor charger continuously')
    monitor_parser.add_argument('--host', help='Charger IP address (auto-discovered if not provided)')
    monitor_parser.add_argument('--device-id', help='Device ID (auto-discovered if not provided)')
    monitor_parser.add_argument('--port', type=int, default=9988, help='Port (default: 9988)')
    monitor_parser.add_argument('--interval', type=float, default=2.0,
                                 help='Polling interval in seconds')
    monitor_parser.add_argument('--duration', type=float, help='Monitor duration in seconds')

    # Start command
    start_parser = subparsers.add_parser('start', help='Start charging')
    start_parser.add_argument('--host', help='Charger IP address (auto-discovered if not provided)')
    start_parser.add_argument('--device-id', help='Device ID (auto-discovered if not provided)')
    start_parser.add_argument('--port', type=int, default=9988, help='Port (default: 9988)')

    # Stop command
    stop_parser = subparsers.add_parser('stop', help='Stop charging')
    stop_parser.add_argument('--host', help='Charger IP address (auto-discovered if not provided)')
    stop_parser.add_argument('--device-id', help='Device ID (auto-discovered if not provided)')
    stop_parser.add_argument('--port', type=int, default=9988, help='Port (default: 9988)')

    # Config command (generic)
    config_parser = subparsers.add_parser('config', help='Set a configuration value')
    config_parser.add_argument('--host', help='Charger IP address (auto-discovered if not provided)')
    config_parser.add_argument('--device-id', help='Device ID (auto-discovered if not provided)')
    config_parser.add_argument('--port', type=int, default=9988, help='Port (default: 9988)')
    config_parser.add_argument('key', help='Configuration key')
    config_parser.add_argument('value', help='Configuration value')

    # Set timeout command
    timeout_parser = subparsers.add_parser('set-timeout', help='Set connection timeout (30-900s)')
    timeout_parser.add_argument('--host', help='Charger IP address (auto-discovered if not provided)')
    timeout_parser.add_argument('--device-id', help='Device ID (auto-discovered if not provided)')
    timeout_parser.add_argument('--port', type=int, default=9988, help='Port (default: 9988)')
    timeout_parser.add_argument('seconds', type=int, help='Timeout in seconds (30-900)')

    # Set max temperature command
    maxtemp_parser = subparsers.add_parser('set-max-temp', help='Set max temperature (85-95°C)')
    maxtemp_parser.add_argument('--host', help='Charger IP address (auto-discovered if not provided)')
    maxtemp_parser.add_argument('--device-id', help='Device ID (auto-discovered if not provided)')
    maxtemp_parser.add_argument('--port', type=int, default=9988, help='Port (default: 9988)')
    maxtemp_parser.add_argument('celsius', type=int, help='Temperature in Celsius (85-95)')

    # Set max voltage command
    maxvolt_parser = subparsers.add_parser('set-max-voltage', help='Set max voltage (265-290V)')
    maxvolt_parser.add_argument('--host', help='Charger IP address (auto-discovered if not provided)')
    maxvolt_parser.add_argument('--device-id', help='Device ID (auto-discovered if not provided)')
    maxvolt_parser.add_argument('--port', type=int, default=9988, help='Port (default: 9988)')
    maxvolt_parser.add_argument('voltage', type=int, help='Voltage in volts (265-290)')

    # Set min voltage command
    minvolt_parser = subparsers.add_parser('set-min-voltage', help='Set min voltage (70-110V)')
    minvolt_parser.add_argument('--host', help='Charger IP address (auto-discovered if not provided)')
    minvolt_parser.add_argument('--device-id', help='Device ID (auto-discovered if not provided)')
    minvolt_parser.add_argument('--port', type=int, default=9988, help='Port (default: 9988)')
    minvolt_parser.add_argument('voltage', type=int, help='Voltage in volts (70-110)')

    # Set direct mode command
    directmode_parser = subparsers.add_parser('set-direct-mode', help='Set direct work mode (plug and charge)')
    directmode_parser.add_argument('--host', help='Charger IP address (auto-discovered if not provided)')
    directmode_parser.add_argument('--device-id', help='Device ID (auto-discovered if not provided)')
    directmode_parser.add_argument('--port', type=int, default=9988, help='Port (default: 9988)')
    directmode_parser.add_argument('enabled', choices=['on', 'off', '1', '0', 'true', 'false'],
                                   help='Enable or disable direct mode')

    # Set LED brightness command
    led_parser = subparsers.add_parser('set-led-brightness', help='Set LED/screen brightness')
    led_parser.add_argument('--host', help='Charger IP address (auto-discovered if not provided)')
    led_parser.add_argument('--device-id', help='Device ID (auto-discovered if not provided)')
    led_parser.add_argument('--port', type=int, default=9988, help='Port (default: 9988)')
    led_parser.add_argument('level', type=int, choices=[0, 1, 3], help='Brightness (0=off, 1=low, 3=high)')

    args = parser.parse_args()

    # Setup logging based on verbose flag and JSON output
    json_output = args.command == 'status' and getattr(args, 'json', False)
    if not json_output:
        setup_logging(verbose=getattr(args, 'verbose', False))

    if not args.command:
        parser.print_help()
        return 1

    try:
        return _execute_command(args)
    except DuosidaError as e:
        logger.error(f"Error: {e}")
        return 1
    except KeyboardInterrupt:
        logger.info("\nOperation cancelled")
        return 130
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        if getattr(args, 'verbose', False):
            import traceback
            traceback.print_exc()
        return 1


def _execute_command(args):
    """Execute the CLI command"""
    # Execute command
    if args.command == 'discover':
        print(f"\nDiscovering Duosida chargers (timeout: {args.timeout}s)...")
        print()

        devices = discover_chargers(timeout=args.timeout)

        if devices:
            print(f"Found {len(devices)} device(s):\n")
            for i, device in enumerate(devices, 1):
                print(f"  {i}. {device['ip']}")
                if device.get('device_id'):
                    print(f"     Device ID: {device['device_id']}")
                print(f"     MAC: {device['mac']}")
                print(f"     Type: {device['type']}")
                print(f"     Firmware: {device['firmware']}")
                print()
        else:
            print("No devices found.")
            print("\nPossible reasons:")
            print("  - No Duosida chargers on this network")
            print("  - Charger is on a different subnet")
            print("  - Firewall blocking UDP port 48890/48899")
            print()
            return 1

    else:
        # Commands that require connection
        # Disable debug output when JSON is requested
        debug = not (args.command == 'status' and getattr(args, 'json', False))

        host = args.host
        device_id = args.device_id

        # Auto-discover if host or device_id not provided
        if not host or not device_id:
            if host and not device_id:
                # Host provided but not device_id - get it via TCP
                if debug:
                    print(f"Retrieving device ID from {host}...")
                device_id = _get_device_id_via_tcp(host, args.port)
                if not device_id:
                    print(f"Error: Could not retrieve device ID from {host}")
                    print("Please specify --device-id manually")
                    return 1
                if debug:
                    print(f"Using device ID: {device_id}")
            else:
                # No host provided - use UDP discovery
                if debug:
                    print("Auto-discovering charger...")
                devices = discover_chargers(timeout=5)

                if not devices:
                    print("Error: No chargers found on network")
                    print("Please specify --host and --device-id manually")
                    return 1

                if len(devices) == 1:
                    # Only one device found, use it
                    device = devices[0]
                    if debug:
                        print(f"Found charger at {device['ip']}")
                else:
                    # Multiple devices found
                    print(f"Error: Found {len(devices)} chargers on network")
                    print("Please specify --host to select one:")
                    for d in devices:
                        print(f"  {d['ip']} - {d.get('device_id', 'unknown')}")
                    return 1

                host = device['ip']
                if not device_id:
                    device_id = device.get('device_id')
                    if not device_id:
                        print(f"Error: Could not retrieve device ID for {host}")
                        print("Please specify --device-id manually")
                        return 1
                    if debug:
                        print(f"Using device ID: {device_id}")

        charger = DuosidaCharger(
            host=host,
            port=args.port,
            device_id=device_id,
            debug=debug
        )

        if not charger.connect():
            return 1

        try:
            if args.command == 'status':
                status = charger.get_status()
                if status:
                    if args.json:
                        print(json.dumps(status.to_dict(), indent=2))
                    else:
                        print(status)
                else:
                    print("Failed to get status")
                    return 1

            elif args.command == 'set-current':
                if not 6 <= args.amps <= 32:
                    print(f"Error: Current must be between 6 and 32 amps")
                    return 1

                if charger.set_max_current(args.amps):
                    print(f"[+] Set max current to {args.amps}A")
                    time.sleep(1)
                    status = charger.get_status()
                    if status:
                        print("\nNew status:")
                        print(status)
                else:
                    print("Failed to set current")
                    return 1

            elif args.command == 'monitor':
                print(f"[+] Monitoring charger (Ctrl+C to stop)...")

                def print_status(status):
                    print("\n" + "="*60)
                    print(status)
                    print("="*60)

                charger.monitor(
                    interval=args.interval,
                    duration=args.duration,
                    callback=print_status
                )

            elif args.command == 'start':
                if charger.start_charging():
                    print("[+] Start command sent")
                    time.sleep(2)
                    status = charger.get_status()
                    if status:
                        print(f"\nStatus: {status.state}")
                        if status.power > 0:
                            print(f"Power: {status.power:.1f}W")
                else:
                    print("Failed to start charging")
                    return 1

            elif args.command == 'stop':
                if charger.stop_charging():
                    print("[+] Stop command sent")
                    time.sleep(2)
                    status = charger.get_status()
                    if status:
                        print(f"\nStatus: {status.state}")
                else:
                    print("Failed to stop charging")
                    return 1

            elif args.command == 'config':
                if charger.set_config(args.key, args.value):
                    print(f"[+] Set {args.key} = {args.value}")
                else:
                    print(f"Failed to set config")
                    return 1

            elif args.command == 'set-timeout':
                if not 30 <= args.seconds <= 900:
                    print(f"Error: Timeout must be between 30 and 900 seconds")
                    return 1

                if charger.set_connection_timeout(args.seconds):
                    print(f"[+] Set connection timeout to {args.seconds}s")
                else:
                    print("Failed to set timeout")
                    return 1

            elif args.command == 'set-max-temp':
                if not 85 <= args.celsius <= 95:
                    print(f"Error: Temperature must be between 85 and 95°C")
                    return 1

                if charger.set_max_temperature(args.celsius):
                    print(f"[+] Set max temperature to {args.celsius}°C")
                else:
                    print("Failed to set temperature")
                    return 1

            elif args.command == 'set-max-voltage':
                if not 265 <= args.voltage <= 290:
                    print(f"Error: Voltage must be between 265 and 290V")
                    return 1

                if charger.set_max_voltage(args.voltage):
                    print(f"[+] Set max voltage to {args.voltage}V")
                else:
                    print("Failed to set voltage")
                    return 1

            elif args.command == 'set-min-voltage':
                if not 70 <= args.voltage <= 110:
                    print(f"Error: Voltage must be between 70 and 110V")
                    return 1

                if charger.set_min_voltage(args.voltage):
                    print(f"[+] Set min voltage to {args.voltage}V")
                else:
                    print("Failed to set voltage")
                    return 1

            elif args.command == 'set-direct-mode':
                enabled = args.enabled.lower() in ('on', '1', 'true')
                if charger.set_direct_work_mode(enabled):
                    state = "enabled" if enabled else "disabled"
                    print(f"[+] Direct work mode {state}")
                else:
                    print("Failed to set direct mode")
                    return 1

            elif args.command == 'set-led-brightness':
                if charger.set_led_brightness(args.level):
                    levels = {0: "off", 1: "low", 3: "high"}
                    print(f"[+] Set LED brightness to {levels.get(args.level, args.level)}")
                else:
                    print("Failed to set brightness")
                    return 1

        finally:
            charger.disconnect()

    return 0


if __name__ == "__main__":
    sys.exit(main())
