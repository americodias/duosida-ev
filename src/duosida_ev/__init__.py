"""Local, async control of Duosida EV wall chargers.

The charger speaks OCPP 1.6 encoded as protobuf on TCP port 9988. This
library plays the central system for it, the way the vendor app does::

    import asyncio
    from duosida_ev import DuosidaClient, discover_chargers

    async def main() -> None:
        chargers = await discover_chargers()
        async with DuosidaClient(chargers[0].host) as charger:
            print(charger.info)
            print(await charger.get_status())
            await charger.set_max_current(16)

    asyncio.run(main())
"""

from .client import DuosidaClient
from .discovery import DiscoveredCharger, discover_chargers, identify_charger
from .exceptions import (
    CommandError,
    CommandRejectedError,
    DiscoveryError,
    DuosidaConnectionError,
    DuosidaError,
    DuosidaTimeoutError,
    ProtocolError,
    ValidationError,
)
from .models import (
    ChargerConfiguration,
    ChargerInfo,
    ChargerState,
    ChargerStatus,
    ConfigurationStatus,
    ConnectorStatus,
)

__version__ = "0.2.2"

__all__ = [
    "ChargerConfiguration",
    "ChargerInfo",
    "ChargerState",
    "ChargerStatus",
    "CommandError",
    "CommandRejectedError",
    "ConfigurationStatus",
    "ConnectorStatus",
    "DiscoveredCharger",
    "DiscoveryError",
    "DuosidaClient",
    "DuosidaConnectionError",
    "DuosidaError",
    "DuosidaTimeoutError",
    "ProtocolError",
    "ValidationError",
    "__version__",
    "discover_chargers",
    "identify_charger",
]
