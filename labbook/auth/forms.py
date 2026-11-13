from flask_wtf import FlaskForm
from wtforms import BooleanField, EmailField, PasswordField, StringField
from wtforms.validators import DataRequired, Email, EqualTo, Length

from ..services.passwords import MIN_PASSWORD_LENGTH

_password = [
    DataRequired(),
    Length(min=MIN_PASSWORD_LENGTH, max=128, message=f"Use at least {MIN_PASSWORD_LENGTH} characters."),
]


def _email_field(label: str = "Email") -> EmailField:
    return EmailField(label, [DataRequired(), Email(message="Enter a valid email address."), Length(max=254)],
                      render_kw={"autocomplete": "email"})


class LoginForm(FlaskForm):
    email = _email_field()
    password = PasswordField("Password", [DataRequired()], render_kw={"autocomplete": "current-password"})


class RegisterForm(FlaskForm):
    name = StringField("Full name", [DataRequired(), Length(min=2, max=120)], render_kw={"autocomplete": "name"})
    email = _email_field()
    password = PasswordField("Password", _password, description=f"At least {MIN_PASSWORD_LENGTH} characters.",
                             render_kw={"autocomplete": "new-password"})
    confirm = PasswordField("Confirm password", [DataRequired(), EqualTo("password", "Passwords don't match.")],
                            render_kw={"autocomplete": "new-password"})
    consent = BooleanField("I have read the Privacy Notice",
                           [DataRequired(message="You need to accept the Privacy Notice to register.")])


class ForgotPasswordForm(FlaskForm):
    email = _email_field()


class SetPasswordForm(FlaskForm):
    password = PasswordField("New password", _password, description=f"At least {MIN_PASSWORD_LENGTH} characters.",
                             render_kw={"autocomplete": "new-password"})
    confirm = PasswordField("Confirm new password", [DataRequired(), EqualTo("password", "Passwords don't match.")],
                            render_kw={"autocomplete": "new-password"})


class ProfileNameForm(FlaskForm):
    name = StringField("Full name", [DataRequired(), Length(min=2, max=120)])


class ChangePasswordForm(SetPasswordForm):
    current_password = PasswordField("Current password", [DataRequired()],
                                     render_kw={"autocomplete": "current-password"})


class ChangeEmailForm(FlaskForm):
    new_email = _email_field("New email")
    current_password = PasswordField("Current password", [DataRequired()],
                                     render_kw={"autocomplete": "current-password"})


class EmailPrefsForm(FlaskForm):
    reminders = BooleanField("Booking reminders", description="An email 1 hour before each booking starts.")
    digest = BooleanField("Daily approvals digest", description="Teachers and Admins only. Sent at 07:00.")
    waitlist_updates = BooleanField("Waitlist updates", description="When an offer expires or an entry lapses.")


class ConsentForm(FlaskForm):
    consent = BooleanField("I have read the updated Privacy Notice",
                           [DataRequired(message="You need to accept the Privacy Notice to continue.")])
