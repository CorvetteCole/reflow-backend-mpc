from marshmallow import Schema, fields, validates, ValidationError, validates_schema
from constants import ControlState, LogSeverity

# Define a schema for the request
class ReflowCurveSchema(Schema):
    name = fields.String(metadata={'description': "Name of the curve"})
    description = fields.String(metadata={'description': "Description of the curve"})
    times = fields.List(fields.Int, required=True, metadata={'description': "Array of times in seconds"})
    temperatures = fields.List(fields.Float, required=True,
                               metadata={'description': "Array of temperatures in degrees Celsius"})

    @validates('times')
    def validate_times(self, value):
        # they must be in ascending order
        if value != sorted(value):
            raise ValidationError('Times must be in ascending order')

    @validates_schema
    def validate_lengths(self, data, **kwargs):
        # Check that times and temperatures are the same length
        if 'times' in data and 'temperatures' in data:
            if len(data['times']) != len(data['temperatures']):
                raise ValidationError('Times and temperatures must be the same length')


class ReflowStatusSchema(Schema):
    curve = fields.Nested(ReflowCurveSchema, required=True, metadata={'description': "The curve data"})
    running = fields.Boolean(required=True, metadata={'description': "Is the curve process running"})
    control_state = fields.Enum(ControlState, required=True,
                                metadata={'description': "Current state of the curve process"})
    progress = fields.Float(required=True, metadata={'description': "Progress of the curve 0-100%"})
    actual_temperatures = fields.Nested(ReflowCurveSchema, required=True,
                                        metadata={'description': "Array of points defining the actual curve so far"})


class LogMessageSchema(Schema):
    message = fields.String(required=True, metadata={'description': "The log message"})
    severity = fields.Field(required=True, metadata={'description': "Severity of the log message"})
    time = fields.Integer(required=True,
                          metadata={'description': "Time of the log message in milliseconds since startup"})


class LogMessagesSchema(Schema):
    logs = fields.List(fields.Nested(LogMessageSchema), required=True,
                       metadata={'description': "Array of log messages"})