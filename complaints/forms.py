"""
Forms for signing in and signing up.

Both forms give their widgets Bootstrap's `form-control` / `form-select`
classes in `__init__` rather than in the template. Doing it here means the
templates can stay as `{{ field }}` and no one has to hand-write an <input>
tag — which is how a required field or a validation error quietly gets lost.
"""

from django import forms
from django.contrib.auth.forms import AuthenticationForm, BaseUserCreationForm

from .models import AcademicDepartment, User


def _style(form):
    """Give every widget the right Bootstrap class for its kind."""
    for field in form.fields.values():
        widget = field.widget
        if isinstance(widget, forms.Select):
            widget.attrs.setdefault('class', 'form-select')
        elif isinstance(widget, forms.CheckboxInput):
            widget.attrs.setdefault('class', 'form-check-input')
        else:
            widget.attrs.setdefault('class', 'form-control')


class EmailLoginForm(AuthenticationForm):
    """
    Django's `AuthenticationForm` with the username box relabelled.

    The field is still *called* `username` — that name is wired into
    `AuthenticationForm.clean()`, which passes it to `authenticate()`. Renaming
    the field would mean rewriting that method for no gain. What matters is
    that the model's `USERNAME_FIELD` is `email`, so the value typed here is
    looked up against the email column. The label and input type below just
    make the form say so.
    """

    username = forms.EmailField(
        label="Email address",
        widget=forms.EmailInput(attrs={'autofocus': True, 'autocomplete': 'email'}),
    )

    error_messages = {
        **AuthenticationForm.error_messages,
        'invalid_login': "No account matches that email and password.",
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _style(self)


class StudentSignupForm(BaseUserCreationForm):
    """
    Student self-registration.

    The security point of this form is what is *not* on it: `role`. A form can
    only ever write the fields listed in `Meta.fields`, so even if someone
    posts `role=admin` by hand, Django's ModelForm machinery never looks at it.
    On top of that, `__init__` pins the role to student on the instance before
    any validation runs, so the value is fixed before the form has had a chance
    to touch anything. Handler and administrator accounts exist only because a
    superuser created them in /admin/.

    We extend `BaseUserCreationForm` (not `UserCreationForm`, which still has
    username-specific code in it) to inherit the password1/password2 pair. The
    second box is a confirmation, not a second piece of data — it also brings
    in Django's `AUTH_PASSWORD_VALIDATORS`, so short and common passwords are
    rejected here for free.
    """

    class Meta(BaseUserCreationForm.Meta):
        model = User
        fields = ['full_name', 'matric_no', 'academic_department', 'email']
        labels = {
            'full_name': "Full name",
            'matric_no': "Matriculation number",
            'academic_department': "Academic department",
            'email': "Email address",
        }
        help_texts = {
            # The model's help text says "Students only", which is true of the
            # database column but pointless on a form only students can use.
            'matric_no': None,
            'academic_department': None,
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Fix the role before validation. See the class docstring.
        self.instance.role = User.Role.STUDENT

        # These two columns are nullable in the database because handlers and
        # administrators genuinely have no matric number and no course of
        # study. For a student signing up they are both required, so the form
        # tightens what the model has to leave loose.
        self.fields['matric_no'].required = True
        self.fields['academic_department'].required = True
        self.fields['academic_department'].queryset = (
            AcademicDepartment.objects.all()
        )
        self.fields['academic_department'].empty_label = "Choose your department"

        _style(self)

    def clean_email(self):
        # Store emails lowercased so "Ada@school.edu" and "ada@school.edu"
        # cannot become two accounts that both look correct to a human.
        return self.cleaned_data['email'].lower()

    def save(self, commit=True):
        user = super().save(commit=False)
        # Belt and braces: the role is already set in __init__, but stating it
        # again at the moment of writing means a future edit to this form
        # cannot accidentally create a non-student.
        user.role = User.Role.STUDENT
        if commit:
            user.save()
        return user
