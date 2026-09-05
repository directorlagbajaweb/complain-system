"""
Fill the database with believable demo data.

Run with:   python manage.py seed_demo
Rebuild:    python manage.py seed_demo --clear

This is for demonstrations and screenshots, not for tests — tests build their
own data so that they never depend on whatever happens to be in the database.
What this command is for is having something on screen that looks like a real
polytechnic's complaint system rather than "Test complaint 1" repeated fifty
times, and having the dashboard's Attention panel actually contain something.

Three things make the data awkward to create, and each is handled the same way:

  * `created_at`, `changed_at` and the other `auto_now_add` columns cannot be
    set when saving. Django stamps them with the current time whatever you
    pass. So every row is written first and then backdated with a queryset
    `update()`, which goes straight to SQL and skips `save()` entirely.

  * `Complaint.save()` writes a StatusHistory row of its own. That is exactly
    what we want in the application and exactly what we do not want here,
    where the point is to write a whole invented history. So each complaint is
    created as Submitted, its automatic history row is deleted, and the real
    chain is written by hand.

  * Randomness has to be repeatable, or two runs produce different data and
    a screenshot cannot be reproduced. Everything comes from one seeded
    `random.Random`, so the same command always builds the same polytechnic.

All timestamps are built in Africa/Lagos, the project's timezone, so complaints
are filed during Nigerian working hours rather than at whatever time it happens
to be in UTC.
"""

import math
import random
import struct
import zlib
from collections import defaultdict
from datetime import timedelta
from io import StringIO

from django.core.files.base import ContentFile
from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from complaints.models import (
    AcademicDepartment,
    Attachment,
    Complaint,
    Message,
    Notification,
    StatusHistory,
    Unit,
    User,
)
from complaints.notifications import notify
from complaints import stats

# One seed for the whole command. Change it and you get a different but equally
# plausible polytechnic; leave it and every run is identical.
RANDOM_SEED = 20260901

# Every demo account shares this password. It is printed at the end so whoever
# is giving the demo can sign in as any of them.
DEMO_PASSWORD = "demo-pass-2026"

STUDENT_DOMAIN = "student.federalpoly.edu.ng"
STAFF_DOMAIN = "federalpoly.edu.ng"

TOTAL_COMPLAINTS = 55


# ---------------------------------------------------------------------------
# People
# ---------------------------------------------------------------------------

# (full name, matric number, academic department). One student per department,
# which is also the simplest way to spread them evenly.
STUDENTS = [
    ("Chinedu Okafor", "ND/23/CSC/0142", "Computer Science"),
    ("Aisha Bello", "ND/23/ACC/0087", "Accounting"),
    ("Oluwaseun Adeyemi", "HND/22/EEE/0311", "Electrical Engineering"),
    ("Ngozi Eze", "ND/24/MCM/0056", "Mass Communication"),
    ("Ibrahim Musa", "HND/22/CVE/0204", "Civil Engineering"),
    ("Funmilayo Adebayo", "ND/23/BUS/0173", "Business Administration"),
    ("Emeka Nwosu", "ND/24/MEE/0029", "Mechanical Engineering"),
    ("Halima Yusuf", "HND/23/ECO/0118", "Economics"),
    ("Tunde Bakare", "ND/22/POL/0245", "Political Science"),
    ("Chiamaka Obi", "ND/24/MCB/0061", "Microbiology"),
    ("Suleiman Abdullahi", "HND/22/LAW/0192", "Law"),
    ("Blessing Etim", "ND/23/MED/0134", "Medicine and Surgery"),
]

# One handler per unit — the person a complaint routed to that office lands on.
HANDLERS = {
    "Bursary": "Folake Ogunleye",
    "Student Affairs": "Danjuma Yakubu",
    "ICT": "Segun Alabi",
    "Works & Maintenance": "Bashir Lawal",
    "Exams & Records": "Grace Nnamdi",
}


# ---------------------------------------------------------------------------
# What students actually complain about
#
# Keyed by category name, so a complaint's text always matches the office it
# was routed to. Written as (subject, description) pairs with the details a
# real complaint carries — a bank, an amount, a block, a course code — because
# a screenshot full of placeholder text demonstrates nothing.
# ---------------------------------------------------------------------------

