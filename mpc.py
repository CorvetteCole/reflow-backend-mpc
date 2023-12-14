import datetime
import multiprocessing
import threading

from ctypes import Structure, c_double, c_int, c_bool
from typing import Callable, List, Tuple
import time

import do_mpc
from casadi import *
from scipy.interpolate import interp1d

from schemas import *
from utils import calculate_derivative

new_run_threshold_temperature = 45  # need to be below this temperature to start a new run
settle_time = datetime.timedelta(seconds=10)
preheat_time = datetime.timedelta(seconds=30)
temperature_derivation_timescale = datetime.timedelta(seconds=2)
preheat_max_temperature = 50

pre_curve_time_s = 15
mpc_lookahead_s = 120
time_step_s = 1
mpc_horizon = int(mpc_lookahead_s / time_step_s)


def _setup_model_and_mpc(curve: ReflowCurveSchema):
    reflow_curve_function = interp1d(curve.times, curve.temperatures, kind='linear', bounds_error=False,
                                     fill_value='extrapolate')

    peak_temperature = max(curve.temperatures)

    # Parameters for the 2nd order transfer function
    k = 4.7875771211019
    omega = 0.005328475532226316
    xi = 1.54264888649055

    model = do_mpc.model.Model('continuous')

    # Define the states (temperature and its derivative)
    T = model.set_variable(var_type='_x', var_name='T')
    dT = model.set_variable(var_type='_x', var_name='dT')

    # Define the input (heater PWM value)
    u = model.set_variable(var_type='_u', var_name='u')

    # Target temperature as time-varying parameter
    T_ref = model.set_variable('_tvp', 'T_ref')

    # Differential equations
    a1 = SX(k * omega ** 2)
    a2 = SX(2 * xi * omega)
    a3 = SX(omega ** 2)

    dT_next = a1 * u - a2 * dT - a3 * T
    T_next = dT

    # Set the differential equations
    model.set_rhs('T', T_next)
    model.set_rhs('dT', dT_next)

    model.setup()

    mpc = do_mpc.controller.MPC(model)
    mpc.settings.supress_ipopt_output()

    setup_mpc = {
        'n_horizon': mpc_horizon,
        't_step': time_step_s,
        # 'n_robust': 1,
        # 'store_full_solution': True,
    }
    mpc.set_param(**setup_mpc)

    tvp_template = mpc.get_tvp_template()

    def tvp_fun(t_now):
        for k in range(mpc_horizon):
            t = t_now + k * setup_mpc['t_step']
            tvp_template['_tvp', k, 'T_ref'] = reflow_curve_function(t)
        return tvp_template

    mpc.set_tvp_fun(tvp_fun)

    # Penalty weights
    P_T = 1e4  # Penalty weight for temperature deviation
    P_u = 1e-8  # Penalty weight for control action

    # Define the terminal cost (mterm) and Lagrange term (lterm)
    # mterm = P_T * (T - T_ref)**2  # Terminal cost
    lterm = P_T * (T - T_ref) ** 2 + P_u * u ** 2  # Running cost
    mterm = P_T * (T - T_ref) ** 2 + P_T * (1 / (0.01 + casadi.fabs(T_ref - peak_temperature))) * (
            T - peak_temperature) ** 2

    # Set the objective function terms in MPC
    mpc.set_objective(mterm=mterm, lterm=mterm)
    mpc.set_rterm(u=0.01)  # You may additionally use set_rterm() to penalize the control input rate of change

    # Define the bounds for PWM
    mpc.bounds['lower', '_u', 'u'] = 0
    mpc.bounds['upper', '_u', 'u'] = 100

    # Define the bounds for temperature
    # mpc.bounds['lower', '_x', 'T'] = 0
    mpc.bounds['upper', '_x', 'T'] = 270

    mpc.setup()

    return model, mpc


