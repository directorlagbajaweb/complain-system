"""
Context processors — values that every template gets without any view having
to pass them.

The navbar bell appears on every page, so its unread count cannot come from a
view: there are dozens of views and each one would have to remember. A context
processor runs on every render and adds the number to the template context.

The cost is one small COUNT query per page for signed-in users. That is the
right trade for a number that has to be correct on every page; if it ever shows
up in profiling, the fix is to cache it, not to start passing it by hand.
"""

from .notifications import unread_count_for


def notifications(request):
    # `request.user` is missing entirely on the few requests that bypass the
    # auth middleware, so ask for it defensively rather than assuming.
    user = getattr(request, 'user', None)
    if user is None:
        return {}
    return {'unread_notification_count': unread_count_for(user)}
