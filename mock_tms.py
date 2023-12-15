import threading
import time
from schemas import OvenStatusSchema, LogMessageSchema
from constants import OvenState, LogSeverity
from typing import Callable, List

# Simulation code adapted from standalone_test/mpc_sim.py
from casadi import SX, DM, vertcat
import numpy as np

from pprint import pprint

set_error_after_s = 45  # Simulate an error after this many seconds

# Parameters for the 2nd order transfer function
k = 4.7875771211019
omega = 0.005328475532226316
xi = 1.54264888649055

# Define the states (temperature and its derivative)
T = DM(25)  # Initial temperature
dT = DM(0)  # Initial temperature derivative

# Define the input (heater PWM value)
u = DM(0)  # Initial duty cycle

# Differential equations
a1 = SX(k * omega ** 2)
a2 = SX(2 * xi * omega)
a3 = SX(omega ** 2)


def system_dynamics(T, dT, u, time_step=1.0):
    dT_next = a1 * u - a2 * dT - a3 * T
    T_next = T + dT * time_step  # Integrate temperature over time step using the previous derivative
    return T_next, dT_next


class MockThermalManagementSystem:
    __log_messages: List[LogMessageSchema] = []
    __oven_status: OvenStatusSchema = None
    __oven_state = OvenState.IDLE

    on_log_message: Callable[[LogMessageSchema], None] = None
    on_oven_status: Callable[[OvenStatusSchema], None] = None
    on_reset: Callable[[], None] = None

    def __init__(self, on_log_message: Callable[[LogMessageSchema], None] = None,
                 on_oven_status: Callable[[OvenStatusSchema], None] = None,
                 on_reset: Callable[[], None] = None):
        self.on_log_message = on_log_message
        self.on_oven_status = on_oven_status
        self.on_reset = on_reset

        # Initialize the simulation environment here
        self.__T = T
        self.__dT = dT
        self.__u = u
        self.__simulation_thread = threading.Thread(target=self.__simulate_periodically)
        self.__simulation_thread.daemon = True
        self.__simulation_thread.start()

    def __simulate_periodically(self):
        error_time = time.time() + set_error_after_s
        while True:

            # if time.time() > error_time:
            #     self.__oven_state = OvenState.FAULT

            self.__duty_cycle = 0
            self.__simulate()
            time.sleep(1)  # Simulate every second

    def __simulate(self):
        # Update the system dynamics based on the current duty cycle
        self.__T, self.__dT = system_dynamics(self.__T, self.__dT, self.__u, time_step=1.0)
        # Update the oven status with the new temperature and other required fields
        self.__oven_status = OvenStatusSchema().load({
            "time": int(time.time() * 1000),
            "temperature": float(self.__T),
            "state": self.__oven_state.value,
            "duty_cycle": self.duty_cycle,
            "door_open": False,  # Assuming door is closed for simulation; update if door status is available
            "errors": []  # Assuming no errors for simulation; update if error information is available
        })
        # If there are any registered callbacks, call them with the new oven status
        if self.on_oven_status:
            self.on_oven_status(self.__oven_status)

        pprint(self.__oven_status)

    @property
    def log_messages(self):
        return self.__log_messages

    @property
    def oven_status(self) -> OvenStatusSchema:
        return self.__oven_status

    @property
    def oven_state(self) -> OvenState:
        return self.__oven_state

    def set_oven_state(self, value: OvenState):
        self.__oven_state = value

    @property
    def duty_cycle(self) -> int:
        return int(self.__u)

    def set_duty_cycle(self, value: int):
        self.__u = DM(value)  # Update the duty cycle in the simulation model

    def reset(self):
        # Reset the simulation environment here
        self.__T = DM(25)  # Reset temperature to initial value
        self.__dT = DM(0)  # Reset temperature derivative to initial value
        self.__u = DM(0)  # Reset duty cycle to initial value
        # Call the simulation to update the oven status
        self.__simulate()

# The actual simulation code from mpc_sim.py will be integrated into the methods above.
