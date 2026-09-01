"""
Phase 2 views: getting in, getting out, and the three empty rooms behind the
door. The rooms are furnished in later phases.
"""

from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from .access import home_url_for, role_required
from .forms import ComplaintForm, EmailLoginForm, MessageForm, StudentSignupForm
from .models import Attachment, Complaint, Notification, User
from .notifications import notify


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


def _own_complaints(request):
    """
    Every complaint this student is allowed to see — which is to say, theirs.

    This function exists so that no view has to remember the rule. Each student
    view starts from here rather than from `Complaint.objects.all()`, so the
    filter is applied once, in the query, before anything is fetched. A view
    that forgot it would have to go out of its way to do so.

    The `select_related` is not decoration: the list page shows each
    complaint's unit, which lives on the category, and its handler. Without it,
    a page of twenty complaints would run sixty extra queries.
    """
    return Complaint.objects.filter(student=request.user).select_related(
        'category', 'category__unit', 'assigned_to'
    )


@role_required(User.Role.STUDENT)
def student_home(request):
    """
    /complaints/ — the student's own complaints, newest first, with a filter
    across the top for each status.
    """
    own = _own_complaints(request)

    # Counts for the filter buttons. One GROUP BY over the student's own rows,
    # rather than one COUNT per button.
    counts = dict(
        own.values_list('status').annotate(total=Count('id')).order_by()
    )

    # Only a real status is allowed through; anything else is treated as "All".
    # Without this check, ?status=<anything> would be reflected back into the
    # page, and a nonsense value would silently show an empty list as if the
    # student had no complaints.
    selected = request.GET.get('status') or ''
    if selected not in Complaint.Status.values:
        selected = ''

    complaints = own.filter(status=selected) if selected else own

    # (value, label, count) for each button, with All first.
    filters = [('', 'All', sum(counts.values()))] + [
        (value, label, counts.get(value, 0))
        for value, label in Complaint.Status.choices
    ]

    return render(request, 'complaints/student_home.html', {
        'complaints': complaints,
        'filters': filters,
        'selected_status': selected,
        'has_any_complaints': bool(counts),
    })


@role_required(User.Role.STUDENT)
def complaint_create(request):
    """
    /complaints/new/ — file a complaint.

    The reference number and the first StatusHistory row are not written here:
    `Complaint.save()` does both, so they cannot be forgotten by a caller. What
    this view is responsible for is the three things the model cannot know —
    whose complaint it is, who to credit in the history, and the attachment.
    """
    if request.method == 'POST':
        form = ComplaintForm(request.POST, request.FILES)
        if form.is_valid():
            # All of it lands, or none of it does. A complaint with no history
            # row, or an attachment pointing at a complaint that was rolled
            # back, would be worse than a failed submission.
            with transaction.atomic():
                complaint = form.save(commit=False)

                # The owner comes from the session, never from the form.
                complaint.student = request.user
                # Read by Complaint.save() when it writes the first history row,
                # so the trail starts with a person's name rather than a blank.
                complaint._changed_by = request.user
                complaint.save()

                uploaded = form.cleaned_data.get('attachment')
                if uploaded:
                    Attachment.objects.create(complaint=complaint, file=uploaded)

                # Called by hand, right where the thing happens — see
                # complaints/notifications.py for why this is not a signal.
                notify(
                    request.user,
                    complaint,
                    f"We have received your complaint {complaint.reference_no} "
                    f"and sent it to {complaint.unit.name}.",
                )

            messages.success(
                request,
                f"Complaint filed. Your reference number is "
                f"{complaint.reference_no} — quote it in any follow-up.",
            )
            return redirect('complaint_detail', reference_no=complaint.reference_no)
    else:
        form = ComplaintForm()

    return render(request, 'complaints/complaint_form.html', {'form': form})


@role_required(User.Role.STUDENT)
def complaint_detail(request, reference_no):
    """
    /complaints/<reference_no>/ — one complaint, its conversation, and its
    timeline. Also handles the reply box, which posts back to this same URL.
    """
    # The ownership check *is* this line. See the explanation in the reply
    # branch below and in _own_complaints().
    complaint = get_object_or_404(_own_complaints(request), reference_no=reference_no)

    if request.method == 'POST':
        form = MessageForm(request.POST)
        if form.is_valid():
            message = form.save(commit=False)
            message.complaint = complaint
            message.author = request.user
            # Set here, not on the form. A student's reply is part of the
            # conversation with staff; it is never a staff-only note.
            message.is_internal = False
            message.save()
            messages.success(request, "Your reply has been added.")
            # Redirect after a successful POST so that refreshing the page
            # does not post the same reply a second time.
            return redirect('complaint_detail', reference_no=complaint.reference_no)
    else:
        form = MessageForm()

    # THE line that keeps internal notes away from students. Explained at
    # length in the write-up; in short, staff-only notes are excluded here, in
    # the query, so they are never loaded and cannot leak through a template.
    thread = (
        complaint.messages.filter(is_internal=False)
        .select_related('author')
        .order_by('created_at')
    )

    timeline = complaint.status_history.select_related('changed_by').order_by(
        'changed_at'
    )

    # How long it took, shown only once there is an answer to the question.
    days_to_resolve = None
    if complaint.resolved_at:
        days_to_resolve = (complaint.resolved_at - complaint.created_at).days

    return render(request, 'complaints/complaint_detail.html', {
        'complaint': complaint,
        'thread': thread,
        'timeline': timeline,
        'attachments': complaint.attachments.all(),
        'form': form,
        'days_to_resolve': days_to_resolve,
    })


# ---------------------------------------------------------------------------
# Notifications
#
# These use @login_required rather than @role_required: the bell is in the
# navbar on every page, so it has to work for whoever is signed in. Only
# students receive notifications so far, but the views should not have to be
# rewritten when handlers start receiving them too.
# ---------------------------------------------------------------------------


@login_required
def notification_list(request):
    """/notifications/ — everything this user has been told, newest first."""
    notifications = (
        Notification.objects.filter(user=request.user)
        .select_related('complaint')
    )
    return render(request, 'complaints/notification_list.html', {
        'notifications': notifications,
    })


@login_required
def notification_open(request, pk):
    """
    Mark one notification read and go to the complaint it is about.

    Filtering by `user=request.user` here does double duty: it stops anyone
    marking someone else's notification as read, and it means a guessed id
    gives a 404 rather than telling the guesser that the notification exists.
    """
    notification = get_object_or_404(
        Notification.objects.select_related('complaint'),
        pk=pk,
        user=request.user,
    )

    if not notification.is_read:
        notification.is_read = True
        notification.save(update_fields=['is_read'])

    return redirect(
        'complaint_detail', reference_no=notification.complaint.reference_no
    )


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
