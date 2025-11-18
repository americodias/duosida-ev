"""
Duosida EV Charger - Python Library for Direct TCP Control

This library provides direct TCP communication with Duosida EV wall chargers,
bypassing the cloud API for local control and monitoring.

Example usage:
    from duosida_ev import DuosidaCharger, discover_chargers

    # Discover chargers on the network
    devices = discover_chargers()
    for device in devices:
        print(f"Found: {device['ip']}")

    # Connect and get status
    charger = DuosidaCharger(host="192.168.20.95", device_id="0310107112122360374")
    charger.connect()
    status = charger.get_status()
    print(status)
    charger.disconnect()
"""

from .charger import DuosidaCharger, ChargerStatus
from .discovery import discover_chargers

__version__ = "0.1.0"
__all__ = ["DuosidaCharger", "ChargerStatus", "discover_chargers"]
