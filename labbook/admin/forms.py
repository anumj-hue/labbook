from datetime import time

from flask_wtf import FlaskForm
from wtforms import (
    BooleanField,
    DateField,
    EmailField,
    Form,
    FormField,
    IntegerField,
    SelectField,
    StringField,
    TextAreaField,
    TimeField,
    URLField,
)
from wtforms.validators import (
    DataRequired,
    Email,
    InputRequired,
    Length,
    NumberRange,
    Optional,
    ValidationError,
)

from ..models import WEEKDAY_LABELS, WEEKDAYS, Role, UnitStatus
from ..services.settings import RULES, normalise_domain
from ..timeutil import is_valid_timezone

ROLE_CHOICES = [(r.value, r.label) for r in Role]
UNIT_STATUS_CHOICES = [(s.value, s.label) for s in UnitStatus]


def _quarter_hour(_form, field):
    if field.data is not None and field.data.minute % 15:
        raise ValidationError("Use 15-minute steps (e.g. 08:00, 08:15).")


class UserInviteForm(FlaskForm):
    name = StringField("Full name", [DataRequired(), Length(min=2, max=120)])
    email = EmailField("Email", [DataRequired(), Email(), Length(max=254)])
    role = SelectField("Role", choices=ROLE_CHOICES, default=Role.STUDENT.value, coerce=Role)


class UserEditForm(FlaskForm):
    name = StringField("Full name", [DataRequired(), Length(min=2, max=120)])
    role = SelectField("Role", choices=ROLE_CHOICES, coerce=Role)


# --- Labs -----------------------------------------------------------------------------------------

class DayHoursForm(Form):
    open = BooleanField("Open")
    start = TimeField("Opens", [Optional(), _quarter_hour], format="%H:%M")
    end = TimeField("Closes", [Optional(), _quarter_hour], format="%H:%M")

    def validate(self, extra_validators=None):
        ok = super().validate(extra_validators)
        if not self.open.data:
            return ok
        if self.start.data is None:
            self.start.errors.append("Required")
            ok = False
        if self.end.data is None:
            self.end.errors.append("Required")
            ok = False
        if ok and self.start.data >= self.end.data:
            self.end.errors.append("Must be after opening time")
            ok = False
        return ok


class LabForm(FlaskForm):
    name = StringField("Name", [DataRequired(), Length(max=120)])
    location = StringField("Location", [Optional(), Length(max=200)], description="e.g. Block A, Room 204")
    timezone = StringField("Timezone", [DataRequired(), Length(max=64)], default="Asia/Kolkata")

    def validate_timezone(self, field):
        if not is_valid_timezone(field.data.strip()):
            raise ValidationError("Unknown timezone. Use an IANA name such as Asia/Kolkata.")


for _prefix in ("term", "vacation"):
    for _day in WEEKDAYS:
        setattr(LabForm, f"{_prefix}_{_day}", FormField(DayHoursForm, label=WEEKDAY_LABELS[_day]))


def hours_from_form(form: LabForm, prefix: str) -> dict[str, list[list[str]]]:
    hours = {}
    for day in WEEKDAYS:
        sub = getattr(form, f"{prefix}_{day}")
        if sub.open.data:
            hours[day] = [[sub.start.data.strftime("%H:%M"), sub.end.data.strftime("%H:%M")]]
    return hours


def hours_to_form_data(hours: dict) -> dict[str, dict]:
    data = {}
    for day in WEEKDAYS:
        intervals = hours.get(day) or []
        if intervals:
            data[day] = {"open": True, "start": time.fromisoformat(intervals[0][0]),
                         "end": time.fromisoformat(intervals[0][1])}
        else:
            data[day] = {"open": False}
    return data


# --- Academic calendar ----------------------------------------------------------------------------

class TermForm(FlaskForm):
    name = StringField("Name", [DataRequired(), Length(max=120)], description='e.g. "Odd Semester 2026"')
    start_date = DateField("Start date", [DataRequired()])
    end_date = DateField("End date", [DataRequired()])

    def validate_end_date(self, field):
        if self.start_date.data and field.data and field.data < self.start_date.data:
            raise ValidationError("End date must be on or after the start date.")


