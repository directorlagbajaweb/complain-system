"""
Data model for the complaint management system.

The shape of the system in one sentence: a *student* files a *complaint* under a
*category*; the category decides which *unit* (office) is responsible; a
*handler* in that unit works it, talks to the student through *messages*, and
every status change is recorded in *status history*.
"""

from django.conf import settings
from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.core.exceptions import ValidationError
from django.db import IntegrityError, models, transaction
from django.utils import timezone


# ---------------------------------------------------------------------------
# Lookup tables
# ---------------------------------------------------------------------------


class AcademicDepartment(models.Model):
    """
    A student's course of study — Computer Science, Electrical Engineering,
    Business Administration.

    This is deliberately NOT the same thing as a Unit. An academic department
    is what a student *studies*; a unit is an office that *fixes things*. They
    happen to both be "departments" in everyday campus speech, which is exactly
    why they are two separate tables here. Merging them would mean a student
    from Computer Science could be routed a Bursary complaint, which is nonsense.
    """

    name = models.CharField(max_length=120, unique=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "academic department"
        verbose_name_plural = "academic departments"

    def __str__(self):
        return self.name


class Unit(models.Model):
    """
    An office that resolves complaints — Bursary, Student Affairs, ICT,
    Works & Maintenance, Exams & Records.

    Handlers belong to exactly one unit. A complaint reaches a unit indirectly:
    through the category the student picked.
    """

    name = models.CharField(max_length=120, unique=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Category(models.Model):
    """
    The thing the student is complaining about — "Hostel accommodation",
    "Student portal access".

    This model is the routing table. The student never picks a unit; they pick
    a category, and the category's `unit` decides who has to deal with it. That
    keeps the student's mental model simple ("my problem is about X") and lets
    the school re-route a whole class of complaints later by editing one row.
    """

    name = models.CharField(max_length=120)
    unit = models.ForeignKey(Unit, on_delete=models.PROTECT, related_name="categories")

    class Meta:
        ordering = ["unit__name", "name"]
        verbose_name_plural = "categories"
        constraints = [
            models.UniqueConstraint(
                fields=["unit", "name"], name="unique_category_name_per_unit"
            )
        ]

    def __str__(self):
        return f"{self.name} ({self.unit.name})"


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------


class UserManager(BaseUserManager):
    """
    Django's stock user manager insists on a username. Ours logs in by email,
    so we need our own `create_user` / `create_superuser`. This is also what
    `manage.py createsuperuser` calls, which is why it must exist before the
    first migration.
    """

    use_in_migrations = True

    def _create_user(self, email, password, **extra_fields):
        if not email:
            raise ValueError("Users must have an email address.")
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        extra_fields.setdefault("role", User.Role.STUDENT)
        return self._create_user(email, password, **extra_fields)

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("role", User.Role.ADMIN)

        if extra_fields.get("is_staff") is not True:
            raise ValueError("Superuser must have is_staff=True.")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_superuser=True.")
        return self._create_user(email, password, **extra_fields)


class User(AbstractUser):
    """
    One user table for all three kinds of person, separated by `role`.

    Why one table and not three? Because they all log in the same way, and
    Django only supports one authentication model. The role field decides what
    you can see; the nullable fields below decide what extra information we
    hold about you.

    Which extra fields apply to whom:
      student -> matric_no, academic_department
      handler -> unit
      admin   -> none of them

    Those fields are nullable at the database level because a handler genuinely
    has no matric number. `clean()` enforces the pairing, so the database stays
    permissive while the application stays strict.
    """

    class Role(models.TextChoices):
        STUDENT = "student", "Student"
        HANDLER = "handler", "Handler"
        ADMIN = "admin", "Administrator"

    # Removing the inherited username field: email is the login.
    username = None

    email = models.EmailField("email address", unique=True)
    full_name = models.CharField(max_length=150)
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.STUDENT)

    # Students only.
    matric_no = models.CharField(
        max_length=30,
        unique=True,
        null=True,
        blank=True,
        help_text="Students only.",
    )
    academic_department = models.ForeignKey(
        AcademicDepartment,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="students",
        help_text="Students only — the student's course of study.",
    )

    # Handlers only.
    unit = models.ForeignKey(
        Unit,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="handlers",
        help_text="Handlers only — the office this staff member works in.",
    )

    # These two lines are what actually make email the login credential.
    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["full_name"]

    objects = UserManager()

    class Meta:
        ordering = ["full_name"]

    def __str__(self):
        return f"{self.full_name} <{self.email}>"

    @property
    def is_student(self):
        return self.role == self.Role.STUDENT

    @property
    def is_handler(self):
        return self.role == self.Role.HANDLER

    @property
    def is_admin_role(self):
        # Deliberately not called `is_admin`: Django already uses `is_staff`
        # and `is_superuser` for admin-site access, which is a different idea.
        return self.role == self.Role.ADMIN

    def clean(self):
        """
        Enforce the role/field pairing described above.

        The database cannot express "matric_no is required, but only for
        students", so the rule lives here. Django's admin and any ModelForm
        call this automatically; `User.objects.create_user()` does not, which
        is why the columns stay nullable.
        """
        super().clean()
        errors = {}

        if self.is_student:
            if self.unit_id:
                errors["unit"] = "Students do not belong to a unit."
        elif self.is_handler:
            if not self.unit_id:
                errors["unit"] = "A handler must belong to a unit."
            if self.matric_no:
                errors["matric_no"] = "Only students have a matriculation number."
            if self.academic_department_id:
                errors["academic_department"] = (
                    "Only students have an academic department."
                )
        else:  # administrator
            if self.matric_no:
                errors["matric_no"] = "Only students have a matriculation number."
            if self.academic_department_id:
                errors["academic_department"] = (
                    "Only students have an academic department."
                )

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        # A blank form field hands us "" rather than None. Two students with ""
        # would collide on the unique index, so normalise empty to NULL — the
        # database allows any number of NULLs in a unique column.
        if not self.matric_no:
            self.matric_no = None
        return super().save(*args, **kwargs)


# ---------------------------------------------------------------------------
# Complaints
# ---------------------------------------------------------------------------

# How many times we will recompute a reference number if two complaints are
# submitted in the same instant and land on the same one.
_REFERENCE_RETRIES = 5


class Complaint(models.Model):
    """
    The centre of the system. Everything else points at this.

    `reference_no` is the human-facing handle — CMP-2026-0001 — the number a
    student quotes at the Bursary counter. It is generated once, on first save,
    and never changes. It is separate from the primary key on purpose: the pk
    is a database detail, the reference is a public identifier with a year in
    it that people read aloud.
    """

    class Status(models.TextChoices):
        SUBMITTED = "submitted", "Submitted"
        ASSIGNED = "assigned", "Assigned"
        IN_PROGRESS = "in_progress", "In progress"
        RESOLVED = "resolved", "Resolved"
        CLOSED = "closed", "Closed"

    class Priority(models.TextChoices):
        LOW = "low", "Low"
        NORMAL = "normal", "Normal"
        HIGH = "high", "High"

    reference_no = models.CharField(max_length=20, unique=True, editable=False)

    student = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="complaints",
    )
    category = models.ForeignKey(
        Category,
        on_delete=models.PROTECT,
        related_name="complaints",
    )
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_complaints",
        help_text="The handler working this complaint.",
    )

    subject = models.CharField(max_length=200)
    description = models.TextField()

    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.SUBMITTED
    )
    priority = models.CharField(
        max_length=20, choices=Priority.choices, default=Priority.NORMAL
    )

    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["-created_at"]),
        ]

    def __str__(self):
        return f"{self.reference_no} — {self.subject}"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Remember the status we were loaded with, so save() can tell whether
        # it changed and write a history row.
        self._original_status = self.status

    @property
    def unit(self):
        """The office responsible, derived from the category. Never stored
        twice — if a category is re-routed, existing complaints follow it."""
        return self.category.unit

    @staticmethod
    def _next_reference_no():
        """
        Build the next CMP-YYYY-NNNN for the current year.

        We look at the highest existing reference for this year and add one.
        The number is zero-padded to four digits so that sorting the text
        sorts the numbers correctly (0002 comes after 0001). That holds up to
        9999 complaints in a single year, which is far beyond what this school
        will file.
        """
        prefix = f"CMP-{timezone.now().year}-"
        last = (
            Complaint.objects.filter(reference_no__startswith=prefix)
            .order_by("-reference_no")
            .values_list("reference_no", flat=True)
            .first()
        )
        next_number = int(last.rsplit("-", 1)[1]) + 1 if last else 1
        return f"{prefix}{next_number:04d}"

    def _sync_resolved_at(self):
        """Keep the resolution timestamp honest without anyone having to
        remember to set it."""
        if self.status == self.Status.RESOLVED and self.resolved_at is None:
            self.resolved_at = timezone.now()
        elif self.status not in (self.Status.RESOLVED, self.Status.CLOSED):
            # Reopened. It is no longer resolved, so the timestamp must go.
            # Closed is left alone: it keeps the date it was resolved on.
            self.resolved_at = None

    def save(self, *args, **kwargs):
        """
        Three jobs happen here so that no caller can forget them:

        1. Give a brand-new complaint its reference number.
        2. Keep `resolved_at` in step with `status`.
        3. Write a StatusHistory row whenever the status changed.

        For (3) the caller can set `complaint._changed_by = request.user`
        before saving to record who did it; if nobody does, the history row is
        still written with an empty `changed_by` rather than being lost.
        """
        creating = self._state.adding
        old_status = None if creating else self._original_status

        self._sync_resolved_at()

        for attempt in range(_REFERENCE_RETRIES):
            if creating and not self.reference_no:
                self.reference_no = self._next_reference_no()
            try:
                # The complaint and its first history row are written together
                # or not at all — no complaint should exist without a trail.
                with transaction.atomic():
                    super().save(*args, **kwargs)
                    if creating or old_status != self.status:
                        StatusHistory.objects.create(
                            complaint=self,
                            changed_by=getattr(self, "_changed_by", None),
                            old_status=old_status,
                            new_status=self.status,
                        )
                break
            except IntegrityError:
                # Two complaints submitted in the same instant can compute the
                # same reference number; the unique index rejects the second.
                # Clear it and go round again for a fresh one.
                if not creating or attempt == _REFERENCE_RETRIES - 1:
                    raise
                self.reference_no = ""

        self._original_status = self.status