COMPLAINT_TEMPLATES = {
    "School fees payment": [
        (
            "Fees paid at the bank but portal still shows unpaid",
            "I paid my ND II second semester fees on 14 July at the Zenith Bank "
            "branch on campus and I have both the teller and the Remita RRR. "
            "The portal still shows my fees as outstanding, so I cannot print "
            "my course form. My RRR is 280194557331.",
        ),
        (
            "Remita receipt will not generate after successful payment",
            "The payment went through and my account was debited ₦68,500, but "
            "when I click 'Print Receipt' the page returns an error. I have "
            "tried three different browsers and two laptops in the ICT centre.",
        ),
        (
            "Charged twice for the same semester fee",
            "My account was debited twice on 2 August for the same RRR, once at "
            "11:42am and again at 11:48am. The bank says the second one went "
            "through to the school and I should report here.",
        ),
        (
            "Acceptance fee not reflecting three weeks after payment",
            "I paid my acceptance fee on 30 June but my admission status still "
            "shows 'Payment Pending'. I have sent the teller to the admissions "
            "email twice with no reply.",
        ),
    ],
    "Refunds and overpayment": [
        (
            "Refund for duplicate fee payment not received",
            "I was debited twice in May and the Bursary confirmed the "
            "overpayment in June. I was told the refund would take four weeks. "
            "It has now been over two months and nothing has come.",
        ),
        (
            "Overpayment of ₦45,000 on school fees",
            "I mistakenly paid the HND fee amount instead of the ND amount. The "
            "difference is ₦45,000. I have the teller showing both the correct "
            "amount and what I paid.",
        ),
    ],
    "Hostel accommodation": [
        (
            "Hostel allocation not showing after payment",
            "I paid the hostel fee on 8 August and got the receipt, but the "
            "portal still shows no room allocated. The porter at Male Hostel "
            "Block C says he cannot let me in without an allocation slip.",
        ),
        (
            "Same room allocated to two students",
            "Room 214 in Female Hostel Block A has been given to me and to "
            "another student. We both have allocation slips with the same room "
            "number and the same session.",
        ),
        (
            "Leaking roof in hostel room, requesting reallocation",
            "The roof over Room 118 leaks badly whenever it rains and my "
            "mattress and books have been soaked twice. I have reported it to "
            "the porter but nothing has been done.",
        ),
    ],
    "Student welfare": [
        (
            "Campus clinic out of basic drugs",
            "I went to the clinic twice last week with malaria symptoms and was "
            "told there were no test kits and no drugs, and to buy them "
            "outside. Many students cannot afford that.",
        ),
        (
            "Request for extension on hostel checkout date",
            "My exams end on the 21st but the hostel checkout date is the 18th. "
            "I am from Maiduguri and cannot travel and come back in between. I "
            "am asking for a three-day extension.",
        ),
    ],
    "Student portal access": [
        (
            "Cannot log into the student portal after password reset",
            "I used the 'Forgot Password' link and got the reset mail, but the "
            "new password is not accepted at login. It keeps saying 'Invalid "
            "credentials' even though I have reset it three times.",
        ),
        (
            "Portal rejects my matric number at login",
            "The portal says my matric number does not exist. I have used the "
            "same number since 2023 and it worked last semester. I have tried "
            "with and without the slashes.",
        ),
        (
            "Portal session expires immediately after login",
            "Every time I log in, the dashboard loads for about two seconds and "
            "then throws me back to the login page. This happens on my phone "
            "and in the ICT laboratory.",
        ),
    ],
    "Campus network and Wi-Fi": [
        (
            "No Wi-Fi signal in the library since Monday",
            "The library Wi-Fi has been completely down since Monday morning. "
            "Students doing project work have had to use their own data. The "
            "access point near the reading room shows no light.",
        ),
        (
            "Campus Wi-Fi disconnects every few minutes in Block B",
            "The signal in Block B drops roughly every three minutes and has to "
            "be reconnected by hand. It has been like this for about two weeks "
            "and makes online lectures impossible to follow.",
        ),
    ],
    "Student email account": [
        (
            "Student email account never created after registration",
            "I completed registration in January but I still have no student "
            "email address. Lecturers send course materials to that address so "
            "I have been missing handouts all semester.",
        ),
        (
            "Cannot receive mail on my student email address",
            "I can sign in and send mail from my student address, but nothing "
            "arrives. A classmate sent me a test message three days ago and it "
            "has never appeared, including in spam.",
        ),
    ],
    "Electricity supply": [
        (
            "No power in Male Hostel Block C for three days",
            "There has been no electricity in Block C since Saturday evening. "
            "Students cannot charge phones or laptops and the water pump does "
            "not run without power, so there is no water either.",
        ),
        (
            "Transformer at the engineering complex not working",
            "The transformer serving the engineering complex has been faulty "
            "since last week. Practical classes in the workshop have been "
            "cancelled twice because the machines cannot run.",
        ),
        (
            "Frequent power outages in the computer laboratory",
            "The laboratory loses power several times a day and the systems "
            "shut down without warning. I lost an entire project file last "
            "Thursday because of this.",
        ),
    ],
    "Water and plumbing": [
        (
            "No running water in Female Hostel Block A",
            "There has been no water in Block A for four days. We have been "
            "fetching from the tap behind the administrative building, which is "
            "a long walk and unsafe at night.",
        ),
        (
            "Burst pipe flooding the walkway near the library",
            "A pipe burst beside the library walkway on Tuesday and water has "
            "been running since. The path is now slippery and students have to "
            "walk on the grass to get past.",
        ),
        (
            "Blocked toilets in the ND II lecture block",
            "Three of the four toilets in the ND II block have been blocked for "
            "over a week. The smell reaches the lecture rooms and classes "
            "nearby are difficult to sit through.",
        ),
    ],
    "Missing or incorrect result": [
        (
            "MTH 201 result missing from my transcript",
            "I sat MTH 201 last session and I know I passed, but the result is "
            "blank on my transcript. My CGPA has been calculated without it and "
            "it is affecting my class of diploma.",
        ),
        (
            "Result shows F for a course I passed",
            "STA 102 shows an F on the portal. I have my marked script showing "
            "58 and the lecturer has confirmed in writing that I passed. Please "
            "have the record corrected.",
        ),
        (
            "Second semester results not released for ND I Computer Science",
            "It has been nine weeks since our last paper and the second "
            "semester results for ND I Computer Science are still not out. "
            "Other departments released theirs weeks ago.",
        ),
    ],
    "Course registration error": [
        (
            "Registered courses not showing on my course form",
            "I registered eight courses and the portal confirmed each one, but "
            "the printed course form only shows five. The three missing ones "
            "are the electives.",
        ),
        (
            "Cannot register CHM 101 — portal says the course is full",
            "The portal says CHM 101 has reached its capacity, but it is a "
            "compulsory course for my programme and I cannot proceed without "
            "it. Registration closes on Friday.",
        ),
        (
            "Wrong level assigned during course registration",
            "My portal has me as ND I but I am in ND II this session. It is "
            "showing me the wrong course list and I cannot register any of my "
            "actual courses.",
        ),
    ],
}


# ---------------------------------------------------------------------------
# Conversation
# ---------------------------------------------------------------------------

STUDENT_REPLIES = [
    "Please is there any update on this? The registration deadline is close.",
    "I have brought the teller to the office as instructed. Kindly check again.",
    "As at this morning it is still not working.",
    "Thank you sir. I will check the portal again tomorrow and confirm.",
    "I went to the office today and was told to come back next week.",
    "This is affecting my course registration. Please treat it as urgent.",
    "It is working now. Thank you very much for your help.",
    "I have sent the details to the email address you gave me.",
]

