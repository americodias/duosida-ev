"""
Command-line interface for Duosida EV Charger
"""

import sys
import argparse
import time

from .charger import DuosidaCharger
from .discovery import discover_chargers


def main():
    parser = argparse.ArgumentParser(
        description="Duosida EV Charger - Direct Control Tool",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )

    subparsers = parser.add_subparsers(dest='command', help='Command to execute')

    # Discover command
    discover_parser = subparsers.add_parser('discover', help='Discover chargers on the network')
    discover_parser.add_argument('--timeout', type=int, default=5,
                                  help='Discovery timeout in seconds (default: 5)')

    # Status command
    status_parser = subparsers.add_parser('status', help='Get charger status')
    status_parser.add_argument('--host', required=True, help='Charger IP address')
    status_parser.add_argument('--device-id', required=True, help='Device ID')
    status_parser.add_argument('--port', type=int, default=9988, help='Port (default: 9988)')

    # Set current command
    current_parser = subparsers.add_parser('set-current', help='Set maximum charging current')
    current_parser.add_argument('--host', required=True, help='Charger IP address')
    current_parser.add_argument('--device-id', required=True, help='Device ID')
    current_parser.add_argument('--port', type=int, default=9988, help='Port (default: 9988)')
    current_parser.add_argument('amps', type=int, help='Current in amperes (6-32)')

    # Monitor command
    monitor_parser = subparsers.add_parser('monitor', help='Monitor charger continuously')
    monitor_parser.add_argument('--host', required=True, help='Charger IP address')
    monitor_parser.add_argument('--device-id', required=True, help='Device ID')
    monitor_parser.add_argument('--port', type=int, default=9988, help='Port (default: 9988)')
    monitor_parser.add_argument('--interval', type=float, default=2.0,
                                 help='Polling interval in seconds')
    monitor_parser.add_argument('--duration', type=float, help='Monitor duration in seconds')

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 1

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
        charger = DuosidaCharger(
            host=args.host,
            port=args.port,
            device_id=args.device_id,
            debug=True
        )

        if not charger.connect():
            return 1

        try:
            if args.command == 'status':
                status = charger.get_status()
                if status:
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

        finally:
            charger.disconnect()

    return 0


if __name__ == "__main__":
    sys.exit(main())
