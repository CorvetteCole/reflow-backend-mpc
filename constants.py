from enum import Enum

pre_curve_time_s = 15
mpc_lookahead_s = 120
time_step_s = 1


class OvenState(Enum):
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


class ControlState(Enum):
    IDLE = 0
    PREPARING = 1
    RUNNING = 2
    COMPLETE = 3
    CANCELLED = 4
    FAULT = 5

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
