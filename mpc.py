import multiprocessing

# from multiprocessing.sharedctypes import Array, Value
from ctypes import Structure, c_double, c_int
from typing import Callable, List
from time import time

from schemas import *


class Mpc:
    __mgr = multiprocessing.Manager()

    __curve: ReflowCurveSchema
    __process: multiprocessing.Process

    __temperatures = __mgr.list()
    __state = ControlState.IDLE

    on_reflow_status: Callable[[ReflowStatusSchema], None]

    @property
    def curve(self) -> ReflowCurveSchema:
        return self.__curve

    @curve.setter
    def curve(self, value: ReflowCurveSchema):
        # TODO can't do this if MPC is running
        self.__curve = value

    @property
    def status(self) -> ControlStatusSchema:
        # TODO
        return ControlStatusSchema()

    @property
    def temperature(self) -> float:
        return self.__temperatures[-1][1]

    @temperature.setter
    def temperature(self, temperature: float):
        self.__temperatures.append(time(), temperature)

    def start(self, curve: ReflowCurveSchema):
        # TODO
        pass

    def stop(self):
        # TODO
        pass

    def __run(self):
        # TODO
        pass
