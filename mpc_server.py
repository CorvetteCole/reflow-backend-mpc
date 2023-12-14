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

testStatus = ReflowStatusSchema().load({
    'curve': {
        'name': 'Test Curve',
        'description': 'A test curve',
        'times': [0, 30, 60, 90],
        'temperatures': [25, 150, 170, 210]

    },
    'running': True,
    'control_state': ControlState.HEATING.name,
    'progress': 50,
    'actual_temperatures': {
        'times': [0, 30, 60],
        'temperatures': [25, 150, 200]
    }
})

pprint(ReflowStatusSchema().dump(testStatus))

print('\n')
testCurve = ReflowCurveSchema().load({
    'name': 'Test Curve',
    'description': 'A test curve',
    'times': [0, 30, 60, 90],
    'temperatures': [25, 150, 170, 210]
})

pprint(ReflowCurveSchema().dump(testCurve))

app = Flask(__name__)
socketio = SocketIO(app)

mpc = ModelPredictiveControl(on_reflow_status=lambda status: socketio.emit('reflow_status', status))
tms = ThermalManagementSystem(on_log_message=lambda message: socketio.emit('log_message', message),
                              on_oven_status=lambda status: socketio.emit('oven_status', status))

client_subscriptions = {}


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
              schema: ReflowStatusSchema
    """

    dummy_status = ReflowStatusSchema().load({
        'curve': {
            'name': 'Test Curve',
            'description': 'A test curve',
            'curve': [[0, 25], [30, 150], [60, 200], [90, 200], [120, 25]]
        },
        'running': True,
        'control_state': ControlState.HEATING,
        'progress': 50,
        'actual_temperatures': [[0, 25], [30, 150], [60, 200]]
    })

    return dummy_status.dump(), 200


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
    dummy_logs = LogMessagesSchema().load({
        'logs': [
            {
                'message': 'This is a test log',
                'severity': LogSeverity.INFO,
                'time': 123456789
            },
            {
                'message': 'This is another test log',
                'severity': LogSeverity.WARN,
                'time': 123456789
            }
        ]
    })

    return dummy_logs.dump(), 200


# To generate OpenAPI documentation
@app.route("/openapi.json")
def create_openapi_spec():
    return jsonify(spec.to_dict())


@socketio.on('subscribe')
def handle_subscribe(message):
    """Subscribe a client to one or more channels."""
    sid = request.sid
    channels = message.get('channels', [])
    valid_channels = {'oven_status', 'reflow_status', 'log_message'}

    # Store only valid channel names
    selected_channels = {channel for channel in channels if channel in valid_channels}

    if sid not in client_subscriptions:
        client_subscriptions[sid] = selected_channels
    else:
        client_subscriptions[sid].update(selected_channels)

    emit('subscription_update', {'subscribed': True, 'channels': list(client_subscriptions[sid])})


@socketio.on('unsubscribe')
def handle_unsubscribe(message):
    """Unsubscribe a client from one or more channels."""
    sid = request.sid
    channels = message.get('channels', [])
    valid_channels = {'oven_status', 'reflow_status', 'log_message'}

    # Only proceed if the client has any subscriptions
    if sid in client_subscriptions:
        # Remove valid channel names that the client wants to unsubscribe from
        client_subscriptions[sid] -= set(channels).intersection(valid_channels)

        if not client_subscriptions[sid]:
            client_subscriptions.pop(sid)

    emit('subscription_update', {'subscribed': False, 'channels': list(client_subscriptions.get(sid, []))})


@socketio.on('disconnect')
def on_disconnect():
    # Clean up client subscriptions
    client_subscriptions.pop(request.sid, None)


# Background task to emit oven status
def oven_status_emitter():
    # TODO
    while True:
        socketio.emit('oven_status', dict(status))


# Background task to emit curve status
def curve_status_emitter():
    while True:
        curve_info = {
            'running': control_state.value != State.IDLE.value,
            'control_state': control_state.value,
            'control_pwm': control_pwm.value
        }
        socketio.emit('curve_status', curve_info)
        time.sleep(1)


# Background task to emit logs
def logs_emitter():
    log_file_path = log_dir / 'message.log'
    with open(log_file_path, 'r') as log_file:
        while True:
            where = log_file.tell()
            line = log_file.readline()
            if not line:
                time.sleep(1)
                log_file.seek(where)
            else:
                socketio.emit('log_message', {'message': line.strip()})


with app.test_request_context():
    # Register the schema with the spec
    spec.path(view=start_curve, app=app)

if __name__ == "__main__":
    socketio.start_background_task(oven_status_emitter)
    socketio.start_background_task(curve_status_emitter)
    socketio.start_background_task(logs_emitter)
    socketio.run(app, host='0.0.0.0', port=5000)
