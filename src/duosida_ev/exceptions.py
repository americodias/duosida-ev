"""
Custom exceptions for Duosida EV Charger library
"""


class DuosidaError(Exception):
    """Base exception for all Duosida errors"""
    pass


class ConnectionError(DuosidaError):
    """Failed to connect to charger"""
    pass


class CommunicationError(DuosidaError):
    """Error during communication with charger"""
    pass


class CommandError(DuosidaError):
    """Error executing a command"""
    pass


class DiscoveryError(DuosidaError):
    """Error during device discovery"""
    pass


class ValidationError(DuosidaError):
    """Invalid parameter value"""
    pass


class TimeoutError(DuosidaError):
    """Operation timed out"""
    pass
