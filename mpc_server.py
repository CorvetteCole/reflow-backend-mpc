import json
import multiprocessing
import queue
import os
import uuid

from flask import Flask, request, jsonify
from flask_socketio import SocketIO, emit
from apispec import APISpec
from apispec.ext.marshmallow import MarshmallowPlugin
from apispec_webframeworks.flask import FlaskPlugin

from schemas import *
# from tms import ThermalManagementSystem
from mock_tms import MockThermalManagementSystem as ThermalManagementSystem
from mpc import ModelPredictiveControl

import warnings

warnings.simplefilter('always')
from pprint import pprint

from constants import ControlState, LogSeverity

# Create an APISpec and include custom operations for Socket.IO events
spec = APISpec(
    title="Thermal Curve API",
    version="1.0.0",
    openapi_version="3.0.2",
    plugins=[FlaskPlugin(), MarshmallowPlugin()],
    # Custom extensions for Socket.IO events
    options={
        'extensions': {
            'x-socketio': {
                'events': {
                    'connect': {
                        'description': 'Client connected to Socket.IO',
                        'responses': {
                            '200': {
                                'description': 'Connection established'
                            }
                        }
                    },
                    'disconnect': {
                        'description': 'Client disconnected from Socket.IO',
                        'responses': {
                            '200': {
                                'description': 'Disconnection successful'
                            }
                        }
                    },
                    'reflow_status': {
                        'description': 'Emit the current status of the reflow process',
                        'responses': {
                            '200': {
                                'description': 'Reflow status emitted',
                                'content': {
                                    'application/json': {
                                        'schema': {
                                            "$ref": "#/components/schemas/ReflowStatus"
                                        }
                                    }
                                }
                            }
                        }
                    },
                    'oven_status': {
                        'description': 'Emit the current status of the oven',
                        'responses': {
                            '200': {
                                'description': 'Oven status emitted',
                                'content': {
                                    'application/json': {
                                        'schema': {
                                            "$ref": "#/components/schemas/OvenStatus"
                                        }
                                    }
                                }
                            }
                        }
                    },
                    'log_message': {
                        'description': 'Emit a log message',
                        'responses': {
                            '200': {
                                'description': 'Log message emitted',
                                'content': {
                                    'application/json': {
                                        'schema': {
                                            "$ref": "#/components/schemas/LogMessage"
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
    }
)

app = Flask(__name__)
socketio = SocketIO(app)

emit_queue = multiprocessing.Queue()

mpc = ModelPredictiveControl(
    # on_reflow_status=lambda status: socketio.emit('reflow_status', ReflowStatusSchema().dump(status))
    on_reflow_status=lambda status: emit_queue.put_nowait(('reflow_status', ReflowStatusSchema().dump(status))))
tms = ThermalManagementSystem(
    on_log_message=lambda message: emit_queue.put_nowait(('log_message', LogMessageSchema().dump(message))),
    on_oven_status=lambda status: emit_queue.put_nowait(('oven_status', OvenStatusSchema().dump(status))),
)

mpc.on_desired_duty_cycle = tms.set_duty_cycle
# Directory to store saved curves
SAVED_CURVES_DIR = "saved_curves"

# Ensure the directory for saved curves exists
if not os.path.exists(SAVED_CURVES_DIR):
    os.makedirs(SAVED_CURVES_DIR)

mpc.on_desired_oven_state = tms.set_oven_state


def handle_oven_status(status):
    mpc.temperature = status['temperature']
    mpc.door_open = status['door_open']
    print('oven status')
    emit_queue.put_nowait(('oven_status', OvenStatusSchema().dump(status)))
    if status['state'] == OvenState.FAULT:
        mpc.stop()


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
    return ControlStatusSchema().dump(mpc.status), 200


@app.route('/oven_status', methods=['GET'])
def oven_status():
    """
    Get the current status of the oven.
    ---
    get:
      description: Get the current status of the oven.
      responses:
        200:
          description: Current status of the oven.
          content:
            application/json:
              schema: OvenStatusSchema
    """
    return OvenStatusSchema().dump(tms.oven_status), 200


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


# Endpoint to save a curve
@app.route('/save_curve', methods=['POST'])
def save_curve():
    curve_data = request.get_json()
    try:
        curve = ReflowCurveSchema().load(curve_data)
        curve["id"] = str(uuid.uuid4())
        curve_path = os.path.join(SAVED_CURVES_DIR, f"{curve['id']}.json")
        with open(curve_path, 'w') as curve_file:
            json.dump(curve_data, curve_file)
        return curve['id'], 200
    except (ValidationError, ValueError) as err:
        return jsonify({'status': 'error', 'message': str(err)}), 400


# endpoint to update a curve. JSON curve in post body, with id in request args
@app.route('/update_curve/<string:curve_id>', methods=['POST'])
def update_curve(curve_id):
    # make sure curve_id is a valid uuid
    try:
        uuid.UUID(curve_id)
    except ValueError:
        return jsonify({'status': 'error', 'message': 'Curve not found.'}), 400

    # make sure curve_id exists in saved curves
    curve_path = os.path.join(SAVED_CURVES_DIR, f"{curve_id}.json")
    if not os.path.exists(curve_path):
        return jsonify({'status': 'error', 'message': 'Curve not found.'}), 400
    curve_data = request.get_json()
    try:
        curve = ReflowCurveSchema().load(curve_data)
        curve["id"] = curve_id
        with open(curve_path, 'w') as curve_file:
            json.dump(curve_data, curve_file)
        return jsonify({'status': 'success', 'message': 'Curve saved successfully.'}), 200
    except (ValidationError, ValueError) as err:
        return jsonify({'status': 'error', 'message': str(err)}), 400


# delete curve with uuid
@app.route('/delete_curve/<string:curve_id>', methods=['DELETE'])
def delete_curve(curve_id):
    # make sure curve_id exists in saved curves
    curve_path = os.path.join(SAVED_CURVES_DIR, f"{curve_id}.json")
    if not os.path.exists(curve_path):
        return jsonify({'status': 'error', 'message': 'Curve not found.'}), 400
    os.remove(curve_path)
    return jsonify({'status': 'success', 'message': 'Curve deleted successfully.'}), 200


# Endpoint to get all saved curves
@app.route('/curves', methods=['GET'])
def get_curves():
    curves = []
    for filename in os.listdir(SAVED_CURVES_DIR):
        if filename.endswith('.json'):
            with open(os.path.join(SAVED_CURVES_DIR, filename), 'r') as curve_file:
                curves.append(json.load(curve_file))
    return jsonify(curves), 200


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
    return LogMessagesSchema().dump({'logs': tms.log_messages}), 200


# To generate OpenAPI documentation
@app.route("/openapi.json")
def create_openapi_spec():
    print(json.dumps(spec.to_dict(), indent=2))
    return jsonify(spec.to_dict())


@socketio.on('connect')
def on_connect():
    print('Client connected')
    # # Send the current status of the reflow process
    # emit('reflow_status', ReflowStatusSchema().dump(mpc.status))
    # # Send the current status of the oven
    emit('oven_status', OvenStatusSchema().dump(tms.oven_status))
    # # Send the current logs
    # emit('log_message', LogMessagesSchema().dump({'logs': tms.log_messages}))


@socketio.on('disconnect')
def on_disconnect():
    # Clean up client subscriptions
    print('Client disconnected')


# background task to emit the current status of the reflow process from the queue
def emit_status():
    while True:
        try:
            to_emit = emit_queue.get_nowait()
            socketio.emit(to_emit[0], to_emit[1])
        except queue.Empty:
            pass
        socketio.sleep(0.1)


with app.test_request_context():
    # Register the schema with the spec
    spec.path(view=start_curve, app=app)
    spec.path(view=curve_status, app=app)
    spec.path(view=stop_curve, app=app)
    spec.path(view=reset_device, app=app)
    spec.path(view=get_logs, app=app)
    spec.path(view=oven_status, app=app)

if __name__ == "__main__":
    socketio.start_background_task(emit_status)
    try:
        socketio.run(app, host='0.0.0.0', port=5000)
    except KeyboardInterrupt:
        print('Exiting...')
        del tms
        del mpc
        socketio.stop()
