from flask import Flask, request, jsonify
from flask_socketio import SocketIO, emit
from apispec import APISpec
from apispec.ext.marshmallow import MarshmallowPlugin
from apispec_webframeworks.flask import FlaskPlugin

from schemas import *
from tms import ThermalManagementSystem
from mpc import ModelPredictiveControl

import warnings

warnings.simplefilter('always')
from pprint import pprint

from constants import ControlState, LogSeverity

# Create an APISpec
spec = APISpec(
    title="Thermal Curve API",
    version="1.0.0",
    openapi_version="3.0.2",
    plugins=[FlaskPlugin(), MarshmallowPlugin()],
)

app = Flask(__name__)
socketio = SocketIO(app)

mpc = ModelPredictiveControl(on_reflow_status=lambda status: socketio.emit('reflow_status', status))
tms = ThermalManagementSystem(on_log_message=lambda message: socketio.emit('log_message', message),
                              on_oven_status=lambda status: socketio.emit('oven_status', status))

mpc.on_desired_duty_cycle = tms.set_duty_cycle
mpc.on_oven_state = tms.set_oven_state


def handle_oven_status(status):
    mpc.temperature = status['temperature']
    mpc.door_open = status['door_open']
    socketio.emit('oven_status', status)


tms.on_oven_status = handle_oven_status


# Use the schema to document the route
@app.route('/start_curve', methods=['POST'])
def start_curve():
    """
    Start a thermal curve process.
    ---
    post:
      description: Start the thermal curve process with the provided curve points.
      requestBody:
        content:
          application/json:
            schema: ReflowCurveSchema
      responses:
        200:
          description: Curve process started.
        400:
          description: Missing or invalid input.
    """
    # Validate the curve data, times and temperatures must ascend
    curve_data = request.get_json()
    try:
        curve = ReflowCurveSchema().load(curve_data)
        mpc.start(curve)
        return jsonify({'status': 'success', 'message': 'Curve process started.'}), 200
    except ValidationError as err:
        return jsonify({'status': 'error', 'message': err.messages}), 400
    except RuntimeError as err:
        return jsonify({'status': 'error', 'message': str(err)}), 400


@app.route('/curve_status', methods=['GET'])
def curve_status():
    """
    Get the current status of the thermal curve process.
    ---
    get:
      description: Get the current status of the thermal curve process.
      responses:
        200:
          description: Current status of the curve process.
          content:
            application/json:
              schema: ControlStatusSchema
    """
    return mpc.status, 200


@app.route('/stop_curve', methods=['POST'])
def stop_curve():
    """
    Stop the thermal curve process.
    ---
    post:
      description: Stop the thermal curve process.
      responses:
        200:
          description: Curve process stopped.
        400:
          description: Missing or invalid input.
    """
    mpc.stop()
    return jsonify({'status': 'success', 'message': 'Curve process stopped.'}), 200


@app.route('/reset', methods=['POST'])
def reset_device():
    """
    Reset the device.
    ---
    post:
      description: Reset the device.
      responses:
        200:
          description: Device reset.
        400:
          description: Missing or invalid input.
    """
    tms.reset()
    return jsonify({'status': 'success', 'message': 'Device reset.'}), 200


@app.route('/logs', methods=['GET'])
def get_logs():
    """
    Get the current logs.
    ---
    get:
      description: Get the current logs.
      responses:
        200:
          description: Current logs.
          content:
            application/json:
              schema: LogMessagesSchema
    """
    return LogMessagesSchema().load({'logs': tms.log_messages}), 200


# To generate OpenAPI documentation
@app.route("/openapi.json")
def create_openapi_spec():
    return jsonify(spec.to_dict())


@socketio.on('disconnect')
def on_disconnect():
    # Clean up client subscriptions
    print('Client disconnected')


with app.test_request_context():
    # Register the schema with the spec
    spec.path(view=start_curve, app=app)
    spec.path(view=curve_status, app=app)
    spec.path(view=stop_curve, app=app)
    spec.path(view=reset_device, app=app)
    spec.path(view=get_logs, app=app)

if __name__ == "__main__":
    try:
        socketio.run(app, host='0.0.0.0', port=5000)
    except KeyboardInterrupt:
        print('Exiting...')
        del tms
        del mpc