def _run_curve(curve: ReflowCurveSchema, control_state: multiprocessing.Value,
               current_temperature: multiprocessing.Value, current_temperature_derivative: multiprocessing.Value,
               current_door_open: multiprocessing.Value, desired_oven_state: multiprocessing.Value,
               desired_duty_cycle: multiprocessing.Value, curve_duration: multiprocessing.Value,
               should_exit: multiprocessing.Event):
    curve_duration.value = 0
    desired_duty_cycle.value = 0
    desired_oven_state.value = OvenState.IDLE.value
    control_state.value = ControlState.PREPARING.value

    # add pre-curve time
    curve.times = [t + pre_curve_time_s for t in curve.times]

    # get index of peak temperature
    peak_temperature = max(curve.temperatures)
    peak_temperature_index = curve.temperatures.index(peak_temperature)
    end_temperature = curve.temperatures[-1]

    # anything after peak temperature is removed.
    curve.times = curve.times[:peak_temperature_index + 1]
    curve.temperatures = curve.temperatures[:peak_temperature_index + 1]

    model, mpc = _setup_model_and_mpc(curve)

    if current_temperature.value > new_run_threshold_temperature:
        # log "waiting for cooldown"
        desired_oven_state.value = OvenState.COOLING.value
        desired_duty_cycle.value = 0
        while current_temperature.value > new_run_threshold_temperature:
            if should_exit.is_set():
                return
            time.sleep(0.1)

    # log "waiting for door to be closed"
    while current_door_open.value:
        if should_exit.is_set():
            return
        time.sleep(0.1)

    # settling time, door closed
    # log "settling..."
    desired_oven_state.value = OvenState.IDLE.value
    settle_start_time = time.monotonic()
    while time.monotonic() - settle_start_time < settle_time.total_seconds():
        if should_exit.is_set():
            return
        if current_door_open.value:
            # log "door opened during settling"
            settle_start_time = time.monotonic()
        time.sleep(0.1)

    # log "preheating"
    desired_oven_state.value = OvenState.HEATING.value
    desired_duty_cycle.value = 100
    preheat_start_time = time.monotonic()
    while time.monotonic() - preheat_start_time < preheat_time.total_seconds() and current_temperature.value < preheat_max_temperature:
        if should_exit.is_set():
            return
        time.sleep(0.1)

    # log "beginning reflow"
    control_state.value = ControlState.RUNNING.value

    peak_hit = False
    mpc.x0['T'] = current_temperature.value
    mpc.x0['dT'] = current_temperature_derivative.value
    mpc.set_initial_guess()
    curve_start_time = time.monotonic()

    while True:
        try:
            loop_start_time = time.monotonic()
            if should_exit.is_set():
                return

            duration = datetime.timedelta(seconds=time.monotonic() - curve_start_time)

            if current_temperature.value >= peak_temperature and not peak_hit:
                peak_hit = True
                desired_oven_state.value = OvenState.COOLING.value
                print(f"Peak temperature of {peak_temperature}°C reached at t={duration.seconds}s")
                print(f'Starting cooldown')

            if current_temperature.value <= end_temperature:
                print(f"End temperature of {end_temperature}°C reached at t={duration.seconds}s")
                print(f'Ending reflow curve')
                desired_oven_state.value = OvenState.IDLE.value
                control_state.value = ControlState.COMPLETE.value
                break

            x0 = np.array([[current_temperature.value], [current_temperature_derivative.value]])

            u0 = mpc.make_step(x0)
            if duration.seconds > curve.times[-1] and peak_hit:
                u0 = np.array([[0]])
            # clamp to 0-100 integer
            desired_duty_cycle = int(np.clip(u0[0, 0], 0, 100))
            print(f'At t={duration.seconds}s, T={x0[0, 0]}, dT={x0[1, 0]}, pwm={desired_duty_cycle.value}')
            curve_duration.value = duration.seconds
            time.sleep(max(0, int(time_step_s - (time.monotonic() - loop_start_time))))
        except KeyboardInterrupt:
            print("mpc keyboardinterrupt")
            break


