"""
Phase 2 views: getting in, getting out, and the three empty rooms behind the
door. The rooms are furnished in later phases.
"""

from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Case, Count, IntegerField, Q, Value, When
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from .access import home_url_for, role_required
from .forms import (
    ComplaintForm,
    EmailLoginForm,
    HandlerMessageForm,
    MessageForm,
    StudentSignupForm,
)
from . import stats
from .models import Attachment, Complaint, Notification, StatusHistory, Unit, User
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


# ---------------------------------------------------------------------------
# The handler side
# ---------------------------------------------------------------------------

# The status changes a handler can make from the detail page, and the colour
# of each button. Assigning is a separate action, so ASSIGNED is not offered
# here; SUBMITTED is not offered because nothing should go back to "nobody has
# looked at this yet" once somebody has.
STATUS_ACTIONS = [
    (Complaint.Status.IN_PROGRESS, "In progress", 'warning'),
    (Complaint.Status.RESOLVED, "Resolved", 'success'),
    (Complaint.Status.CLOSED, "Closed", 'dark'),
]

_ALLOWED_STATUS_CHANGES = {value for value, _, _ in STATUS_ACTIONS}


def _complaints_in_scope(request):
    """
    Every complaint this staff member is allowed to see.

    The handler equivalent of `_own_complaints()`, and it works the same way:
    the rule is expressed once, as a filter on the query, so the restricted set
    is the only thing any handler view ever starts from.

    A complaint has no unit column of its own. The student picks a category,
    and the category names the office responsible — so "my unit's complaints"
    is written `category__unit`, following that link in SQL. Storing the unit
    on the complaint as well would be faster to query and would eventually be
    wrong: re-routing a category would leave old complaints pointing at the
    office that used to own them.

    Administrators are not filtered at all. That is the whole difference
    between the two roles on these pages.
    """
    complaints = Complaint.objects.select_related(
        'student', 'student__academic_department',
        'category', 'category__unit', 'assigned_to',
    )

    if request.user.is_admin_role:
        return complaints

    # A handler with no unit is a broken account — `User.clean()` forbids it —
    # but if one exists, the safe reading of "complaints for your unit" is
    # none, not all.
    if not request.user.unit_id:
        return complaints.none()

    return complaints.filter(category__unit_id=request.user.unit_id)


@role_required(User.Role.HANDLER, User.Role.ADMIN)
def handler_queue(request):
    """
    /queue/ — the work queue.

    Administrators may look at it too. It is not their landing page — they
    still start on /dashboard/ — but they see every unit rather than one.
    """
    in_scope = _complaints_in_scope(request)

    counts = dict(
        in_scope.values_list('status').annotate(total=Count('id')).order_by()
    )
    unassigned_count = in_scope.filter(assigned_to__isnull=True).count()
    mine_count = in_scope.filter(assigned_to=request.user).count()

    # One button is active at a time, so one query parameter carries the
    # choice. Anything unrecognised falls back to "All" rather than showing an
    # empty queue, which would look like there is no work to do.
    selected = request.GET.get('filter') or ''

    if selected == 'unassigned':
        complaints = in_scope.filter(assigned_to__isnull=True)
    elif selected == 'mine':
        complaints = in_scope.filter(assigned_to=request.user)
    elif selected in Complaint.Status.values:
        complaints = in_scope.filter(status=selected)
    else:
        selected = ''
        complaints = in_scope

    # Default order: unassigned first, then oldest first. A complaint nobody
    # has picked up is the one most likely to be forgotten, and among those the
    # one that has been waiting longest has the best claim on a handler's
    # attention. Complaint.Meta orders by newest first, which is right for a
    # student looking at their own history and exactly wrong here, so this
    # order_by replaces it.
    complaints = complaints.annotate(
        assignment_rank=Case(
            When(assigned_to__isnull=True, then=Value(0)),
            default=Value(1),
            output_field=IntegerField(),
        )
    ).order_by('assignment_rank', 'created_at')

    filters = (
        [('', "All", sum(counts.values()))]
        + [
            (value, label, counts.get(value, 0))
            for value, label in Complaint.Status.choices
        ]
        + [
            ('unassigned', "Unassigned", unassigned_count),
            ('mine', "Assigned to me", mine_count),
        ]
    )

    return render(request, 'handler/queue.html', {
        'complaints': complaints,
        'filters': filters,
        'selected_filter': selected,
        'has_any_complaints': bool(counts),
    })