class HolidayForm(FlaskForm):
    lab_id = SelectField("Applies to", coerce=int)
    start_date = DateField("Start date", [DataRequired()])
    end_date = DateField("End date", [Optional()], description="Leave empty for a single day.")
    reason = StringField("Reason", [DataRequired(), Length(max=200)], description='Shown as "Closed — reason".')

    def validate_end_date(self, field):
        if self.start_date.data and field.data and field.data < self.start_date.data:
            raise ValidationError("End date must be on or after the start date.")


# --- Equipment ------------------------------------------------------------------------------------

class EquipmentTypeForm(FlaskForm):
    name = StringField("Name", [DataRequired(), Length(max=160)],
                       description='e.g. "Digital Oscilloscope DSO-X 2002A"')
    category = StringField("Category", [DataRequired(), Length(max=80)])
    description = TextAreaField("Description and usage notes", [Optional(), Length(max=20000)],
                                description="Markdown supported.")
    safety_notes = TextAreaField("Safety notes", [Optional(), Length(max=20000)], description="Markdown supported.")
    photo_url = URLField("Photo URL", [Optional(), Length(max=500)])
    requires_approval = BooleanField("Student bookings need teacher approval")
    purpose_required = BooleanField("Purpose is required when booking")
    max_duration_min = IntegerField(
        "Max duration (minutes)", [Optional(), NumberRange(15, 1440)],
        description="Leave empty to use the department default.",
    )
    student_advance_days = IntegerField(
        "Student advance booking window (days)", [Optional(), NumberRange(0, 365)],
        description="Leave empty to use the department default.",
    )

    def validate_max_duration_min(self, field):
        if field.data is not None and field.data % 15:
            raise ValidationError("Use a multiple of 15 minutes.")

    def validate_photo_url(self, field):
        if field.data and not field.data.lower().startswith(("https://", "http://")):
            raise ValidationError("Use an http(s) URL.")


class AccessoryForm(FlaskForm):
    name = StringField("Item", [DataRequired(), Length(max=120)])
    expected_qty = IntegerField("Qty", [InputRequired(), NumberRange(1, 99)], default=1)
    sort_order = IntegerField("Order", [Optional(), NumberRange(0, 999)], default=0)


class UnitForm(FlaskForm):
    type_id = SelectField("Equipment type", coerce=int)
    lab_id = SelectField("Lab", coerce=int)
    asset_tag = StringField("Asset tag", [DataRequired(), Length(max=40)], description="e.g. OSC-03")
    serial_no = StringField("Serial number", [Optional(), Length(max=80)])
    status = SelectField("Status", choices=UNIT_STATUS_CHOICES, coerce=UnitStatus, default=UnitStatus.AVAILABLE.value)
    notes = TextAreaField("Notes", [Optional(), Length(max=2000)])


# --- Settings -------------------------------------------------------------------------------------

class SettingsForm(FlaskForm):
    allowed_email_domains = TextAreaField(
        "Allowed email domains",
        description="One per line, e.g. rajagiritech.edu.in. Leave empty to allow any email address.",
    )

    def validate_allowed_email_domains(self, field):
        for domain in parse_domains(field.data):
            if "." not in domain or not all(part and part.replace("-", "").isalnum() for part in domain.split(".")):
                raise ValidationError(f'"{domain}" is not a valid domain.')


def parse_domains(text: str | None) -> list[str]:
    seen = []
    for line in (text or "").replace(",", "\n").splitlines():
        domain = normalise_domain(line)
        if domain and domain not in seen:
            seen.append(domain)
    return seen


def _multiple_of(step: int):
    def check(_form, field):
        if field.data is not None and field.data % step:
            raise ValidationError(f"Use a multiple of {step}.")
    return check


for _rule in RULES:
    validators = [InputRequired(), NumberRange(_rule.minimum, _rule.maximum)]
    if _rule.step > 1:
        validators.append(_multiple_of(_rule.step))
    setattr(SettingsForm, _rule.key, IntegerField(_rule.label, validators, description=_rule.unit))


class PrivacyNoticeForm(FlaskForm):
    body_md = TextAreaField("Notice (Markdown)", [DataRequired(), Length(max=50000)])
