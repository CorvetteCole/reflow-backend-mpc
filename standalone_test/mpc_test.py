import importlib.util
import json
import multiprocessing
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
import serial
from enum import Enum
import traceback
import matplotlib.pyplot as plt

from casadi import *
import do_mpc
from scipy.interpolate import interp1d


class State(Enum):
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


should_exit = multiprocessing.Event()
status_lock = multiprocessing.Lock()
serial_lock = multiprocessing.Lock()

# Directory for log files
log_dir = Path(f"test_data/mpc_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}")
log_dir.mkdir(exist_ok=True, parents=True)

mgr = multiprocessing.Manager()

control_pwm = mgr.Value('i', 0)
control_state = mgr.Value('i', 0)

pre_curve_time_s = 15
mpc_lookahead_s = 120
time_step_s = 1

# array of (time, temperature), used for dT
temperature_data = mgr.list()
temperature_derivative_timescale = timedelta(seconds=2)

# convert status dictionary to a shared dictionary with Manager
status = mgr.dict({
    'temperature': 0,
    'state': State.IDLE.value,
    'pwm': 0,
    'door_open': False,
    'error': 0
})

reflow_curve = np.array([
    [90, 90],
    [180, 130],
    [210, 138],
    [240, 165]])

end_temperature = 138

# add 30 seconds @ 35°C to the beginning of the reflow curve, shift the rest accordingly
reflow_curve[:, 0] += pre_curve_time_s
peak_temp = max(reflow_curve[:, 1])  # Define what the peak temperature should be

reflow_curve_function = interp1d(reflow_curve[:, 0], reflow_curve[:, 1], kind='linear', bounds_error=False,
                                 fill_value='extrapolate')

ui_heartbeat_interval_millis = 500

mpc_horizon = int(mpc_lookahead_s / time_step_s)


def setup_model_and_mpc():
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
    mterm = P_T * (T - T_ref) ** 2 + P_T * (1 / (0.01 + casadi.fabs(T_ref - peak_temp))) * (T - peak_temp) ** 2

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


model, mpc = setup_model_and_mpc()


def process_serial_line(line, temperature_data, status):
    try:
        data = json.loads(line)
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        if 'current' in data:
            status['temperature'] = data['current']
            temperature_data.append((datetime.now(), data['current']))
        if 'state' in data:
            status['state'] = State.from_string(data['state']).value
        if 'pwm' in data:
            status['pwm'] = data['pwm']
        if 'door' in data:
            status['door_open'] = data['door'] == 'open'
        if 'error' in data:
            status['error'] = data['error']

        if 'current' in data:
            log_file = log_dir / 'status.log'
            print(f"{now} - {line}")
        else:
            log_file = log_dir / 'message.log'

        with log_file.open('a') as f:
            f.write(f"{now} - {line}\n")
    except json.JSONDecodeError:
        print("Received non-JSON data")
    except Exception as e:
        print(f"Error: {e}")


def handle_communication(should_exit, temperature_data, status, control_pwm, control_state):
    with serial.Serial('/dev/ttyUSB0', 115200, timeout=1) as ser:
        last_sent_time = time.monotonic()
        while not should_exit.is_set():
            # Read from serial
            line = ser.readline().decode().strip()
            if line:  # Ignore empty lines
                process_serial_line(line, temperature_data, status)
            current_time = time.monotonic()
            if current_time - last_sent_time > ui_heartbeat_interval_millis / 1000:
                # Write to serial if PWM value changed or timeout happened
                control_state_enum = State(control_state.value)
                if control_state.value != status['state']:
                    print(f"Sending new state {control_state_enum.name}")
                if control_pwm.value != status['pwm']:
                    print(f"Sending new pwm {control_pwm.value}")
                ser.write(json.dumps(
                    {'state': control_state_enum.name, 'pwm': control_pwm.value}).encode())
                last_sent_time = current_time


def calculate_temperature_derivative(temperature_data):
    derivatation_time = datetime.now() - temperature_derivative_timescale

    temperature_data = [data for data in temperature_data if data[0] > derivatation_time]

    if len(temperature_data) < 2:
        return 0

        # Calculate the differences and average them.
    diffs = []
    for i in range(1, len(temperature_data)):
        time_diff = (temperature_data[i][0] - temperature_data[i - 1][0]).total_seconds()
        temp_diff = temperature_data[i][1] - temperature_data[i - 1][1]
        diffs.append(temp_diff / time_diff)

    return sum(diffs) / len(diffs)


def get_current_state(temperature_data):
    return np.array([[status['temperature']], [calculate_temperature_derivative(temperature_data)]])