@role_required(User.Role.HANDLER, User.Role.ADMIN)
def queue_detail(request, reference_no):
    """
    /queue/<reference_no>/ — one complaint, with the actions a handler can take.

    Three different POSTs come back to this URL, told apart by a hidden
    `action` field: claiming the complaint, changing its status, and adding a
    message. They share a page, so they share a handler.
    """
    complaint = get_object_or_404(
        _complaints_in_scope(request), reference_no=reference_no
    )

    form = HandlerMessageForm()

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'assign':
            form = _handle_assign(request, complaint)
        elif action == 'status':
            form = _handle_status_change(request, complaint)
        elif action == 'message':
            form = _handle_handler_message(request, complaint)
        elif action == 'reassign' and request.user.is_admin_role:
            # Guarded by the role here, not only by hiding the dropdown in the
            # template. A handler posting this action falls through to the
            # error below, exactly as if they had invented the name.
            form = _handle_reassign(request, complaint)
        else:
            messages.error(request, "Unrecognised action.")

        if form is None:
            # Redirect after a successful POST, so a refresh does not repeat
            # the action. A form comes back instead only when it failed
            # validation and has errors to show.
            return redirect('queue_detail', reference_no=complaint.reference_no)

    # Unlike the student page, nothing is filtered out of this thread —
    # internal notes are exactly what staff come here to read.
    thread = (
        complaint.messages.select_related('author').order_by('created_at')
    )
    timeline = complaint.status_history.select_related('changed_by').order_by(
        'changed_at'
    )

    days_to_resolve = None
    if complaint.resolved_at:
        days_to_resolve = (complaint.resolved_at - complaint.created_at).days

    # Only administrators may hand a complaint to someone else, and only to a
    # handler in the unit that owns it — sending an ICT problem to the Bursary
    # would put it in front of somebody with no way to fix it.
    assignable_handlers = None
    if request.user.is_admin_role:
        assignable_handlers = _assignable_handlers(complaint)

    return render(request, 'handler/queue_detail.html', {
        'complaint': complaint,
        'thread': thread,
        'timeline': timeline,
        'attachments': complaint.attachments.all(),
        'form': form,
        'status_actions': STATUS_ACTIONS,
        'days_to_resolve': days_to_resolve,
        'assignable_handlers': assignable_handlers,
    })


def _assignable_handlers(complaint):
    """Active handlers in the unit responsible for this complaint."""
    return User.objects.filter(
        role=User.Role.HANDLER,
        unit=complaint.category.unit,
        is_active=True,
    ).order_by('full_name')


def _handle_reassign(request, complaint):
    """
    Administrator reassignment. Returns None on success.

    The chosen handler is looked up inside `_assignable_handlers()` rather than
    fetched by id from the whole user table. That single decision enforces
    three rules at once: the target must exist, must be a handler, and must
    belong to this complaint's unit. A posted id that fails any of them finds
    nothing and is rejected — there is no separate validation to keep in step.
    """
    handler = _assignable_handlers(complaint).filter(
        pk=request.POST.get('handler') or 0
    ).first()

    if handler is None:
        messages.error(
            request,
            "Pick a handler from this complaint's unit.",
        )
        return None

    if handler == complaint.assigned_to:
        messages.info(
            request, f"{complaint.reference_no} is already with {handler.full_name}."
        )
        return None

    previous = complaint.assigned_to

    with transaction.atomic():
        complaint.assigned_to = handler
        complaint._changed_by = request.user

        if complaint.status == Complaint.Status.SUBMITTED:
            # Nobody had it before, so this is also the moment it becomes
            # assigned. Complaint.save() sees the status change and writes the
            # history row itself.
            complaint.status = Complaint.Status.ASSIGNED
            complaint.save()
        else:
            # The status is not changing — the complaint is only moving between
            # people — so save() will not write anything, and the reassignment
            # would leave no trace. StatusHistory is the only audit table this
            # system has, so the row is written here by hand with the same
            # status on both sides: "touched at this time, by this person, not
            # moved". Reading the timeline, it shows up as a dated entry rather
            # than as a status change.
            complaint.save()
            StatusHistory.objects.create(
                complaint=complaint,
                changed_by=request.user,
                old_status=complaint.status,
                new_status=complaint.status,
            )

        # Both sides are told, because both have something to do about it.
        notify(
            complaint.student,
            complaint,
            f"{complaint.reference_no} is now being handled by "
            f"{handler.full_name} in {complaint.unit.name}.",
        )
        notify(
            handler,
            complaint,
            f"{request.user.full_name} assigned {complaint.reference_no} "
            f"to you: {complaint.subject}",
        )

    if previous:
        messages.success(
            request,
            f"{complaint.reference_no} moved from {previous.full_name} "
            f"to {handler.full_name}.",
        )
    else:
        messages.success(
            request, f"{complaint.reference_no} assigned to {handler.full_name}."
        )
    return None


def _handle_assign(request, complaint):
    """
    "Assign to me". Returns None on success, or a form to re-render.

    There is no recipient in the request. The complaint is assigned to
    `request.user` and to nobody else — not because a check rejects other
    values, but because no other value is ever read. Handing a complaint to a
    different handler is administrator work, and arrives in Phase 5.
    """
    if complaint.assigned_to_id is not None:
        messages.error(
            request,
            f"{complaint.reference_no} is already assigned to "
            f"{complaint.assigned_to.full_name}.",
        )
        return None

    with transaction.atomic():
        complaint.assigned_to = request.user
        complaint.status = Complaint.Status.ASSIGNED
        complaint._changed_by = request.user
        complaint.save()

        notify(
            complaint.student,
            complaint,
            f"{complaint.reference_no} has been assigned to "
            f"{request.user.full_name} in {complaint.unit.name}.",
        )

    messages.success(request, f"{complaint.reference_no} is now assigned to you.")
    return None


