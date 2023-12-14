import importlib.util
import numpy as np
from casadi import *
import do_mpc
import matplotlib.pyplot as plt
import matplotlib as mpl
from scipy.interpolate import interp1d
from do_mpc.data import save_results
from simple_pid import PID

lookahead_s = 120
t_step = 1
n_horizon = int(lookahead_s / t_step)

pid_kp = 300
pid_ki = 0.15
pid_kd = 150.0

pid = PID(pid_kp, pid_ki, pid_kd, setpoint=25, output_limits=(0, 100), sample_time=t_step,
          proportional_on_measurement=False, differential_on_measurement=True)

pre_settle_time_s = 15

# Define the reflow curve
reflow_curve = np.array([
    [90, 90],
    [180, 130],
    [210, 138],
    [240, 165]])
# reflow_curve = np.array([
#     # [30, 100],
#     [120, 150],
#     [150, 183],
#     [210, 235]])

# add 30 seconds @ 35°C to the beginning of the reflow curve, shift the rest accordingly
# reflow_curve = np.vstack((np.array([0, 35]), reflow_curve))
reflow_curve[:, 0] += pre_settle_time_s

reflow_curve_function = interp1d(reflow_curve[:, 0], reflow_curve[:, 1], kind='linear', bounds_error=False,
                                 fill_value='extrapolate')

# Parameters for the 2nd order transfer function
k = 4.7875771211019
omega = 0.005328475532226316
xi = 1.54264888649055
theta = 0  # Assuming no time-delay (e^-theta s) for simplicity

# Set up the MPC problem
model_type = 'continuous'  # either 'discrete' or 'continuous'
model = do_mpc.model.Model(model_type)

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

# Setup MPC
mpc = do_mpc.controller.MPC(model)
mpc.settings.supress_ipopt_output()

setup_mpc = {
    'n_horizon': n_horizon,
    't_step': t_step,
    # 'n_robust': 1,
    # 'store_full_solution': True,
}
mpc.set_param(**setup_mpc)

tvp_template = mpc.get_tvp_template()


# Define the tvp function to return the correct setpoint based on the reflow curve function


def tvp_fun(t_now):
    for k in range(n_horizon):
        t = t_now + k * setup_mpc['t_step']
        tvp_template['_tvp', k, 'T_ref'] = reflow_curve_function(t)
    return tvp_template


mpc.set_tvp_fun(tvp_fun)

# Define a high penalty for the peak temperature not being reached at the desired time:
peak_temp_penalty_weight = 1e6
peak_temp = max(reflow_curve[:, 1])  # Define what the peak temperature should be

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

# Setup Simulator
mpc_simulator = do_mpc.simulator.Simulator(model)
mpc_simulator.set_param(t_step=t_step)

pid_simulator = do_mpc.simulator.Simulator(model)
pid_simulator.set_param(t_step=t_step)

sim_template = mpc_simulator.get_tvp_template()

initial_temperature = 45.51749
initial_derivative = 0.1347846903863636

mpc.x0['T'] = initial_temperature  # Set initial temperature for MPC
mpc.x0['dT'] = initial_derivative  # Set initial temperature derivative for MPC

mpc_simulator.x0['T'] = initial_temperature  # Set initial temperature for the simulator
mpc_simulator.x0['dT'] = initial_derivative  # Set initial temperature derivative for the simulator
mpc_simulator.reset_history()  # Reset any previous simulation history

pid_simulator.x0['T'] = initial_temperature  # Set initial temperature for the simulator
pid_simulator.x0['dT'] = initial_derivative  # Set initial temperature derivative for the simulator
pid_simulator.reset_history()

mpc.set_initial_guess()  # Set the initial guess for the optimization problem based


def sim_tvp_fun(t_now):
    sim_template['T_ref'] = reflow_curve_function(t_now)
    return sim_template


mpc_simulator.set_tvp_fun(sim_tvp_fun)
mpc_simulator.setup()

pid_simulator.set_tvp_fun(sim_tvp_fun)
pid_simulator.setup()

# Initialize the states and inputs
mpc.x0['T'] = reflow_curve[0, 1]
mpc.x0['dT'] = 0
mpc.set_initial_guess()

# Running the MPC
extra_time_s = 120
n_steps = int((reflow_curve[-1, 0] + extra_time_s) / setup_mpc['t_step'])

# Set up the initial plot with dual y-axis for temperature and PWM signal.
plt.ion()  # Turn on interactive mode.
fig, ax1 = plt.subplots(figsize=(16, 9))

ax2 = ax1.twinx()  # Instantiate a second y-axis that shares the same x-axis.

# Generate the time values for which you want to plot the target temperature
plot_times = np.arange(0, reflow_curve[-1, 0] + extra_time_s + t_step, t_step)

# Initialize an array to hold the target temperatures
target_temperatures = np.zeros_like(plot_times)

# Evaluate the tvp_fun for each time step
for i, t_now in enumerate(plot_times):
    # skip times not in the reflow curve
    tvp = tvp_fun(t_now)  # This gives us the full horizon, but we just need the first entry
    target_temperatures[i] = tvp['_tvp', 0, 'T_ref']

# Now plot the target temperatures, which include the decay phase,
# on top of your current figure setup.
ax1.plot(plot_times, target_temperatures, label='Internal Reference Curve', color='lightgray', linestyle=':')

