"""
Display helpers for complaints.

These map a stored value onto a Bootstrap colour. They live here rather than on
the model because "resolved is green" is a fact about the page, not about the
complaint — the model should not have to know that Bootstrap exists, and a
redesign should not mean editing models.py.
"""

from django import template

from complaints.models import Complaint

register = template.Library()

# Colour per status. Grey while nobody has picked it up, blue once someone has,
# amber while work is happening, green when it is done, dark once it is filed
# away. Anything unrecognised falls back to grey rather than to no colour, so a
# new status added later still renders as a badge.
STATUS_COLOURS = {
    Complaint.Status.SUBMITTED: 'secondary',
    Complaint.Status.ASSIGNED: 'primary',
    Complaint.Status.IN_PROGRESS: 'warning',
    Complaint.Status.RESOLVED: 'success',
    Complaint.Status.CLOSED: 'dark',
}

PRIORITY_COLOURS = {
    Complaint.Priority.LOW: 'light',
    Complaint.Priority.NORMAL: 'info',
    Complaint.Priority.HIGH: 'danger',
}


@register.filter
def status_colour(status):
    """{{ complaint.status|status_colour }} -> 'success'"""
    return STATUS_COLOURS.get(status, 'secondary')


@register.filter
def priority_colour(priority):
    """{{ complaint.priority|priority_colour }} -> 'danger'"""
    return PRIORITY_COLOURS.get(priority, 'secondary')