HANDLER_REPLIES = [
    "We have received your complaint and passed it to the appropriate desk.",
    "Kindly bring the original teller to the office between 9am and 2pm.",
    "This has been escalated to the vendor. We will update you shortly.",
    "The record has now been corrected. Please log in again and confirm.",
    "We are still waiting on confirmation from the bank. Please bear with us.",
    "A technician has been assigned and will visit the block tomorrow morning.",
    "Your name has been added to the list going to the Registrar this week.",
    "Apologies for the delay. The office was closed for the mid-semester break.",
]

INTERNAL_NOTES = [
    "Third complaint about this block this month. The transformer needs "
    "replacing, not repairing — raise it with Works.",
    "Student has been to the office twice already. Confirm with the Bursar "
    "before replying so we do not give two different answers.",
    "Bank confirmation still pending. Do not promise a date yet.",
    "The vendor contract expired in June. Escalate to the Registrar before "
    "responding to this one.",
    "Duplicate of an earlier complaint from the same hostel. Handle together.",
    "Checked the payment log — the money is there, the reconciliation job "
    "just has not run. No need to involve the bank.",
]


# ---------------------------------------------------------------------------
# Shape of the data
# ---------------------------------------------------------------------------

# How many complaints each office carries. Bursary and Works & Maintenance get
# the most, which is what actually happens: money and buildings generate more
# complaints than records do.
UNIT_SHARE = {
    "Bursary": 15,
    "Works & Maintenance": 13,
    "ICT": 10,
    "Exams & Records": 9,
    "Student Affairs": 8,
}

# Complaints per calendar month, oldest first, ending with the month we are
# currently in. Allocating by month rather than by week is deliberate: the
# dashboard's line chart buckets by month, so this is the axis the shape is
# actually read on, and anything decided per week has to survive a translation
# before it reaches the screen.
#
# The rise runs 8 → 13 → 22. The current month gets 12 regardless of how few
# days of it have passed, which is the point: proportionally it would earn one
# or two on the 3rd, and a line that dives to the floor at its right-hand edge
# reads as a broken chart rather than as a month that is three days old. Twelve
# in three days is a higher rate than any month before it, so the story the
# chart tells — complaints are climbing — stays true.
MONTHLY_SHARE = [8, 13, 22, 12]

# Within a month, how uneven the days are. Drawn once per month and used as
# sampling weights, so some days are three or four times busier than others
# and the weekly rhythm is visibly lumpy rather than smooth.
DAY_WEIGHT_CHOICES = [1, 1, 1, 2, 2, 3, 5]

PRIORITY_SHARE = {
    Complaint.Priority.HIGH: 8,     # ~15%
    Complaint.Priority.NORMAL: 36,  # ~65%
    Complaint.Priority.LOW: 11,     # ~20%
}

# High priority complaints that must still be open, so the dashboard's
# "High priority open" list has something in it.
HIGH_PRIORITY_STILL_OPEN = 5

# How many complaints carry a file, so the detail page can be screenshotted
# with a real attachment on it.
ATTACHMENT_COUNT = 6


# ---------------------------------------------------------------------------
# Attachments
#
# The files are generated here rather than committed to the repository, byte by
# byte, using only zlib and struct from the standard library. Two reasons: a
# seeded demo should not depend on binary fixtures somebody has to remember to
# copy, and adding Pillow as a dependency to draw a placeholder image would be
# a poor trade. What comes out is a genuinely valid PDF and a genuinely valid
# PNG — they open.
#
# Keyed by category, so a complaint about fees carries a payment receipt and a
# complaint about a leaking roof carries a photograph.
# ---------------------------------------------------------------------------

ATTACHMENT_SPECS = {
    "School fees payment": {
        'filename': "remita-receipt.pdf",
        'kind': 'pdf',
        'lines': [
            "FEDERAL POLYTECHNIC",
            "Remita Payment Receipt",
            "",
            "RRR ..................... 280194557331",
            "Payer ................... {student}",
            "Matric No ............... {matric}",
            "Description ............. ND II School Fees, 2025/2026",
            "Amount .................. NGN 68,500.00",
            "Bank .................... Zenith Bank Plc",
            "Status .................. SUCCESSFUL",
            "",
            "Attached to complaint {reference}",
        ],
    },
    "Refunds and overpayment": {
        'filename': "bank-teller.pdf",
        'kind': 'pdf',
        'lines': [
            "ZENITH BANK PLC",
            "Counter Deposit Slip (customer copy)",
            "",
            "Depositor ............... {student}",
            "Account ................. Federal Polytechnic Fees Account",
            "Amount .................. NGN 45,000.00",
            "Teller No ............... 4471902",
            "",
            "Attached to complaint {reference}",
        ],
    },
    "Missing or incorrect result": {
        'filename': "result-printout.pdf",
        'kind': 'pdf',
        'lines': [
            "FEDERAL POLYTECHNIC",
            "Statement of Result (portal printout)",
            "",
            "Student ................. {student}",
            "Matric No ............... {matric}",
            "",
            "MTH 201  Engineering Mathematics II ....  --",
            "STA 102  Statistics for Science ........  F",
            "CSC 204  Data Structures ...............  B",
            "",
            "Attached to complaint {reference}",
        ],
    },
    "Course registration error": {
        'filename': "course-form.pdf",
        'kind': 'pdf',
        'lines': [
            "FEDERAL POLYTECHNIC",
            "Course Registration Form (printed from portal)",
            "",
            "Student ................. {student}",
            "Matric No ............... {matric}",
            "Level ................... ND I",
            "",
            "Registered .............. 5 courses",
            "Expected ................ 8 courses",
            "",
            "Attached to complaint {reference}",
        ],
    },
    # Photographs. A student reporting a physical fault sends a picture of it,
    # so these are images rather than documents.
    "Hostel accommodation": {
        'filename': "hostel-room.png",
        'kind': 'png',
        'palette': (118, 106, 92),
    },
    "Electricity supply": {
        'filename': "hostel-block-dark.png",
        'kind': 'png',
        'palette': (58, 62, 78),
    },
    "Water and plumbing": {
        'filename': "burst-pipe.png",
        'kind': 'png',
        'palette': (96, 112, 120),
    },
}