class ModelPredictiveControl:
    __curve: ReflowCurveSchema

    __control_process: multiprocessing.Process = None
    __monitor_thread: threading.Thread

    __current_temperature = multiprocessing.Value(c_double)
    __current_temperature_derivative = multiprocessing.Value(c_double)
    __current_door_open = multiprocessing.Value(c_bool)
    __control_state = multiprocessing.Value(c_int)

    __desired_oven_state = multiprocessing.Value(c_int)
    __desired_duty_cycle = multiprocessing.Value(c_int)

    __curve_duration = multiprocessing.Value(c_int)
    __curve_duration_history: List[float] = []
    __curve_temperature_history: List[float] = []

    __should_exit_mpc = multiprocessing.Event()
    __should_exit = multiprocessing.Event()

    __temperatures: List[Tuple[float, float]] = []

    __error_msg = ""

    on_reflow_status: Callable[[ReflowStatusSchema], None]
    on_desired_oven_state: Callable[[OvenState], None]
    on_desired_duty_cycle: Callable[[int], None]

    def __init__(self, on_reflow_status: Callable[[ReflowStatusSchema], None] = None):
        self.on_reflow_status = on_reflow_status
        self.__control_state.value = ControlState.IDLE.value
        self.__desired_oven_state.value = OvenState.IDLE.value
        self.__desired_duty_cycle.value = 0

        self.__curve = ReflowCurveSchema()
        self.__monitor_thread = threading.Thread(target=self.__monitor)
        self.__monitor_thread.start()

    def __del__(self):
        self.__should_exit_mpc.set()
        if self.__control_process and self.__control_process.is_alive():
            self.__control_process.join()
        self.__should_exit.set()
        self.__monitor_thread.join()

    def __monitor(self):
        """
        Monitor the control process and update appropriate things
        """
        last_oven_state = self.__desired_oven_state.value
        last_duty_cycle = self.__desired_duty_cycle.value
        last_reflow_status = {}

        while not self.__should_exit.is_set():
            reflow_status = {
                'state': self.__control_state.value,
            }

            # if mpc should be in a "running" state
            if self.__control_state.value not in [ControlState.IDLE.value, ControlState.CANCELLED.value,
                                                  ControlState.FAULT.value]:
                if not self.busy:
                    # log "control process died"
                    self.__control_state.value = ControlState.FAULT.value
                    self.__desired_oven_state.value = OvenState.IDLE.value
                    self.__desired_duty_cycle.value = 0
                    self.__error_msg = "Control process died"

                else:
                    # update curve history, but only if the duration has changed. Need to handle first duration too
                    if self.__control_state.value == ControlState.RUNNING.value:
                        if not self.__curve_duration_history or self.__curve_duration_history[
                            -1] != self.__curve_duration.value:
                            self.__curve_duration_history.append(self.__curve_duration.value)
                            self.__curve_temperature_history.append(self.temperature)

                    if self.__control_state.value in [ControlState.RUNNING.value, ControlState.COMPLETE.value]:
                        reflow_status['actual_temperatures'] = ReflowCurveSchema().dump({
                            'times': self.__curve_duration_history,
                            'temperatures': self.__curve_temperature_history
                        })
            else:
                # make sure desired oven state is idle and duty cycle is 0
                self.__desired_oven_state.value = OvenState.IDLE.value
                self.__desired_duty_cycle.value = 0

            if self.__control_state.value == ControlState.FAULT.value:
                reflow_status['error'] = self.__error_msg

            # compare reflow_status to last_reflow_status
            if reflow_status != last_reflow_status:
                if self.on_reflow_status:
                    self.on_reflow_status(ReflowStatusSchema().dump(reflow_status))

            if self.__desired_oven_state.value != last_oven_state:
                if self.on_desired_oven_state:
                    self.on_desired_oven_state(OvenState(self.__desired_oven_state.value))

            if self.__desired_duty_cycle.value != last_duty_cycle:
                if self.on_desired_duty_cycle:
                    self.on_desired_duty_cycle(self.__desired_duty_cycle.value)

            last_oven_state = self.__desired_oven_state.value
            last_duty_cycle = self.__desired_duty_cycle.value
            last_reflow_status = reflow_status

            time.sleep(0.1)

    @property
    def busy(self) -> bool:
        return self.__control_process and self.__control_process.is_alive()

    @property
    def curve(self) -> ReflowCurveSchema:
        return self.__curve

    @curve.setter
    def curve(self, value: ReflowCurveSchema):
        if self.busy:
            raise RuntimeError("Can't change curve while running")
        self.__curve = value

    @property
    def status(self) -> ControlStatusSchema:
        return ControlStatusSchema().dump({
            'curve': self.__curve,
            'reflow': {
                'state': ControlState(self.__control_state.value),
                'error': self.__error_msg,
                'actual_temperatures': {
                    'times': self.__curve_duration_history,
                    'temperatures': self.__curve_temperature_history
                }
            }
        })

    @property
    def temperature(self) -> float:
        return self.__temperatures[-1][1]

    @temperature.setter
    def temperature(self, temperature: float):
        current_time = time.monotonic()
        self.__current_temperature.value = temperature
        self.__temperatures.append((current_time, temperature))
        # prune old temperatures
        self.__temperatures = [t for t in self.__temperatures if
                               t[0] > (current_time - temperature_derivation_timescale.total_seconds())]
        self.__current_temperature_derivative.value = calculate_derivative(self.__temperatures)

    @property
    def door_open(self) -> bool:
        return self.__current_door_open.value

    @door_open.setter
    def door_open(self, door_open: bool):
        self.__current_door_open.value = door_open

    def start(self, curve: ReflowCurveSchema):
        if self.busy:
            raise RuntimeError('Control process is already running')

        self.__curve = curve
        self.__control_state.value = ControlState.IDLE.value
        self.__curve_duration_history = []
        self.__curve_temperature_history = []
        self.__curve_duration.value = 0

        self.__control_process = multiprocessing.Process(target=_run_curve,
                                                         args=(curve, self.__control_state,
                                                               self.__current_temperature,
                                                               self.__current_temperature_derivative,
                                                               self.__current_door_open,
                                                               self.__desired_oven_state,
                                                               self.__desired_duty_cycle,
                                                               self.__curve_duration,
                                                               self.__should_exit_mpc))

    def stop(self):
        if self.busy:
            self.__control_state.value = ControlState.CANCELLED.value
            self.__desired_oven_state.value = OvenState.IDLE.value
            self.__should_exit_mpc.set()