class Message(models.Model):
    """
    The conversation on a complaint.

    `is_internal` is the important field. An internal message is staff talking
    among themselves — "this student has complained about this twice already",
    "escalate to the Registrar". Students must NEVER see these. The flag lives
    here rather than in a separate table so the thread stays in one place and
    in one chronological order; the filtering is the application's job.
    """

    complaint = models.ForeignKey(
        Complaint, on_delete=models.CASCADE, related_name="messages"
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="messages",
    )
    body = models.TextField()
    is_internal = models.BooleanField(
        default=False,
        help_text="Staff-only note. Never shown to the student.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
        indexes = [models.Index(fields=["complaint", "created_at"])]

    def __str__(self):
        kind = "internal note" if self.is_internal else "message"
        return f"{kind} on {self.complaint.reference_no}"


class Attachment(models.Model):
    """
    Evidence: a photo of a broken hostel window, a scan of a payment receipt.

    Files are written under MEDIA_ROOT in year/month folders so one directory
    never fills up with thousands of files.
    """

    complaint = models.ForeignKey(
        Complaint, on_delete=models.CASCADE, related_name="attachments"
    )
    file = models.FileField(upload_to="attachments/%Y/%m/")
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-uploaded_at"]

    def __str__(self):
        return self.filename

    @property
    def filename(self):
        return self.file.name.rsplit("/", 1)[-1]


class StatusHistory(models.Model):
    """
    The audit trail. One row per status change, written automatically by
    `Complaint.save()`.

    `old_status` is nullable because the very first row — the one written when
    the complaint is created — has nothing before it. That row is what lets the
    timeline start with "Submitted" rather than appearing out of nowhere.

    This exists so nobody can quietly mark a complaint Resolved and deny it
    later. It is append-only by convention; nothing here ever updates a row.
    """

    complaint = models.ForeignKey(
        Complaint, on_delete=models.CASCADE, related_name="status_history"
    )
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="status_changes",
    )
    old_status = models.CharField(
        max_length=20, choices=Complaint.Status.choices, null=True, blank=True
    )
    new_status = models.CharField(max_length=20, choices=Complaint.Status.choices)
    changed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["changed_at"]
        verbose_name = "status history entry"
        verbose_name_plural = "status history"

    def __str__(self):
        origin = self.old_status or "new"
        return f"{self.complaint.reference_no}: {origin} -> {self.new_status}"


class Notification(models.Model):
    """
    The bell icon. A short line of text pointing at a complaint, plus whether
    the user has looked at it.

    The text is stored rather than generated on the fly so that a notification
    still reads correctly months later, even if the complaint has moved on
    since. "Your complaint CMP-2026-0007 was assigned to ICT" stays true as a
    record of what happened, which a live-generated message would not.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notifications",
    )
    complaint = models.ForeignKey(
        Complaint, on_delete=models.CASCADE, related_name="notifications"
    )
    body = models.CharField(max_length=255)
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["user", "is_read"])]

    def __str__(self):
        return f"To {self.user.full_name}: {self.body}"
