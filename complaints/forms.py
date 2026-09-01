"""
Forms for signing in and signing up.

Both forms give their widgets Bootstrap's `form-control` / `form-select`
classes in `__init__` rather than in the template. Doing it here means the
templates can stay as `{{ field }}` and no one has to hand-write an <input>
tag — which is how a required field or a validation error quietly gets lost.
"""

from django import forms
from django.contrib.auth.forms import AuthenticationForm, BaseUserCreationForm

from .models import AcademicDepartment, Complaint, Message, Unit, User


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


# ---------------------------------------------------------------------------
# Filing and discussing a complaint
# ---------------------------------------------------------------------------


def _category_choices_grouped_by_unit():
    """
    Build the category dropdown as <optgroup>s, one per responsible office.

    The student picks *what their problem is about*; the category's unit then
    decides who has to deal with it. Grouping the list under the office name
    makes that visible — "Hostel accommodation" sitting under "Student Affairs"
    tells the student where their complaint is going without them having to
    choose an office themselves, which they would often get wrong.

    Django's ModelChoiceField cannot do optgroups on its own, but a Select
    widget renders any (group_label, [(value, label), ...]) structure it is
    handed. Validation is unaffected: ModelChoiceField checks the submitted
    value against its *queryset*, not against these choices, so a student
    cannot smuggle in a category by editing the HTML.
    """
    groups = []
    for unit in Unit.objects.prefetch_related('categories').order_by('name'):
        options = [(category.pk, category.name) for category in unit.categories.all()]
        if options:
            groups.append((unit.name, options))
    return [('', 'Choose the subject of your complaint')] + groups


class ComplaintForm(forms.ModelForm):
    """
    The student's submission form.

    Note which fields are absent: `student`, `status`, `assigned_to` and
    `reference_no`. A student says what happened and how urgent it is; they do
    not get to say whose complaint it is, who will handle it, or that it is
    already resolved. Leaving those off the form means no posted value for them
    is ever read — the view sets `student` from `request.user`, and the rest
    keep their model defaults.
    """

    # Not a field on Complaint — attachments are their own table, because one
    # complaint may eventually carry several files. The view creates the
    # Attachment row from whatever arrives here.
    attachment = forms.FileField(
        required=False,
        label="Attachment (optional)",
        help_text="A photo or scan that supports your complaint — a receipt, "
                  "a screenshot, a picture of the fault.",
    )

    class Meta:
        model = Complaint
        fields = ['category', 'subject', 'description', 'priority']
        labels = {
            'category': "What is this about?",
            'subject': "Subject",
            'description': "What happened?",
            'priority': "How urgent is it?",
        }
        widgets = {
            'subject': forms.TextInput(
                attrs={'placeholder': "One line summarising the problem"}
            ),
            'description': forms.Textarea(
                attrs={
                    'rows': 6,
                    'placeholder': "Give the full story: what happened, when, "
                                   "and anything already tried.",
                }
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['category'].choices = _category_choices_grouped_by_unit()
        # The model defaults priority to Normal, so the dropdown should not
        # offer an empty option that means the same thing.
        self.fields['priority'].initial = Complaint.Priority.NORMAL
        _style(self)


class MessageForm(forms.ModelForm):
    """
    The reply box on the complaint detail page.

    `is_internal` is deliberately not a field here. Internal notes are staff
    talking among themselves, and this form is only ever shown to a student —
    if the flag were on the form, a student could post one and it would be
    hidden from them but visible to staff, which is exactly backwards. The view
    writes `is_internal=False` itself.
    """

    class Meta:
        model = Message
        fields = ['body']
        labels = {'body': "Add a reply"}
        widgets = {
            'body': forms.Textarea(
                attrs={'rows': 3, 'placeholder': "Write your reply…"}
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['body'].label = "Add a reply"
        _style(self)


class HandlerMessageForm(forms.ModelForm):
    """
    The staff reply box: the same message body, plus the internal-note switch.

    This is a separate form from `MessageForm` rather than the same one with
    the checkbox conditionally added. Two forms means the student's form has no
    `is_internal` field to post to at all — the capability is absent rather
    than merely hidden, and the two audiences cannot be confused by a future
    edit that forgets which case it is in.
    """

    class Meta:
        model = Message
        fields = ['body', 'is_internal']
        labels = {
            'body': "Message",
            'is_internal': "Internal note — staff only, hidden from the student",
        }
        help_texts = {'is_internal': None}
        widgets = {
            'body': forms.Textarea(
                attrs={'rows': 3, 'placeholder': "Write a reply or a note…"}
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # An unticked checkbox sends nothing at all, which Django reads as
        # False — so a public reply is what you get by default, and staff have
        # to deliberately tick the box to write something the student cannot
        # see. The safer of the two options is the one that happens by accident.
        self.fields['is_internal'].required = False
        _style(self)
