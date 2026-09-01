"""
Creating notifications.

One function, called by hand from the views that should cause a notification.

Django offers signals for this — `post_save` on Complaint could fire a
notification without any view knowing about it. We are deliberately not doing
that. A signal makes the notification appear from nowhere: you read the view
that files a complaint, see no mention of notifications, and have to already
know that signals exist to find out why the bell lit up. Calling `notify()` on
the line after the complaint is saved means the cause sits next to the effect,
and anyone reading the view can point at the line that did it.

The cost of being explicit is that a new view which should notify somebody can
forget to. That is a real trade-off, and the reason it is worth paying here is
that this code has to be read and explained more often than it has to be
extended.
"""

from .models import Notification


def notify(user, complaint, body):
    """
    Record one notification for `user` about `complaint`.

    `body` is stored as finished text rather than being assembled later from
    the complaint's current state. That is on purpose: a notification is a
    record of something that happened at a moment in time. "Your complaint
    CMP-2026-0007 has been received" should still read correctly in March, when
    the complaint has long since been resolved and closed.
    """
    return Notification.objects.create(
        user=user,
        complaint=complaint,
        body=body,
    )


def unread_count_for(user):
    """How many unread notifications this user has. Used by the navbar bell."""
    if not user.is_authenticated:
        return 0
    return Notification.objects.filter(user=user, is_read=False).count()
