"""
The numbers behind the admin dashboard.

Every function here returns a finished answer and does its counting in the
database. Nothing loads a list of complaints and adds things up in Python.

That distinction matters more than it looks. `len(Complaint.objects.all())`
and `Complaint.objects.count()` give the same number, but the first one drags
every row across the wire and builds a model instance for each. It is fine with
forty complaints in a demo and hopeless with forty thousand. Writing the sums
as aggregates means the database does the work it is good at and sends back a
single row.

Keeping them here rather than in views.py also makes the claim checkable: if
you want to know how a tile is calculated, there is exactly one place to look.
"""

from datetime import timedelta

from django.db.models import (
    Avg,
    Count,
    DurationField,
    ExpressionWrapper,
    F,
    Max,
    Q,
)
from django.db.models.functions import TruncMonth
from django.utils import timezone

from .models import Complaint, Unit

# "Open" means nobody is finished with it. Resolved and Closed are the two
# endings; everything else is still live. Defined once, because a dashboard
# whose tiles disagree about what "open" means is worse than no dashboard.
FINISHED_STATUSES = [Complaint.Status.RESOLVED, Complaint.Status.CLOSED]

# How long a complaint can sit untouched before it is called stale.
STALE_AFTER_DAYS = 7

# Statuses where somebody has taken the complaint on and is expected to be
# doing something about it. Submitted is excluded — an untouched submitted
# complaint is "unassigned", which is its own list.
IN_HAND_STATUSES = [Complaint.Status.ASSIGNED, Complaint.Status.IN_PROGRESS]


def headline_stats():
    """
    The four tiles, in two queries.

    The first three counts come back from a single trip: `Count` takes a
    `filter=` argument, so one pass over the table can count several different
    things at once instead of running a query per tile.
    """
    counts = Complaint.objects.aggregate(
        total=Count('id'),
        open=Count('id', filter=~Q(status__in=FINISHED_STATUSES)),
        high_priority_open=Count(
            'id',
            filter=Q(priority=Complaint.Priority.HIGH)
            & ~Q(status__in=FINISHED_STATUSES),
        ),
    )
    counts['average_days_to_resolve'] = average_days_to_resolve()
    return counts


def average_days_to_resolve():
    """
    Mean time from filing to resolution, in days, or None if nothing has been
    resolved yet.

    `resolved_at - created_at` is subtraction done by the database, averaged by
    the database, and returned as a single duration. Only complaints that have
    actually been resolved are included: counting unresolved ones as zero would
    drag the average down and make the service look faster the more work it
    left undone.
    """
    result = (
        Complaint.objects.filter(resolved_at__isnull=False)
        .annotate(
            time_taken=ExpressionWrapper(
                F('resolved_at') - F('created_at'), output_field=DurationField()
            )
        )
        .aggregate(average=Avg('time_taken'))
    )

    average = result['average']
    if average is None:
        return None

    # Django hands back a timedelta on both SQLite and PostgreSQL. Some
    # backends return raw microseconds instead, so accept either rather than
    # crashing the whole dashboard over a units mismatch.
    if isinstance(average, timedelta):
        return round(average.total_seconds() / 86400, 1)
    return round(float(average) / 86400 / 1_000_000, 1)


def status_breakdown():
    """
    (value, label, count) for all five statuses, including the ones at zero.

    The database only returns rows for statuses that exist, so the result is
    merged onto the full list of choices. Without that step a status nobody has
    used would simply be missing from the strip, and a gap reads as an error
    rather than as a zero.
    """
    counts = dict(
        Complaint.objects.values_list('status').annotate(total=Count('id')).order_by()
    )
    return [
        (value, label, counts.get(value, 0))
        for value, label in Complaint.Status.choices
    ]


