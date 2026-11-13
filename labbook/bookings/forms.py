from datetime import datetime, time
from zoneinfo import ZoneInfo

from flask_wtf import FlaskForm
from wtforms import DateField, HiddenField, SelectField, TextAreaField
from wtforms.validators import DataRequired, Length, Optional

from ..rules import SLOT_MINUTES

_TIMES = [f"{m // 60:02d}:{m % 60:02d}" for m in range(0, 24 * 60, SLOT_MINUTES)]
START_CHOICES = [("", "--:--")] + [(t, t) for t in _TIMES]
END_CHOICES = [("", "--:--")] + [(t, t) for t in _TIMES[1:]]


class BookingForm(FlaskForm):
    type_id = HiddenField()
    booking_id = HiddenField()
    date = DateField("Date", [DataRequired(message="Pick a date.")])
    start = SelectField("Start", choices=START_CHOICES, validators=[DataRequired(message="Pick a start time.")])
    end = SelectField("End", choices=END_CHOICES, validators=[DataRequired(message="Pick an end time.")])
    unit_id = SelectField("Unit", coerce=int, default=0, validators=[Optional()])
    purpose = TextAreaField("Purpose or notes", [Optional(), Length(max=500)], render_kw={"rows": 2})

    def times(self, tz: ZoneInfo) -> tuple[datetime, datetime] | None:
        """Aware start/end datetimes in the lab timezone, or None if incomplete."""
        if not (self.date.data and self.start.data and self.end.data):
            return None
        start = datetime.combine(self.date.data, time.fromisoformat(self.start.data), tz)
        return start, datetime.combine(self.date.data, time.fromisoformat(self.end.data), tz)


class CancelForm(FlaskForm):
    reason = TextAreaField("Reason (optional)", [Optional(), Length(max=500)])
