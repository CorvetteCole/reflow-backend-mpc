import multiprocessing
from ctypes import c_int
from typing import Callable, List

from schemas import *


class ThermalManagementSystem:
    __log_messages: List[LogMessageSchema]

    __duty_cycle = multiprocessing.Value(c_int)
    __oven_state = multiprocessing.Value(c_int)
    __status_queue = multiprocessing.Queue()

    on_log_message: Callable[[LogMessageSchema], None]
    on_oven_status: Callable[[OvenStatusSchema], None]

    def __init__(self):
        pass

    @property
    def log_messages(self):
        return self.__log_messages

    @property
    def oven_status(self) -> OvenStatusSchema:
        # TODO
        return OvenStatusSchema()

    @property
    def oven_state(self) -> OvenState:
        return OvenState(self.__oven_state.value)

    @oven_state.setter
    def oven_state(self, value: OvenState):
        self.__oven_state.value = value.value

    @property
    def duty_cycle(self) -> int:
        return self.__duty_cycle.value

    @duty_cycle.setter
    def duty_cycle(self, value: int):
        self.__duty_cycle.value = value

    def reset(self):
        # TODO
        pass