def _handle_status_change(request, complaint):
    """Move the complaint to one of the three statuses the buttons offer."""
    new_status = request.POST.get('status')

    if new_status not in _ALLOWED_STATUS_CHANGES:
        messages.error(request, "That is not a status you can set from here.")
        return None

    if new_status == complaint.status:
        messages.info(
            request,
            f"{complaint.reference_no} is already {complaint.get_status_display().lower()}.",
        )
        return None

    with transaction.atomic():
        complaint.status = new_status
        # Read by Complaint.save() when it writes the StatusHistory row, so the
        # trail records which handler made the change rather than "the system".
        complaint._changed_by = request.user
        complaint.save()

        notify(
            complaint.student,
            complaint,
            f"{complaint.reference_no} is now "
            f"{complaint.get_status_display().lower()}.",
        )

    messages.success(
        request,
        f"{complaint.reference_no} moved to {complaint.get_status_display()}.",
    )
    return None


def _handle_handler_message(request, complaint):
    """
    Add a reply or an internal note.

    The checkbox decides two things at once, and they have to agree: an
    internal note is invisible to the student, so notifying them about it would
    be a message pointing at something they cannot read.
    """
    form = HandlerMessageForm(request.POST)
    if not form.is_valid():
        return form  # re-render with errors

    with transaction.atomic():
        message = form.save(commit=False)
        message.complaint = complaint
        message.author = request.user
        message.save()

        if not message.is_internal:
            notify(
                complaint.student,
                complaint,
                f"{request.user.full_name} replied to your complaint "
                f"{complaint.reference_no}.",
            )

    messages.success(
        request,
        "Internal note added — the student cannot see it."
        if message.is_internal
        else "Your reply has been sent to the student.",
    )
    return None


# ---------------------------------------------------------------------------
# The admin dashboard
# ---------------------------------------------------------------------------


@role_required(User.Role.ADMIN)
def admin_dashboard(request):
    """
    /dashboard/ — the overview across every unit.

    This view does no counting of its own. Each number comes from a named
    function in complaints/stats.py that computes it in the database; the view's
    job is to gather them and hand them to the template.
    """
    return render(request, 'admin/dashboard.html', {
        'stats': stats.headline_stats(),
        'status_breakdown': stats.status_breakdown(),

        # Both charts' data travels as one dictionary, rendered into the page
        # by `json_script` and read back by Chart.js. See the write-up.
        'chart_data': {
            'per_unit': stats.complaints_per_unit(),
            'by_month': stats.filed_vs_resolved_by_month(months=6),
        },

        'unassigned': stats.unassigned_complaints(),
        'stale': stats.stale_complaints(),
        'high_priority': stats.high_priority_open(),
        'stale_after_days': stats.STALE_AFTER_DAYS,
    })


@role_required(User.Role.ADMIN)
def admin_complaint_list(request):
    """
    /dashboard/complaints/ — every complaint in the system, with filters and a
    search box.

    Each filter is applied only when it was actually supplied and recognised,
    so an unknown value narrows nothing rather than silently emptying the list.
    """
    complaints = Complaint.objects.select_related(
        'student', 'category', 'category__unit', 'assigned_to'
    )

    selected_status = request.GET.get('status') or ''
    if selected_status in Complaint.Status.values:
        complaints = complaints.filter(status=selected_status)
    else:
        selected_status = ''

    selected_priority = request.GET.get('priority') or ''
    if selected_priority in Complaint.Priority.values:
        complaints = complaints.filter(priority=selected_priority)
    else:
        selected_priority = ''

    selected_unit = request.GET.get('unit') or ''
    if selected_unit.isdigit() and Unit.objects.filter(pk=selected_unit).exists():
        complaints = complaints.filter(category__unit_id=selected_unit)
    else:
        selected_unit = ''

    # One box, three columns. `Q(...) | Q(...)` is an OR in SQL, so a typed
    # reference matches the reference column while a typed name matches the
    # student — the searcher does not have to say which kind of thing they
    # are typing.
    query = (request.GET.get('q') or '').strip()
    if query:
        complaints = complaints.filter(
            Q(reference_no__icontains=query)
            | Q(subject__icontains=query)
            | Q(student__full_name__icontains=query)
        )

    return render(request, 'admin/complaint_list.html', {
        'complaints': complaints,
        'units': Unit.objects.all(),
        'statuses': Complaint.Status.choices,
        'priorities': Complaint.Priority.choices,
        'selected_status': selected_status,
        'selected_priority': selected_priority,
        'selected_unit': selected_unit,
        'query': query,
        'is_filtered': bool(
            selected_status or selected_priority or selected_unit or query
        ),
    })
