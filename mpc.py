import multiprocessing
import threading

# from multiprocessing.sharedctypes import Array, Value
from ctypes import Structure, c_double, c_int
from typing import Callable, List
from time import time

from schemas import *


def _run_curve(curve: ReflowCurveSchema, state: multiprocessing.Value,
               should_exit: multiprocessing.Event):
    # TODO
    pass


class ModelPredictiveControl:
    __mgr = multiprocessing.Manager()

    __curve: ReflowCurveSchema

    __control_process: multiprocessing.Process
    __monitor_thread: threading.Thread

    __temperatures = []
    __state = ControlState.IDLE

    on_reflow_status: Callable[[ReflowStatusSchema], None]

    def __init__(self, on_reflow_status: Callable[[ReflowStatusSchema], None] = None):
        self.on_reflow_status = on_reflow_status

        self.__curve = ReflowCurveSchema()

        self.__control_process = multiprocessing.Process(target=self.__run)


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