def prepare_run():
    global control_pwm, control_state, temperature_data
    # wait until we are below 45°C
    print('Waiting for cooldown')
    control_state.value = State.COOLING.value
    while not should_exit.is_set():
        if status['temperature'] < 45:
            print('Cooldown done')
            break
        time.sleep(1)

    control_state.value = State.IDLE.value
    print('Settling...')
    # Wait for door to be closed for at least 10 seconds
    start_time = datetime.now()
    while not should_exit.is_set():
        if status['door_open']:
            start_time = datetime.now()
            continue
        if (datetime.now() - start_time).seconds > 10:
            print('Oven state settled')
            break
        time.sleep(1)

    # need to run heaters at 100% at least 20 seconds or until oven reaches 60°C
    print('Preheat started')
    control_state.value = State.HEATING.value
    control_pwm.value = 100
    start_time = datetime.now()
    while not should_exit.is_set():
        duration = datetime.now() - start_time
        if duration.seconds > 30 or status['temperature'] > 50:
            print(f'30 seconds elapsed or temperature reached 50°C, preheat done')
            break
        time.sleep(1)


def save_plot(reflow_curve, time_data, temperature_data_list, pwm_data):
    """ Generates a plot for the temperature and PWM data and saves it to a file. """
    plt.figure(figsize=(16, 9))
    ax1 = plt.subplot(111)

    # Plot temperature and PWM data
    ax1.plot(time_data, temperature_data_list, 'b-', label='Temperature [°C]')
    ax1.set_xlabel('Time [s]')
    ax1.set_ylabel('Temperature [°C]', color='b')

    ax1.set_ylim(0, max(reflow_curve[:, 1]) + 50)

    # Create a twin axis for the PWM plot
    ax2 = ax1.twinx()
    ax2.step(time_data, pwm_data, 'g-', label='PWM [%]', where='post')
    ax2.set_ylabel('PWM [%]', color='g')

    # Plot the reflow curve
    ax1.plot(reflow_curve[:, 0], reflow_curve[:, 1], 'r--', label='Reflow Curve')

    # Plot legends
    ax1.legend(loc='upper left')
    ax2.legend(loc='upper right')

    # Save plot to file
    plt.tight_layout()
    plt.savefig(log_dir / 'plot.png')
    plt.close()


def run_curve():
    global control_pwm, control_state, temperature_data

    time_data = []
    temperature_data_list = []
    pwm_data_list = []

    peak_hit = False
    control_state.value = State.HEATING.value
    mpc.x0['T'] = status['temperature']
    mpc.x0['dT'] = calculate_temperature_derivative(temperature_data)
    mpc.set_initial_guess()
    start_time = datetime.now()
    while should_exit.is_set() is False:
        duration = datetime.now() - start_time
        loop_start_time = time.time()
        if status['temperature'] >= peak_temp and not peak_hit:
            peak_hit = True
            print(f"Peak temperature of {peak_temp}°C reached at t={duration.seconds}s")
            print(f'Starting cooldown')
            control_state.value = State.COOLING.value

        if status['temperature'] <= end_temperature and peak_hit:
            print(f"End temperature of {end_temperature}°C reached at t={duration.seconds}s")
            print(f'Ending reflow curve')
            control_state.value = State.IDLE.value
            break

        x0 = np.array([[status['temperature']], [calculate_temperature_derivative(temperature_data)]])

        u0 = mpc.make_step(x0)
        if duration.seconds > reflow_curve[-1, 0] and peak_hit:
            u0 = np.array([[0]])
        # clamp to 0-100 integer
        control_pwm.value = int(np.clip(u0[0, 0], 0, 100))
        print(f'At t={duration.seconds}s, T={x0[0, 0]}, dT={x0[1, 0]}, pwm={control_pwm.value}')

        time_data.append(duration.total_seconds())
        temperature_data_list.append(status['temperature'])
        pwm_data_list.append(control_pwm.value)
        time.sleep(max(0, int(time_step_s - (time.time() - loop_start_time))))
    save_plot(reflow_curve, time_data, temperature_data_list, pwm_data_list)


def main():
    global control_pwm, control_state, temperature_data, should_exit, status

    serial_process = multiprocessing.Process(target=handle_communication, args=(should_exit, temperature_data, status,
                                                                                control_pwm, control_state))
    serial_process.start()

    print('Waiting for 5 seconds')
    time.sleep(5)

    try:
        prepare_run()
        print('Passing control to MPC')
        run_curve()
    except Exception as e:
        print(f'Caught exception {e}')
        # print stack trace
        traceback.print_exc()
        print("Exiting...")
    finally:
        control_state.value = State.IDLE.value
        should_exit.set()
        serial_process.join()


if __name__ == '__main__':
    main()
