from enum import Enum


class ControlState(Enum):
    IDLE = 0
    HEATING = 1
    COOLING = 2
    FAULT = 3

    # from string
    @classmethod
    def from_string(cls, s):
        # case-insensitive string of name
        try:
            return cls[s.upper()]
        except KeyError:
            raise ValueError()


class LogSeverity(Enum):
    DEBUG = 0
    INFO = 1
    WARN = 2
    CRITICAL = 3

    # from string
    @classmethod
    def from_string(cls, s):
        # case-insensitive string of name
        try:
            return cls[s.upper()]
        except KeyError:
            raise ValueError()
