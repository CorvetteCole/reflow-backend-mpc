from flask import Flask, request, jsonify
from apispec import APISpec
from apispec.ext.marshmallow import MarshmallowPlugin
from apispec_webframeworks.flask import FlaskPlugin
from marshmallow import Schema, fields, validates, ValidationError

# Create an APISpec
spec = APISpec(
    title="Thermal Curve API",
    version="1.0.0",
    openapi_version="3.0.2",
    plugins=[FlaskPlugin(), MarshmallowPlugin()],
)


# Define a schema for the request
class ReflowCurveSchema(Schema):
    name = fields.String(required=True, description="Name of the curve")
    description = fields.String(required=True, description="Description of the curve")
    curve = fields.List(fields.List(fields.Float), required=True,
                        description="Array of [time, temperature] points defining the curve")

    @validates('reflow_curve')
    def validate_reflow_curve(self, value):
        # Check that the array has at least one point
        if not value:
            raise ValidationError('At least one [time, temperature] point is required.')

        # Separate the times and temperatures
        times, temperatures = zip(*value)

        # Validate sequence is strictly increasing
        if not all(x < y for x, y in zip(times, times[1:])):
            raise ValidationError('Times must be in ascending order.')

        if not all(x < y for x, y in zip(temperatures, temperatures[1:])):
            raise ValidationError('Temperatures must be in ascending order.')


app = Flask(__name__)


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
    errors = ReflowCurveSchema().validate(curve_data)
    if errors:
        return jsonify({'status': 'error', 'message': errors}), 400

    return jsonify({'status': 'success', 'message': 'Curve process started.'}), 200


# To generate OpenAPI documentation
@app.route("/openapi.json")
def create_openapi_spec():
    return jsonify(spec.to_dict())


with app.test_request_context():
    # Register the schema with the spec
    spec.path(view=start_curve, app=app)

if __name__ == "__main__":
    app.run(debug=True)
