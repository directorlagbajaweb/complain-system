"""
Admin registration.

Phase 1 has no views of its own, so the Django admin *is* the interface. It is
worth setting up properly: with these list displays and filters a staff member
can already run the whole system from /admin/.
"""

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.forms import AdminPasswordChangeForm
from django.utils.html import format_html

from .models import (
    AcademicDepartment,
    Attachment,
    Category,
    Complaint,
    Message,
    Notification,
    StatusHistory,
    Unit,
    User,
)


# ---------------------------------------------------------------------------
# Lookups
# ---------------------------------------------------------------------------


@admin.register(AcademicDepartment)
class AcademicDepartmentAdmin(admin.ModelAdmin):
    list_display = ["name", "student_count"]
    search_fields = ["name"]

    @admin.display(description="students")
    def student_count(self, obj):
        return obj.students.count()


@admin.register(Unit)
class UnitAdmin(admin.ModelAdmin):
    list_display = ["name", "category_count", "handler_count"]
    search_fields = ["name"]

    @admin.display(description="categories")
    def category_count(self, obj):
        return obj.categories.count()

    @admin.display(description="handlers")
    def handler_count(self, obj):
        return obj.handlers.count()


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ["name", "unit", "complaint_count"]
    list_filter = ["unit"]
    search_fields = ["name"]
    list_select_related = ["unit"]

    @admin.display(description="complaints")
    def complaint_count(self, obj):
        return obj.complaints.count()


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    """
    Django's stock UserAdmin is built around a `username` field we deleted, so
    every fieldset has to be redeclared. Nothing clever here — it is just
    listing our fields instead of the default ones.
    """

    change_password_form = AdminPasswordChangeForm

    list_display = ["email", "full_name", "role", "matric_no", "academic_department", "unit", "is_active"]
    list_filter = ["role", "is_active", "is_staff", "unit", "academic_department"]
    search_fields = ["email", "full_name", "matric_no"]
    ordering = ["full_name"]
    list_select_related = ["academic_department", "unit"]

    fieldsets = [
        (None, {"fields": ["email", "password"]}),
        ("Personal", {"fields": ["full_name", "role"]}),
        (
            "Student details",
            {
                "fields": ["matric_no", "academic_department"],
                "description": "Students only. Leave blank for staff.",
            },
        ),
        (
            "Handler details",
            {
                "fields": ["unit"],
                "description": "Handlers only. The office this person works in.",
            },
        ),
        (
            "Permissions",
            {
                "fields": ["is_active", "is_staff", "is_superuser", "groups", "user_permissions"],
                "classes": ["collapse"],
            },
        ),
        ("Dates", {"fields": ["last_login", "date_joined"], "classes": ["collapse"]}),
    ]

    # The "add user" screen: only what is needed to create the account.
    add_fieldsets = [
        (
            None,
            {
                "classes": ["wide"],
                "fields": ["email", "full_name", "role", "password1", "password2"],
            },
        ),
    ]


# ---------------------------------------------------------------------------
# Complaints
# ---------------------------------------------------------------------------


class MessageInline(admin.TabularInline):
    model = Message
    extra = 0
    fields = ["author", "body", "is_internal", "created_at"]
    readonly_fields = ["created_at"]


class AttachmentInline(admin.TabularInline):
    model = Attachment
    extra = 0
    fields = ["file", "uploaded_at"]
    readonly_fields = ["uploaded_at"]


class StatusHistoryInline(admin.TabularInline):
    """Read-only: the audit trail is written by Complaint.save(), never by hand."""

    model = StatusHistory
    extra = 0
    can_delete = False
    fields = ["old_status", "new_status", "changed_by", "changed_at"]
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Complaint)
class ComplaintAdmin(admin.ModelAdmin):
    list_display = [
        "reference_no",
        "subject",
        "student",
        "category",
        "routed_unit",
        "status_label",
        "priority",
        "assigned_to",
        "created_at",
    ]
    list_filter = [
        "status",
        "priority",
        "category__unit",
        "category",
        "created_at",
    ]
    search_fields = ["reference_no", "subject", "description", "student__full_name", "student__matric_no"]
    date_hierarchy = "created_at"
    readonly_fields = ["reference_no", "created_at", "resolved_at"]
    autocomplete_fields = ["student", "assigned_to", "category"]
    list_select_related = ["student", "category", "category__unit", "assigned_to"]
    inlines = [MessageInline, AttachmentInline, StatusHistoryInline]

    fieldsets = [
        (None, {"fields": ["reference_no", "student", "category", "subject", "description"]}),
        ("Handling", {"fields": ["status", "priority", "assigned_to"]}),
        ("Dates", {"fields": ["created_at", "resolved_at"]}),
    ]

    @admin.display(description="unit", ordering="category__unit__name")
    def routed_unit(self, obj):
        return obj.category.unit

    @admin.display(description="status", ordering="status")
    def status_label(self, obj):
        colours = {
            Complaint.Status.SUBMITTED: "#6b7280",
            Complaint.Status.ASSIGNED: "#2563eb",
            Complaint.Status.IN_PROGRESS: "#d97706",
            Complaint.Status.RESOLVED: "#059669",
            Complaint.Status.CLOSED: "#374151",
        }
        return format_html(
            '<b style="color:{}">{}</b>',
            colours.get(obj.status, "#000"),
            obj.get_status_display(),
        )

    def save_model(self, request, obj, form, change):
        # Tell the model who is making this change, so the StatusHistory row it
        # writes records a person rather than an anonymous entry.
        obj._changed_by = request.user
        super().save_model(request, obj, form, change)

    def save_formset(self, request, form, formset, change):
        # New messages typed in the inline get the logged-in staff member as
        # their author automatically.
        if formset.model is Message:
            instances = formset.save(commit=False)
            for instance in instances:
                if instance.author_id is None:
                    instance.author = request.user
                instance.save()
            for obj in formset.deleted_objects:
                obj.delete()
            formset.save_m2m()
        else:
            super().save_formset(request, form, formset, change)


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = ["complaint", "author", "short_body", "is_internal", "created_at"]
    list_filter = ["is_internal", "created_at"]
    search_fields = ["body", "complaint__reference_no"]
    list_select_related = ["complaint", "author"]
    autocomplete_fields = ["complaint", "author"]

    @admin.display(description="body")
    def short_body(self, obj):
        return obj.body[:70] + ("…" if len(obj.body) > 70 else "")


@admin.register(Attachment)
class AttachmentAdmin(admin.ModelAdmin):
    list_display = ["filename", "complaint", "uploaded_at"]
    list_filter = ["uploaded_at"]
    search_fields = ["complaint__reference_no"]
    list_select_related = ["complaint"]
    autocomplete_fields = ["complaint"]


@admin.register(StatusHistory)
class StatusHistoryAdmin(admin.ModelAdmin):
    """Deliberately read-only. An audit trail you can edit is not an audit trail."""

    list_display = ["complaint", "old_status", "new_status", "changed_by", "changed_at"]
    list_filter = ["new_status", "changed_at"]
    search_fields = ["complaint__reference_no"]
    list_select_related = ["complaint", "changed_by"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ["user", "body", "complaint", "is_read", "created_at"]
    list_filter = ["is_read", "created_at"]
    search_fields = ["body", "user__full_name", "user__email", "complaint__reference_no"]
    list_select_related = ["user", "complaint"]
    autocomplete_fields = ["user", "complaint"]
