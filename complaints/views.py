"""
Phase 2 views: getting in, getting out, and the three empty rooms behind the
door. The rooms are furnished in later phases.
"""

from django.contrib import messages
from django.contrib.auth import login, logout
from django.shortcuts import redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from .access import home_url_for, role_required
from .forms import EmailLoginForm, StudentSignupForm
from .models import User


# ---------------------------------------------------------------------------
# Getting in and out
# ---------------------------------------------------------------------------


def _safe_redirect_target(request):
    """
    The `?next=` URL, but only if it is safe to send someone there.

    Without this check an attacker could send a student a link to
    /login/?next=https://evil.example/ — they would sign in on the real site,
    trust it, and be handed straight to a copy of it. `url_has_allowed_host_
    and_scheme` refuses anything pointing off our own host. Returns None when
    there is nothing safe to use, and the caller falls back to the role home.
    """
    target = request.POST.get('next') or request.GET.get('next')
    if target and url_has_allowed_host_and_scheme(
        url=target,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return target
    return None


def login_view(request):
    """
    One login page for all three roles.

    There is no "log in as..." choice, because a person's role is a fact about
    their account, not a thing they get to pick at the door. We authenticate
    the email and password first; only then do we read the role off the user
    record we just proved they own, and send them to the matching page.
    """
    if request.user.is_authenticated:
        return redirect(home_url_for(request.user))

    if request.method == 'POST':
        form = EmailLoginForm(request, data=request.POST)
        if form.is_valid():
            # `get_user()` is the account the form just authenticated. The role
            # comes from that record — never from anything the browser sent.
            user = form.get_user()
            login(request, user)
            messages.success(request, f"Welcome back, {user.full_name}.")
            return redirect(_safe_redirect_target(request) or home_url_for(user))
    else:
        form = EmailLoginForm(request)

    return render(request, 'registration/login.html', {'form': form})


def signup_view(request):
    """
    Student self-registration. Staff accounts are not created here — see the
    docstring on StudentSignupForm for how the role is pinned.
    """
    if request.user.is_authenticated:
        return redirect(home_url_for(request.user))

    if request.method == 'POST':
        form = StudentSignupForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)
            messages.success(
                request,
                f"Your account has been created. Welcome, {user.full_name}.",
            )
            return redirect(home_url_for(user))
    else:
        form = StudentSignupForm()

    return render(request, 'registration/signup.html', {'form': form})


@require_POST
def logout_view(request):
    """
    Log out and return to the login page.

    POST only, which is why the navbar's "Log out" is a one-button form rather
    than an <a href>. A plain link would let any page on the internet log our
    users out just by embedding <img src="/logout/">, and browsers pre-fetching
    links could do it by accident.
    """
    logout(request)
    messages.info(request, "You have been logged out.")
    return redirect('login')


def root_view(request):
    """The bare domain: your own dashboard if we know you, the login page if
    we do not."""
    if request.user.is_authenticated:
        return redirect(home_url_for(request.user))
    return redirect('login')


# ---------------------------------------------------------------------------
# The three dashboards
#
# Placeholders for now. Each one is guarded by the roles that may see it, and
# that guard is the real content of this phase — the pages themselves fill up
# in Phase 3 onwards.
# ---------------------------------------------------------------------------


@role_required(User.Role.STUDENT)
def student_home(request):
    """/complaints/ — where a student will see their own complaints."""
    return render(request, 'dashboards/student_home.html')


@role_required(User.Role.HANDLER, User.Role.ADMIN)
def handler_queue(request):
    """/queue/ — where a handler will see the complaints routed to their unit.

    Administrators may look at it too. It is not their landing page — they
    still start on /dashboard/ — but nothing about the queue needs hiding from
    someone who can already see every complaint in the system.
    """
    return render(request, 'dashboards/handler_queue.html')


@role_required(User.Role.ADMIN)
def admin_dashboard(request):
    """/dashboard/ — where an administrator will see reports across all units."""
    return render(request, 'dashboards/admin_dashboard.html')
