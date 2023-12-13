from marshmallow import Schema, fields, validates, ValidationError, validates_schema
from constants import ControlState, LogSeverity, OvenState


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
    actual_temperatures = fields.Nested(ReflowCurveSchema, required=True,
                                        metadata={'description': "Array of points defining the actual curve so far"})
    state = fields.Enum(ControlState, required=True,
                        metadata={'description': "Current state of the reflow process"})


class ControlStatusSchema(Schema):
    curve = fields.Nested(ReflowCurveSchema, required=True, metadata={'description': "The curve data"})
    reflow = fields.Nested(ReflowStatusSchema, required=True, metadata={'description': "The control data"})


class OvenStatusSchema(Schema):
    time = fields.Int(required=True, metadata={'description': "Time in milliseconds since startup"})
    temperature = fields.Float(required=True, metadata={'description': "The current temperature of the oven"})
    state = fields.Enum(OvenState, required=True, metadata={'description': "The current state of the oven"})
    duty_cycle = fields.Int(required=True, metadata={'description': "The current duty cycle of the oven"})
    door_open = fields.Boolean(required=True, metadata={'description': "Whether the oven door is open or not"})
    errors = fields.List(fields.String, metadata={'description': "Array of errors if state is FAULT"})

    @validates('duty_cycle')
    def validate_duty_cycle(self, value):
        if value < 0 or value > 100:
            raise ValidationError('Duty cycle must be between 0 and 100')


class LogMessageSchema(Schema):
    message = fields.String(required=True, metadata={'description': "The log message"})
    severity = fields.Enum(LogSeverity, required=True, metadata={'description': "Severity of the log message"})
    time = fields.Integer(required=True,
                          metadata={'description': "Time of the log message in milliseconds since startup"})


class LogMessagesSchema(Schema):
    logs = fields.List(fields.Nested(LogMessageSchema), required=True,
                       metadata={'description': "Array of log messages"})