# Plot the reflow curve on the primary y-axis.
ax1.plot(reflow_curve[:, 0], reflow_curve[:, 1], 'r--', label='Original Reflow Curve')

should_error_margin = 10  # Define the error margin as 5 degrees Celsius.
shall_error_margin = 2

# Calculate the lower and upper bounds of the band
should_lower_bound = reflow_curve[:, 1] - should_error_margin
should_upper_bound = reflow_curve[:, 1] + should_error_margin

shall_lower_bound = reflow_curve[:, 1] - shall_error_margin
shall_upper_bound = reflow_curve[:, 1] + shall_error_margin

# Plot the error band around the reflow curve
ax1.fill_between(reflow_curve[:, 0], should_lower_bound, should_upper_bound, color='orange', alpha=0.2,
                 label='Acceptable Temperature Band', zorder=1)

# Plot the error band around the reflow curve
ax1.fill_between(reflow_curve[:, 0], shall_lower_bound, shall_upper_bound, color='red', alpha=0.2,
                 label='Target Temperature Band', zorder=2)

ax1.set_xlabel('Time [s]')
ax1.set_ylabel('Temperature [°C]', color='b')
ax1.tick_params(axis='y', labelcolor='b')

# Prepare the PWM axis (secondary y-axis).
ax2.set_ylabel('PWM [%]', color='g')
ax2.tick_params(axis='y', labelcolor='g')
ax2.set_ylim(0, 100)

# Initialize lines for temperature and control action.
mpc_temperature_line, = ax1.plot([], [], 'b-', label='MPC Temperature', zorder=5)
mpc_control_line, = ax2.step([], [], 'g-', label='MPC control action', where='post', zorder=1)
pid_temperature_line, = ax1.plot([], [], 'c-', label='PID Temperature', zorder=4, linewidth=1)
pid_control_line, = ax2.step([], [], color='lime', label='PID control action', where='post', zorder=0, linewidth=1)

plt.title('Reflow Oven Temperature Control')
fig.tight_layout()  # To ensure the right y-label is not clipped.

# Prepare legends separately for the two axes.
legend1 = ax1.legend(loc='upper left')
legend1.set_zorder(10)
legend1.remove()
ax2.legend(loc='upper right')
ax2.add_artist(legend1)

ax2.set_zorder(1)
ax1.set_zorder(2)
ax1.set_frame_on(False)

# Run the control loop and update the plot in each iteration.
mpc_control_actions = []  # Initialize list for control actions.
pid_control_actions = []
peak_hit = False

for k in range(n_steps):
    t_now = k * setup_mpc['t_step']

    # set pid setpoint to T_ref
    pid.setpoint = reflow_curve_function(t_now)
    u_pid = numpy.array(pid(pid_simulator.x0['T']))
    # need to be correct dimensions [[]], but sometimes is just the number
    if u_pid.ndim == 0:
        u_pid = numpy.array([[u_pid]])

    u_mpc = mpc.make_step(mpc_simulator.x0)
    if t_now > reflow_curve[-1, 0] and peak_hit:
        u_mpc = numpy.array([[0]])
        u_pid = numpy.array([[0]])
    mpc_control_actions.append(u_mpc[0, 0])  # Extract scalar value from u_mpc
    mpc_simulator.make_step(u_mpc)

    pid_control_actions.append(u_pid[0, 0])
    pid_simulator.make_step(u_pid)

    # print simulator temperature and derivative
    print(f"t={t_now} MPC: {mpc_simulator.x0['T']}, {mpc_simulator.x0['dT']}")

    # Update the plot.
    mpc_time = np.array(mpc.data['_time'])
    mpc_temp = np.array(mpc.data['_x', 'T']).flatten()

    mpc_temperature_line.set_data(mpc_time, mpc_temp)
    mpc_control_line.set_data(mpc_time, mpc_control_actions)

    pid_temperature_line.set_data(mpc_time, np.array(pid_simulator.data['_x', 'T']).flatten())
    pid_control_line.set_data(mpc_time, pid_control_actions)

    if mpc_temp[-1] > peak_temp and not peak_hit:
        print(f"reflow complete at {mpc_time[-1]}s")
        peak_hit = True
        ax1.plot(mpc_time[-1], mpc_temp[-1], 'ro', label='Peak Temperature', zorder=10)
        ax1.text(mpc_time[-1] - 20, mpc_temp[-1] + 1, f"{int(mpc_time[-1][0])}/{reflow_curve[-1, 0]}s", color='r',
                 zorder=10)
        # ax1.legend(loc='upper left')
        if 'legend1' in locals():
            legend1.remove()
        legend1 = ax1.legend(loc='upper left')
        legend1.remove()
        legend1.set_zorder(10)
        ax2.add_artist(legend1)

    # Adjust plot limits.
    ax1.set_xlim(0, reflow_curve[-1, 0] + extra_time_s)
    ax1.set_ylim(0, max(reflow_curve[:, 1]) + 50)

    fig.canvas.draw()

    # Refresh the plot.
    # plt.draw()
    plt.pause(0.01)

# After loop completion, keep the figure open.
plt.ioff()  # Turn off interactive mode to keep the figure open.
plt.show()