class Command(BaseCommand):
    help = (
        "Populate the database with realistic demo complaints. "
        "Use --clear to wipe existing complaint data and rebuild."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--clear',
            action='store_true',
            help=(
                "Delete all complaints, messages, attachments, status history "
                "and notifications first, then reseed. Units, categories, "
                "academic departments and user accounts are left alone."
            ),
        )

    @transaction.atomic
    def handle(self, *args, **options):
        self.rng = random.Random(RANDOM_SEED)

        # Reference data first: a complaint cannot exist without a category to
        # file it under. seed_lookups is safe to re-run, so just call it —
        # quietly, because its own output is not interesting here.
        call_command('seed_lookups', stdout=StringIO())

        if options['clear']:
            self._clear()

        students = self._create_students()
        handlers = self._create_handlers()

        if Complaint.objects.exists():
            self.stdout.write(
                self.style.WARNING(
                    "\nThe database already contains complaints, so none were "
                    "added — running this twice must not double the data.\n"
                    "Use `python manage.py seed_demo --clear` to wipe the "
                    "complaint data and build it again."
                )
            )
            self._summarise(students, handlers)
            return

        self._create_complaints(students, handlers)
        self._summarise(students, handlers)

    # -- Wiping ------------------------------------------------------------

    def _clear(self):
        """
        Remove the complaint data and nothing else.

        Deleting the complaints alone would cascade to all four of the other
        tables, but they are deleted explicitly so the command can report what
        it removed. Units, categories, departments and users are untouched:
        they are configuration, not demo content, and a handler account that
        vanished on every reseed would be infuriating.
        """
        self.stdout.write("Clearing existing complaint data…")

        # Deleting an Attachment row does not delete the file it points at, so
        # the files are removed first. Without this, every reseed would leave
        # another copy behind in MEDIA_ROOT and Django would start suffixing
        # names to avoid collisions — `remita-receipt_a8Fk2p.pdf`.
        orphaned = 0
        for attachment in Attachment.objects.all():
            attachment.file.delete(save=False)
            orphaned += 1
        if orphaned:
            self.stdout.write(f"  removed {orphaned} attachment files from disk")

        for label, queryset in [
            ("notifications", Notification.objects.all()),
            ("messages", Message.objects.all()),
            ("attachments", Attachment.objects.all()),
            ("status history", StatusHistory.objects.all()),
            ("complaints", Complaint.objects.all()),
        ]:
            removed = queryset.count()
            queryset.delete()
            self.stdout.write(f"  removed {removed} {label}")

    # -- People ------------------------------------------------------------

    def _account(self, email, full_name, **extra):
        """Fetch or create one account. Idempotent by email."""
        user, created = User.objects.get_or_create(
            email=email,
            defaults={'full_name': full_name, 'is_active': True, **extra},
        )
        if created:
            user.set_password(DEMO_PASSWORD)
            user.save()
        return user

    def _create_students(self):
        departments = {d.name: d for d in AcademicDepartment.objects.all()}
        students = []
        for full_name, matric_no, department_name in STUDENTS:
            first, last = full_name.lower().split()
            students.append(
                self._account(
                    email=f"{first}.{last}@{STUDENT_DOMAIN}",
                    full_name=full_name,
                    role=User.Role.STUDENT,
                    matric_no=matric_no,
                    academic_department=departments.get(department_name),
                )
            )
        return students

    def _create_handlers(self):
        """One handler per unit, returned keyed by unit name."""
        handlers = {}
        for unit_name, full_name in HANDLERS.items():
            unit = Unit.objects.get(name=unit_name)
            first, last = full_name.lower().split()
            handlers[unit_name] = self._account(
                email=f"{first}.{last}@{STAFF_DOMAIN}",
                full_name=full_name,
                role=User.Role.HANDLER,
                unit=unit,
            )
        return handlers

    # -- Timestamps --------------------------------------------------------

    @staticmethod
    def _months_back(local_now, count):
        """The first of the month, `count` months before the one we are in."""
        start = local_now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        for _ in range(count):
            start = (start - timedelta(days=1)).replace(day=1)
        return start

    def _filing_times(self, now):
        """
        `TOTAL_COMPLAINTS` filing times, oldest first.

        Each calendar month gets its share from MONTHLY_SHARE. Within a month
        the days carry random weights so some are far busier than others, tilted
        so later days in the month draw more than earlier ones — which puts a
        rise inside each month as well as across them.

        Every time lands between 8am and 6pm Lagos time. Complaints arriving at
        3am would give the whole dataset away at a glance.
        """
        local_now = timezone.localtime(now)
        months = len(MONTHLY_SHARE)

        times = []
        for index, count in enumerate(MONTHLY_SHARE):
            start = self._months_back(local_now, months - 1 - index)
            is_current = index == months - 1

            if is_current:
                # Only the days that have actually happened. Before 9am today
                # has no office hours yet, so it does not count as a day.
                day_count = local_now.day - (1 if local_now.hour < 9 else 0)
                day_count = max(day_count, 1)
            else:
                day_count = (
                    self._months_back(local_now, months - 2 - index).date()
                    - start.date()
                ).days

            # Lumpy, with a mild pull towards the end of the month.
            weights = [
                self.rng.choice(DAY_WEIGHT_CHOICES) * (1 + day / day_count)
                for day in range(day_count)
            ]

            for _ in range(count):
                day = self.rng.choices(range(day_count), weights=weights, k=1)[0]

                latest_hour = 17
                if is_current and day == day_count - 1 and local_now.hour <= 17:
                    # Nothing filed in the hour that has not happened yet.
                    latest_hour = max(local_now.hour - 1, 8)

                times.append(
                    start
                    + timedelta(
                        days=day,
                        hours=self.rng.randint(8, latest_hour),
                        minutes=self.rng.randint(0, 59),
                    )
                )

        return sorted(times)

    def _step(self, previous, now, low_hours, high_hours):
        """
        A gap after `previous` that still lands comfortably in the past.

        A complaint filed two days ago cannot have spent a week in progress, so
        the requested gap is shrunk to whatever time actually exists. Doing it
        here — while the chain is being built — is what stops the whole history
        having to be slid backwards later, which would drag the complaint's
        filing date into the previous month and undo the shape above.
        """
        room = (now - previous).total_seconds() / 3600.0
        if room <= low_hours + 2:
            # Barely any room: take a slice of what is left.
            return previous + timedelta(hours=max(room * 0.4, 0.5))
        return previous + timedelta(
            hours=self.rng.uniform(low_hours, min(high_hours, room - 2))
        )

    def _status_slots(self):
        """
        The status each complaint ends up in, ordered oldest to newest.

        Older complaints have had time to finish, newer ones have not, so the
        sequence runs Closed → Resolved → In progress → Assigned → Submitted.
        Zipped against the sorted filing times, that alone produces a coherent
        history.

        The exceptions are inserted deliberately, and they are the whole point
        of the Attention panel: complaints that are old *and* have not moved.
        Without them the panel is empty and there is nothing to demonstrate.
        """
        slots = (
            [Complaint.Status.CLOSED] * 10
            + [Complaint.Status.RESOLVED] * 20
            + [Complaint.Status.IN_PROGRESS] * 8
            + [Complaint.Status.ASSIGNED] * 5
            + [Complaint.Status.SUBMITTED] * 5
        )

        # Four old complaints somebody took on and then left: they become the
        # Stale list. Two Assigned, two In progress, dropped into old positions
        # so their last status change is well over a fortnight ago.
        for position, status in [
            (12, Complaint.Status.ASSIGNED),
            (17, Complaint.Status.IN_PROGRESS),
            (22, Complaint.Status.ASSIGNED),
            (27, Complaint.Status.IN_PROGRESS),
        ]:
            slots.insert(position, ('stale', status))

        # Three complaints nobody ever picked up: the Unassigned list.
        for position in (31, 36, 41):
            slots.insert(position, ('forgotten', Complaint.Status.SUBMITTED))

        return slots

    def _working_moment(self, moment):
        """
        Nudge a timestamp into Nigerian office hours, 8am to 6pm.

        Anything before 8am moves to the start of that morning; anything from
        6pm moves to the following morning. A handler closing a complaint at
        01:31 is the sort of detail that gives a fabricated dataset away, and
        the fix costs nothing.

        Only ever moves a timestamp forward, which is what keeps the "resolved
        two to fourteen days after filing" window intact. Africa/Lagos has no
        daylight saving, so replacing the hour on an aware datetime is safe
        here in a way it would not be in, say, Europe.
        """
        local = timezone.localtime(moment)
        if 8 <= local.hour < 18:
            return local
        if local.hour < 8:
            return local.replace(hour=8, minute=self.rng.randint(5, 59))
        return (local + timedelta(days=1)).replace(
            hour=self.rng.randint(8, 10), minute=self.rng.randint(0, 59)
        )

    def _normalise_transitions(self, transitions, now):
        """
        Put a whole chain into office hours, keeping it strictly increasing
        and entirely in the past.

        Snapping each timestamp independently can make two steps collide or
        swap, so each one is also forced to land after the one before it. If
        the tail ends up in the future — which happens when a recent complaint
        gets pushed over tonight's boundary — the entire chain slides back by
        the overshoot, preserving every gap within it.
        """
        fixed = []
        previous = None
        for old_status, new_status, when, actor in transitions:
            when = self._working_moment(when)
            if previous is not None and when <= previous:
                # +2..7 hours then re-snap: either later the same working day,
                # or the next morning. Both are after `previous`.
                when = self._working_moment(
                    previous + timedelta(hours=self.rng.randint(2, 7))
                )
            fixed.append((old_status, new_status, when, actor))
            previous = when

        overshoot = fixed[-1][2] - now
        if overshoot > timedelta(0):
            # Slide by whole days, never by the raw overshoot. Africa/Lagos has
            # no daylight saving, so a whole number of days keeps every
            # timestamp at the same time of day — and therefore still inside
            # the office hours just applied. Shifting by an arbitrary two hours
            # and seventeen minutes would undo all of that.
            slide = timedelta(
                days=math.ceil(overshoot.total_seconds() / 86400) or 1
            )
            fixed = [
                (old, new, when - slide, actor) for old, new, when, actor in fixed
            ]

        return fixed

    @staticmethod
    def _resolved_at_from(transitions):
        """
        The moment the complaint was resolved, read back off its own history.

        Derived rather than tracked separately so that `resolved_at` and the
        timeline can never disagree — which they would the moment any of the
        adjustments above moved a timestamp.
        """
        for _, new_status, when, _ in transitions:
            if new_status == Complaint.Status.RESOLVED:
                return when
        return None

    def _history_chain(self, status, created, handler, now, kind):
        """
        Invent a plausible life for one complaint.

        Returns a list of (old_status, new_status, when, actor) ready to be
        written as StatusHistory rows. The first entry always has `None` as
        the old status: that is the row written when a complaint is filed, and
        it is what lets the student's timeline begin at "Submitted" rather
        than appearing from nowhere.

        `kind` is 'stale' for the complaints that must look abandoned. For
        those, every step is pushed far enough back that the newest one is
        comfortably older than the dashboard's seven-day threshold.
        """
        student_filed = (None, Complaint.Status.SUBMITTED, created, 'student')
        transitions = [student_filed]

        if status == Complaint.Status.SUBMITTED:
            return transitions

        if kind == 'stale':
            # Picked up soon after filing, then forgotten. The gap that
            # follows is what makes it stale.
            assigned_at = created + timedelta(
                days=self.rng.randint(1, 3), hours=self.rng.randint(0, 8)
            )
        else:
            assigned_at = self._step(created, now, 4, 60)
        transitions.append(
            (Complaint.Status.SUBMITTED, Complaint.Status.ASSIGNED, assigned_at, 'handler')
        )

        if status == Complaint.Status.ASSIGNED:
            return transitions

        in_progress_at = (
            assigned_at + timedelta(days=self.rng.randint(1, 3))
            if kind == 'stale'
            else self._step(assigned_at, now, 6, 72)
        )
        transitions.append(
            (
                Complaint.Status.ASSIGNED,
                Complaint.Status.IN_PROGRESS,
                in_progress_at,
                'handler',
            )
        )

        if status == Complaint.Status.IN_PROGRESS:
            return transitions

        # Resolved between two and fourteen days after filing, as asked — and
        # never before the work started. The upper bound is thirteen rather
        # than fourteen because moving a timestamp into office hours can push
        # it into the next morning, and the window has to survive that.
        resolved_at = created + timedelta(
            days=self.rng.randint(2, 13),
            hours=self.rng.randint(0, 8),
        )
        if resolved_at <= in_progress_at:
            resolved_at = in_progress_at + timedelta(hours=self.rng.randint(3, 20))
        transitions.append(
            (
                Complaint.Status.IN_PROGRESS,
                Complaint.Status.RESOLVED,
                resolved_at,
                'handler',
            )
        )

        if status == Complaint.Status.CLOSED:
            closed_at = resolved_at + timedelta(
                days=self.rng.randint(1, 10), hours=self.rng.randint(0, 10)
            )
            if closed_at >= now:
                closed_at = now - timedelta(hours=self.rng.randint(1, 12))
            transitions.append(
                (
                    Complaint.Status.RESOLVED,
                    Complaint.Status.CLOSED,
                    closed_at,
                    'handler',
                )
            )

        return transitions

    # -- Building the complaints -------------------------------------------

    def _create_complaints(self, students, handlers):
        now = timezone.now()
        stale_threshold = now - timedelta(days=stats.STALE_AFTER_DAYS)

        times = self._filing_times(now)
        slots = self._status_slots()

        categories_by_unit = {
            unit.name: list(unit.categories.all())
            for unit in Unit.objects.prefetch_related('categories')
        }

        # Which office each complaint goes to, shuffled so the busy units are
        # not all bunched at one end of the timeline.
        unit_names = [
            name for name, count in UNIT_SHARE.items() for _ in range(count)
        ]
        self.rng.shuffle(unit_names)

        # Build every complaint's plan before writing anything, so priorities
        # can be assigned with knowledge of which ones ended up open.
        plans = []
        for index, (moment, slot) in enumerate(zip(times, slots)):
            kind, status = slot if isinstance(slot, tuple) else ('normal', slot)
            created = moment

            # Two guarantees the sampled dates cannot make on their own.
            if kind == 'forgotten':
                # Must be old enough to appear in the Unassigned list, which
                # is about complaints that have been waiting, not new ones.
                created = min(created, now - timedelta(days=self.rng.randint(12, 34)))
            elif status in (Complaint.Status.RESOLVED, Complaint.Status.CLOSED):
                # Needs room for a resolution two to fourteen days later that
                # still lands in the past.
                created = min(created, now - timedelta(days=self.rng.randint(4, 20)))

            transitions = self._history_chain(status, created, handlers, now, kind)

            if kind == 'stale':
                # Push the whole chain back until its last movement is older
                # than the dashboard's threshold, with a fortnight to spare.
                last_change = transitions[-1][2]
                if last_change > stale_threshold:
                    # Whole days again, for the same reason as the slide in
                    # _normalise_transitions: it keeps the time of day intact.
                    overdue = math.ceil(
                        (last_change - stale_threshold).total_seconds() / 86400
                    )
                    shift = timedelta(days=overdue + self.rng.randint(9, 30))
                    transitions = [
                        (old, new, when - shift, actor)
                        for old, new, when, actor in transitions
                    ]

            # Office hours, strictly increasing, all in the past. Done last so
            # it also tidies the fractional shift the stale adjustment leaves.
            transitions = self._normalise_transitions(transitions, now)

            # The complaint was filed when its first history row says it was,
            # and resolved when its resolution row says it was. Reading both
            # back off the chain is what stops them drifting apart.
            created = transitions[0][2]
            resolved_at = self._resolved_at_from(transitions)

            unit_name = unit_names[index]
            category = self.rng.choice(categories_by_unit[unit_name])
            subject, description = self.rng.choice(COMPLAINT_TEMPLATES[category.name])

            plans.append({
                'kind': kind,
                'status': status,
                'created': created,
                'resolved_at': resolved_at,
                'transitions': transitions,
                'unit_name': unit_name,
                'category': category,
                'subject': subject,
                'description': description,
                'student': self.rng.choice(students),
                # Nobody has picked up a Submitted complaint — that is what
                # Submitted means.
                'handler': (
                    None if status == Complaint.Status.SUBMITTED
                    else handlers[unit_name]
                ),
            })

        self._assign_priorities(plans)

        self.stdout.write(f"Creating {len(plans)} complaints…")
        for plan in plans:
            self._write_complaint(plan, now)

        count = self._create_attachments()
        self.stdout.write(f"Writing {count} attachment files into MEDIA_ROOT…")

    def _assign_priorities(self, plans):
        """
        Spread priorities over the plans, making sure enough high priority
        complaints are still open for the dashboard panel to have content.
        """
        open_statuses = {
            Complaint.Status.SUBMITTED,
            Complaint.Status.ASSIGNED,
            Complaint.Status.IN_PROGRESS,
        }
        still_open = [p for p in plans if p['status'] in open_statuses]
        finished = [p for p in plans if p['status'] not in open_statuses]

        high_open = self.rng.sample(still_open, HIGH_PRIORITY_STILL_OPEN)
        remaining_high = PRIORITY_SHARE[Complaint.Priority.HIGH] - HIGH_PRIORITY_STILL_OPEN
        high_finished = self.rng.sample(finished, remaining_high)

        for plan in high_open + high_finished:
            plan['priority'] = Complaint.Priority.HIGH

        rest = [p for p in plans if 'priority' not in p]
        self.rng.shuffle(rest)
        low_count = PRIORITY_SHARE[Complaint.Priority.LOW]
        for plan in rest[:low_count]:
            plan['priority'] = Complaint.Priority.LOW
        for plan in rest[low_count:]:
            plan['priority'] = Complaint.Priority.NORMAL

    def _write_complaint(self, plan, now):
        """
        Write one complaint and everything hanging off it.

        The dance in the middle is the point: create normally so the reference
        number is generated by the same code the application uses, then throw
        away the automatic history row and backdate the record into the past.
        """
        complaint = Complaint.objects.create(
            student=plan['student'],
            category=plan['category'],
            subject=plan['subject'],
            description=plan['description'],
            priority=plan['priority'],
        )

        # Complaint.save() wrote a "filed just now" history row. The real
        # history is invented below, so this one goes.
        complaint.status_history.all().delete()

        # update() writes straight to SQL, so auto_now_add does not intervene
        # and save() does not run again.
        Complaint.objects.filter(pk=complaint.pk).update(
            created_at=plan['created'],
            status=plan['status'],
            resolved_at=plan['resolved_at'],
            assigned_to=plan['handler'],
        )
        complaint.refresh_from_db()

        handler = plan['handler']
        student = plan['student']

        for old_status, new_status, when, actor in plan['transitions']:
            row = StatusHistory.objects.create(
                complaint=complaint,
                changed_by=student if actor == 'student' else handler,
                old_status=old_status,
                new_status=new_status,
            )
            self._backdate(row, changed_at=when)

        self._write_messages(complaint, plan, now)
        self._write_notifications(complaint, plan, now)

    def _write_messages(self, complaint, plan, now):
        """
        One to four messages on most complaints, alternating sides, plus an
        internal note on roughly a quarter of them.

        Messages are spread between the complaint being filed and its last
        movement, so a conversation never appears to have happened before the
        complaint existed or after it was closed.
        """
        first = plan['created']
        last = max(when for _, _, when, _ in plan['transitions'])
        if last <= first:
            last = min(now, first + timedelta(days=2))
        span = (last - first).total_seconds()

        # A staff-only note on about a quarter of complaints — enough that the
        # handler's page demonstrably shows something the student's does not.
        # Decided before the early return below, because whether staff talked
        # among themselves has nothing to do with whether they wrote back.
        if plan['handler'] is not None and self.rng.random() < 0.30:
            note = Message.objects.create(
                complaint=complaint,
                author=plan['handler'],
                body=self.rng.choice(INTERNAL_NOTES),
                is_internal=True,
            )
            self._backdate(
                note,
                created_at=first + timedelta(seconds=self.rng.uniform(0.2, 0.9) * span),
            )

        if self.rng.random() < 0.15:
            return  # some complaints simply have no conversation

        count = self.rng.randint(1, 4)
        offsets = sorted(
            self.rng.uniform(0.05, 0.95) * span for _ in range(count)
        )

        # Staff usually answer first, then it alternates.
        for position, offset in enumerate(offsets):
            from_handler = position % 2 == 0 and plan['handler'] is not None
            message = Message.objects.create(
                complaint=complaint,
                author=plan['handler'] if from_handler else plan['student'],
                body=self.rng.choice(
                    HANDLER_REPLIES if from_handler else STUDENT_REPLIES
                ),
                is_internal=False,
            )
            self._backdate(message, created_at=first + timedelta(seconds=offset))

    def _write_notifications(self, complaint, plan, now):
        """
        Notify the student about some of what happened to their complaint.

        Uses the application's own `notify()` helper rather than creating rows
        directly, so the demo data is built the same way real notifications
        are — then backdated, and some marked read, which a live notification
        never is at the moment it is created.
        """
        # Only some complaints carry notifications, or the bell shows an
        # implausible number and every list is a wall of text.
        if self.rng.random() > 0.45:
            return

        for old_status, new_status, when, actor in plan['transitions']:
            if actor == 'student':
                continue  # they know, they filed it
            if self.rng.random() > 0.6:
                continue

            notification = notify(
                plan['student'],
                complaint,
                f"{complaint.reference_no} is now "
                f"{Complaint.Status(new_status).label.lower()}.",
            )
            # Older news has usually been read; recent news often has not.
            age_days = (now - when).days
            is_read = self.rng.random() < (0.85 if age_days > 14 else 0.35)
            self._backdate(notification, created_at=when, is_read=is_read)

    # -- Generating the attachment files -----------------------------------

    @staticmethod
    def _pdf_bytes(lines):
        """
        A one-page PDF containing `lines`, built by hand.

        A PDF is five objects — catalogue, page tree, page, font, content
        stream — followed by a cross-reference table giving the byte offset of
        each. The offsets are why this is assembled into a bytearray as it
        goes: they have to be counted, not guessed.

        Text is encoded as latin-1 because that is what the standard Helvetica
        encoding covers. The Naira sign is not in it, so amounts are written
        "NGN 68,500.00" rather than with the symbol — a real portal printout
        does the same.
        """
        content = "BT\n/F1 11 Tf\n56 780 Td\n15 TL\n"
        for line in lines:
            # \ ( ) are PDF string escapes and would corrupt the stream.
            escaped = (
                line.replace('\\', r'\\').replace('(', r'\(').replace(')', r'\)')
            )
            content += f"({escaped}) Tj T*\n"
        content += "ET"
        stream = content.encode('latin-1', errors='replace')

        objects = [
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
            b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica "
            b"/Encoding /WinAnsiEncoding >>",
            b"<< /Length "
            + str(len(stream)).encode('ascii')
            + b" >>\nstream\n"
            + stream
            + b"\nendstream",
        ]

        out = bytearray(b"%PDF-1.4\n")
        offsets = []
        for number, body in enumerate(objects, start=1):
            offsets.append(len(out))
            out += f"{number} 0 obj\n".encode('ascii') + body + b"\nendobj\n"

        xref_at = len(out)
        out += f"xref\n0 {len(objects) + 1}\n".encode('ascii')
        out += b"0000000000 65535 f \n"
        for offset in offsets:
            out += f"{offset:010d} 00000 n \n".encode('ascii')
        out += (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_at}\n%%EOF\n"
        ).encode('ascii')

        return bytes(out)

    def _png_bytes(self, base_colour, width=320, height=240):
        """
        A small PNG, built by hand: header, one compressed image chunk, end.

        It stands in for a photograph a student took of a fault — a wall with
        a darker band across it and a little grain, which at thumbnail size
        reads as a picture rather than as a colour swatch. Enough for a
        screenshot to look real; nobody is meant to study it.

        Each scanline in a PNG is prefixed with a filter byte, and 0 means "no
        filter", which is why every row starts with a zero.
        """
        red, green, blue = base_colour
        rows = []
        for y in range(height):
            # A soft vertical gradient, plus a darker horizontal band a third
            # of the way down so the image has some structure.
            shade = 1.0 - (y / height) * 0.35
            if height // 3 <= y < height // 3 + height // 8:
                shade *= 0.62
            row = bytearray(b'\x00')
            for _ in range(width):
                grain = self.rng.randint(-8, 8)
                row += bytes(
                    (
                        max(0, min(255, int(red * shade) + grain)),
                        max(0, min(255, int(green * shade) + grain)),
                        max(0, min(255, int(blue * shade) + grain)),
                    )
                )
            rows.append(bytes(row))

        def chunk(tag, payload):
            body = tag + payload
            return (
                struct.pack('>I', len(payload))
                + body
                + struct.pack('>I', zlib.crc32(body) & 0xFFFFFFFF)
            )

        # Colour type 2 is 8-bit RGB with no alpha.
        header = struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0)
        return (
            b'\x89PNG\r\n\x1a\n'
            + chunk(b'IHDR', header)
            + chunk(b'IDAT', zlib.compress(b''.join(rows), 9))
            + chunk(b'IEND', b'')
        )

    def _create_attachments(self):
        """
        Give a handful of complaints a file, spread across categories so the
        demo shows both a document and a photograph.

        One complaint is taken from each eligible category, in a shuffled
        order, so the six chosen are never all receipts.
        """
        eligible = Complaint.objects.filter(
            category__name__in=ATTACHMENT_SPECS
        ).select_related('student', 'category').order_by('reference_no')

        by_category = defaultdict(list)
        for complaint in eligible:
            by_category[complaint.category.name].append(complaint)

        category_names = sorted(by_category)
        self.rng.shuffle(category_names)

        created = 0
        for category_name in category_names[:ATTACHMENT_COUNT]:
            complaint = self.rng.choice(by_category[category_name])
            spec = ATTACHMENT_SPECS[category_name]

            if spec['kind'] == 'pdf':
                data = self._pdf_bytes([
                    line.format(
                        student=complaint.student.full_name,
                        matric=complaint.student.matric_no or "—",
                        reference=complaint.reference_no,
                    )
                    for line in spec['lines']
                ])
            else:
                data = self._png_bytes(spec['palette'])

            attachment = Attachment(complaint=complaint)
            # `file.save()` writes the bytes through the storage backend into
            # MEDIA_ROOT, under the `attachments/%Y/%m/` path the model asks
            # for, and saves the row.
            attachment.file.save(spec['filename'], ContentFile(data), save=True)

            # Evidence arrives with the complaint or shortly after it, never
            # before — and `uploaded_at` is auto_now_add, so it is backdated
            # like everything else here.
            self._backdate(
                attachment,
                uploaded_at=complaint.created_at
                + timedelta(minutes=self.rng.randint(2, 240)),
            )
            created += 1

        return created

    @staticmethod
    def _backdate(instance, **fields):
        """
        Set columns that `save()` refuses to honour.

        `auto_now_add` fields are stamped by Django during save and cannot be
        passed in. A queryset `update()` goes straight to SQL and bypasses all
        of that, which is exactly what seed data needs and exactly what
        application code should never do.
        """
        type(instance).objects.filter(pk=instance.pk).update(**fields)
        for name, value in fields.items():
            setattr(instance, name, value)

    # -- Reporting ---------------------------------------------------------

    def _summarise(self, students, handlers):
        write = self.stdout.write

        write(self.style.SUCCESS("\nDone.\n"))

        write("By status")
        for value, label, count in stats.status_breakdown():
            write(f"  {label:<14} {count:>3}")

        write("\nBy unit")
        per_unit = stats.complaints_per_unit()
        for name, count in zip(per_unit['labels'], per_unit['values']):
            write(f"  {name:<22} {count:>3}")

        write("\nBy priority")
        for value, label in Complaint.Priority.choices:
            count = Complaint.objects.filter(priority=value).count()
            write(f"  {label:<14} {count:>3}")

        # Read straight from the dashboard's own functions, so this is not a
        # claim about what the Attention panel will show — it is what it will
        # show.
        write("\nAttention panel")
        write(f"  Unassigned            {stats.unassigned_complaints().count():>3}")
        write(
            f"  Stale (>{stats.STALE_AFTER_DAYS}d no change) "
            f"{stats.stale_complaints().count():>3}"
        )
        write(f"  High priority open    {stats.high_priority_open().count():>3}")

        average = stats.average_days_to_resolve()
        write("\nTotals")
        write(f"  Complaints            {Complaint.objects.count():>3}")
        write(f"  Messages              {Message.objects.count():>3}")
        write(
            f"  … of which internal   "
            f"{Message.objects.filter(is_internal=True).count():>3}"
        )
        write(f"  Attachments           {Attachment.objects.count():>3}")
        write(f"  Status history rows   {StatusHistory.objects.count():>3}")
        write(f"  Notifications         {Notification.objects.count():>3}")
        write(
            f"  … unread              "
            f"{Notification.objects.filter(is_read=False).count():>3}"
        )
        write(f"  Avg days to resolve   {average if average is not None else '—':>3}")

        write(self.style.SUCCESS(f"\nSign in with password: {DEMO_PASSWORD}"))
        write(f"  student  {students[0].email}")
        write(f"  handler  {handlers['Bursary'].email}")
        write("  admin    create one with `python manage.py createsuperuser`")