def complaints_per_unit():
    """
    How much work each office is carrying. Feeds the bar chart.

    Counting starts from Unit rather than from Complaint so that an office with
    no complaints still appears, at zero. Counting from the complaints side
    would silently drop it, and "the Bursary has nothing" is worth showing.
    """
    rows = (
        Unit.objects.annotate(total=Count('categories__complaints'))
        .order_by('-total', 'name')
        .values('name', 'total')
    )
    return {
        'labels': [row['name'] for row in rows],
        'values': [row['total'] for row in rows],
    }


def _recent_month_starts(months):
    """The first day of each of the last `months` months, oldest first."""
    cursor = timezone.localtime().replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    )
    starts = []
    for _ in range(months):
        starts.append(cursor)
        # Step back into the previous month by stepping off the start of this
        # one. Doing it this way avoids any month-length arithmetic.
        cursor = (cursor - timedelta(days=1)).replace(
            day=1, hour=0, minute=0, second=0, microsecond=0
        )
    return list(reversed(starts))


def filed_vs_resolved_by_month(months=6):
    """
    Two series for the line chart: how many complaints were filed each month
    against how many were resolved.

    Read together they say whether the school is keeping up. Two lines close to
    each other mean work is going out about as fast as it comes in; a filed
    line above a resolved line for several months means a backlog is building.

    `TruncMonth` does the bucketing in SQL — it rounds each timestamp down to
    the first of its month so the database can group by it. The result is then
    laid onto a fixed six-month axis: a month with no activity has no row to
    return, and a chart that skips empty months would compress a quiet period
    and misrepresent the trend.
    """
    starts = _recent_month_starts(months)
    window_start = starts[0]

    def by_month(queryset, field):
        rows = (
            queryset.annotate(month=TruncMonth(field))
            .values('month')
            .annotate(total=Count('id'))
        )
        # Keyed by (year, month) rather than by the datetime itself, so the
        # lookup cannot miss over a timezone or microsecond difference.
        return {(row['month'].year, row['month'].month): row['total'] for row in rows}

    filed = by_month(
        Complaint.objects.filter(created_at__gte=window_start), 'created_at'
    )
    resolved = by_month(
        Complaint.objects.filter(resolved_at__gte=window_start), 'resolved_at'
    )

    return {
        'labels': [start.strftime('%b %Y') for start in starts],
        'filed': [filed.get((s.year, s.month), 0) for s in starts],
        'resolved': [resolved.get((s.year, s.month), 0) for s in starts],
    }


# ---------------------------------------------------------------------------
# The attention lists
# ---------------------------------------------------------------------------

def _with_related():
    return Complaint.objects.select_related(
        'student', 'category', 'category__unit', 'assigned_to'
    )


def unassigned_complaints():
    """Submitted, and nobody has picked it up. Longest wait first."""
    return (
        _with_related()
        .filter(status=Complaint.Status.SUBMITTED, assigned_to__isnull=True)
        .order_by('created_at')
    )


def stale_complaints(days=STALE_AFTER_DAYS):
    """
    Taken on by somebody, but nothing has happened to it in over `days` days.

    Staleness is measured from the *last status change*, not from when the
    complaint was filed. A complaint opened three months ago and moved to
    In progress this morning is being worked on; one opened yesterday and left
    alone since is not — and only the history table can tell those apart.

    `Max('status_history__changed_at')` is the newest row in that complaint's
    audit trail, computed by the database per complaint. Every complaint has at
    least one such row, written when it was created, so nothing falls out of
    the comparison for want of a history.
    """
    cutoff = timezone.now() - timedelta(days=days)
    return (
        _with_related()
        .filter(status__in=IN_HAND_STATUSES)
        .annotate(last_change=Max('status_history__changed_at'))
        .filter(last_change__lt=cutoff)
        .order_by('last_change')
    )


def high_priority_open():
    """High priority and not yet finished with. Oldest first."""
    return (
        _with_related()
        .filter(priority=Complaint.Priority.HIGH)
        .exclude(status__in=FINISHED_STATUSES)
        .order_by('created_at')
    )
