"""
Who is allowed to see what.

Two things live here, and they are related:

  * `home_url_for(user)` — the one place that knows which page a role belongs on.
  * `role_required(...)` — the decorator that keeps everybody else out.

Django ships a permission framework (permissions, groups, `@permission_required`)
that could do this. We are not using it, on purpose. That framework is built for
fine-grained per-object rights — "may this user change this particular row" —
and it stores the answer in database tables you have to remember to populate.
Our rule is much blunter than that: a person is one of three things, and each
thing gets one part of the site. Written as the twenty lines below, the rule is
readable in full, in one sitting, by anyone marking this project. Spread across
permission rows in a database, it would not be.
"""

from functools import wraps

from django.contrib import messages
from django.contrib.auth.views import redirect_to_login
from django.shortcuts import redirect
from django.urls import reverse

from .models import User

# The role → landing page map. This is the single source of truth for "where
# does this kind of user belong", used both when someone logs in and when
# someone is bounced out of a page they should not be on.
ROLE_HOME = {
    User.Role.STUDENT: 'student_home',      # /complaints/
    User.Role.HANDLER: 'handler_queue',     # /queue/
    User.Role.ADMIN: 'admin_dashboard',     # /dashboard/
}


def home_url_for(user):
    """
    The URL this user's own dashboard lives at.

    Falls back to the student page for any role we do not recognise. That case
    should be impossible — `role` is a choices field — but a login view that
    crashes on unexpected data is worse than one that shows the least
    privileged page.
    """
    return reverse(ROLE_HOME.get(user.role, 'student_home'))


def role_required(*allowed_roles):
    """
    Restrict a view to particular roles.

        @role_required(User.Role.HANDLER, User.Role.ADMIN)
        def queue(request):
            ...

    Three cases, checked in this order:

    1. Not signed in  -> send them to the login page, remembering where they
       were headed so they arrive there after signing in.
    2. Signed in, wrong role -> tell them, and put them back on their own
       dashboard. We redirect rather than showing a bare 403 because this is
       nearly always a person clicking a stale bookmark, not an attack, and a
       dead end with no way out is a bad answer to an honest mistake.
    3. Signed in, right role -> run the view.

    Note what case 2 does *not* do: it does not render the page and hide parts
    of it. The check happens before the view function is ever called, so a
    student cannot reach the handler queue by guessing its URL, and hiding the
    link in the navbar is only tidiness — this is the thing actually stopping
    them.
    """

    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            user = request.user

            if not user.is_authenticated:
                # `get_full_path()` keeps the query string, so ?page=2 survives
                # the round trip through the login form.
                return redirect_to_login(request.get_full_path())

            if user.role not in allowed_roles:
                messages.error(
                    request,
                    "You do not have access to that page.",
                )
                return redirect(home_url_for(user))

            return view_func(request, *args, **kwargs)

        return wrapper

    return decorator
