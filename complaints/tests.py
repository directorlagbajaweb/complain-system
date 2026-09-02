"""
Tests for Phase 2: accounts and access control.

These are worth having even though the pages behind the door are still empty.
The access rules are the part of the system that has to be right — an empty
page shown to the wrong person is a bug that becomes a data leak the moment
Phase 3 puts real complaints on it. Testing the rules now means later phases
cannot quietly break them.

Run with:  python manage.py test
"""

from django.test import TestCase
from django.urls import reverse

from .models import AcademicDepartment, Unit, User

STUDENT_HOME = '/complaints/'
HANDLER_QUEUE = '/queue/'
ADMIN_DASHBOARD = '/dashboard/'

PASSWORD = 'a-long-enough-password-42'


class AccessControlTests(TestCase):
    """Requirements 7 and 8: who may open which of the three dashboards."""

    @classmethod
    def setUpTestData(cls):
        cls.department = AcademicDepartment.objects.create(name="Computer Science")
        cls.unit = Unit.objects.create(name="ICT")

        cls.student = User.objects.create_user(
            email='student@school.edu',
            password=PASSWORD,
            full_name="Ada Student",
            matric_no='CSC/2023/001',
            academic_department=cls.department,
        )
        cls.handler = User.objects.create_user(
            email='handler@school.edu',
            password=PASSWORD,
            full_name="Bola Handler",
            role=User.Role.HANDLER,
            unit=cls.unit,
        )
        cls.admin = User.objects.create_user(
            email='admin@school.edu',
            password=PASSWORD,
            full_name="Chidi Admin",
            role=User.Role.ADMIN,
        )

    # -- Anonymous users ---------------------------------------------------

    def test_anonymous_is_sent_to_login_from_every_dashboard(self):
        for url in (STUDENT_HOME, HANDLER_QUEUE, ADMIN_DASHBOARD):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertRedirects(
                    response, f"{reverse('login')}?next={url}"
                )

    def test_login_page_remembers_where_you_were_going(self):
        """Signing in after being bounced lands you on the page you asked for,
        not on your default dashboard."""
        self.client.get(HANDLER_QUEUE)  # bounced to /login/?next=/queue/
        response = self.client.post(
            f"{reverse('login')}?next={HANDLER_QUEUE}",
            {'username': 'handler@school.edu', 'password': PASSWORD, 'next': HANDLER_QUEUE},
        )
        self.assertRedirects(response, HANDLER_QUEUE)

    # -- Students ----------------------------------------------------------

    def test_student_reaches_own_dashboard(self):
        self.client.force_login(self.student)
        self.assertEqual(self.client.get(STUDENT_HOME).status_code, 200)

    def test_student_cannot_open_queue_or_dashboard(self):
        self.client.force_login(self.student)
        for url in (HANDLER_QUEUE, ADMIN_DASHBOARD):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertRedirects(response, STUDENT_HOME)

    # -- Handlers ----------------------------------------------------------

    def test_handler_reaches_own_queue(self):
        self.client.force_login(self.handler)
        self.assertEqual(self.client.get(HANDLER_QUEUE).status_code, 200)

    def test_handler_cannot_open_admin_dashboard(self):
        self.client.force_login(self.handler)
        response = self.client.get(ADMIN_DASHBOARD)
        self.assertRedirects(response, HANDLER_QUEUE)

    # -- Administrators ----------------------------------------------------

    def test_admin_reaches_own_dashboard(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(ADMIN_DASHBOARD).status_code, 200)


class LoginRedirectTests(TestCase):
    """Requirement 3: one login page, three destinations."""

    @classmethod
    def setUpTestData(cls):
        cls.department = AcademicDepartment.objects.create(name="Economics")
        cls.unit = Unit.objects.create(name="Bursary")

    def _make(self, email, role, **extra):
        return User.objects.create_user(
            email=email, password=PASSWORD, full_name="Test Person",
            role=role, **extra
        )

    def test_each_role_lands_on_its_own_page(self):
        cases = [
            (self._make('s@school.edu', User.Role.STUDENT), STUDENT_HOME),
            (self._make('h@school.edu', User.Role.HANDLER, unit=self.unit), HANDLER_QUEUE),
            (self._make('a@school.edu', User.Role.ADMIN), ADMIN_DASHBOARD),
        ]
        for user, expected in cases:
            with self.subTest(role=user.role):
                self.client.logout()
                response = self.client.post(
                    reverse('login'),
                    {'username': user.email, 'password': PASSWORD},
                )
                self.assertRedirects(response, expected)

    def test_wrong_password_does_not_log_anyone_in(self):
        self._make('s2@school.edu', User.Role.STUDENT)
        response = self.client.post(
            reverse('login'),
            {'username': 's2@school.edu', 'password': 'not-the-password'},
        )
        self.assertEqual(response.status_code, 200)  # form redisplayed
        self.assertFalse(response.wsgi_request.user.is_authenticated)

    def test_open_redirect_is_refused(self):
        """A `next` pointing at another site is ignored in favour of the role
        home, so the login page cannot be used to launder a phishing link."""
        user = self._make('s3@school.edu', User.Role.STUDENT)
        response = self.client.post(
            f"{reverse('login')}?next=https://evil.example/",
            {'username': user.email, 'password': PASSWORD},
        )
        self.assertRedirects(response, STUDENT_HOME)


class SignupTests(TestCase):
    """Requirement 4: students may register themselves, and only as students."""

    @classmethod
    def setUpTestData(cls):
        cls.department = AcademicDepartment.objects.create(name="Law")

    def _payload(self, **overrides):
        data = {
            'full_name': "Ngozi Student",
            'matric_no': 'LAW/2024/017',
            'academic_department': self.department.pk,
            'email': 'ngozi@school.edu',
            'password1': PASSWORD,
            'password2': PASSWORD,
        }
        data.update(overrides)
        return data

    def test_signup_creates_a_student_and_logs_them_in(self):
        response = self.client.post(reverse('signup'), self._payload())
        self.assertRedirects(response, STUDENT_HOME)

        user = User.objects.get(email='ngozi@school.edu')
        self.assertEqual(user.role, User.Role.STUDENT)
        self.assertEqual(user.matric_no, 'LAW/2024/017')
        self.assertTrue(user.check_password(PASSWORD))

    def test_role_cannot_be_chosen_by_the_person_signing_up(self):
        """The heart of requirement 4: posting role=admin must not produce an
        administrator."""
        for attempted_role in (User.Role.ADMIN, User.Role.HANDLER):
            with self.subTest(role=attempted_role):
                # Signing up logs you in, and the signup page turns a signed-in
                # visitor away — so start each pass as an anonymous visitor.
                self.client.logout()
                email = f"sneaky-{attempted_role}@school.edu"
                self.client.post(
                    reverse('signup'),
                    self._payload(
                        email=email,
                        matric_no=f"LAW/2024/{attempted_role[:3]}",
                        role=attempted_role,
                    ),
                )
                user = User.objects.get(email=email)
                self.assertEqual(user.role, User.Role.STUDENT)
                self.assertFalse(user.is_staff)
                self.assertFalse(user.is_superuser)

    def test_new_student_is_not_staff(self):
        self.client.post(reverse('signup'), self._payload())
        user = User.objects.get(email='ngozi@school.edu')
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)

    def test_matric_number_and_department_are_required(self):
        response = self.client.post(
            reverse('signup'), self._payload(matric_no='', academic_department='')
        )
        self.assertEqual(response.status_code, 200)
        self.assertFormError(response.context['form'], 'matric_no', "This field is required.")
        self.assertFormError(
            response.context['form'], 'academic_department', "This field is required."
        )

    def test_duplicate_email_is_rejected(self):
        self.client.post(reverse('signup'), self._payload())
        self.client.logout()
        response = self.client.post(
            reverse('signup'), self._payload(matric_no='LAW/2024/018')
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(User.objects.filter(email='ngozi@school.edu').count(), 1)

    def test_email_is_stored_lowercased(self):
        self.client.post(reverse('signup'), self._payload(email='Ngozi@School.edu'))
        self.assertTrue(User.objects.filter(email='ngozi@school.edu').exists())


class LogoutTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.student = User.objects.create_user(
            email='out@school.edu', password=PASSWORD, full_name="Logging Out"
        )

    def test_logout_posts_back_to_login(self):
        self.client.force_login(self.student)
        response = self.client.post(reverse('logout'))
        self.assertRedirects(response, reverse('login'))
        # And the session really is gone.
        self.assertRedirects(
            self.client.get(STUDENT_HOME),
            f"{reverse('login')}?next={STUDENT_HOME}",
        )

    def test_logout_refuses_a_get(self):
        """A GET must not log anyone out — see the comment on logout_view."""
        self.client.force_login(self.student)
        response = self.client.get(reverse('logout'))
        self.assertEqual(response.status_code, 405)
        self.assertEqual(self.client.get(STUDENT_HOME).status_code, 200)


class SignedInRedirectTests(TestCase):
    """Someone already signed in has no business on the login or signup page."""

    @classmethod
    def setUpTestData(cls):
        cls.student = User.objects.create_user(
            email='already@school.edu', password=PASSWORD, full_name="Already In"
        )

    def test_login_page_redirects_a_signed_in_user_home(self):
        self.client.force_login(self.student)
        self.assertRedirects(self.client.get(reverse('login')), STUDENT_HOME)

    def test_signup_page_redirects_a_signed_in_user_home(self):
        self.client.force_login(self.student)
        self.assertRedirects(self.client.get(reverse('signup')), STUDENT_HOME)

    def test_root_sends_anonymous_users_to_login(self):
        self.assertRedirects(self.client.get('/'), reverse('login'))


# ---------------------------------------------------------------------------
# Phase 3: the student side
# ---------------------------------------------------------------------------


class StudentComplaintTestCase(TestCase):
    """Shared setup: two students, a handler, and a complaint owned by each."""

    @classmethod
    def setUpTestData(cls):
        from .models import Category, Complaint, Unit

        cls.department = AcademicDepartment.objects.create(name="Computer Science")
        cls.unit = Unit.objects.create(name="ICT")
        cls.other_unit = Unit.objects.create(name="Bursary")
        cls.category = Category.objects.create(name="Student portal access", unit=cls.unit)

        cls.student = User.objects.create_user(
            email='ada@school.edu', password=PASSWORD, full_name="Ada Student",
            matric_no='CSC/2023/001', academic_department=cls.department,
        )
        cls.other_student = User.objects.create_user(
            email='bola@school.edu', password=PASSWORD, full_name="Bola Student",
            matric_no='CSC/2023/002', academic_department=cls.department,
        )
        cls.handler = User.objects.create_user(
            email='handler@school.edu', password=PASSWORD, full_name="Chidi Handler",
            role=User.Role.HANDLER, unit=cls.unit,
        )

        cls.mine = Complaint.objects.create(
            student=cls.student, category=cls.category,
            subject="Cannot log in to the portal",
            description="It rejects my matric number.",
        )
        cls.theirs = Complaint.objects.create(
            student=cls.other_student, category=cls.category,
            subject="Portal shows the wrong course list",
            description="Two courses are missing.",
        )


class OwnershipTests(StudentComplaintTestCase):
    """A student sees their own complaints and nobody else's."""

    def test_list_shows_only_my_complaints(self):
        self.client.force_login(self.student)
        response = self.client.get(STUDENT_HOME)
        listed = list(response.context['complaints'])
        self.assertEqual(listed, [self.mine])
        self.assertNotContains(response, self.theirs.reference_no)

    def test_opening_someone_elses_complaint_is_a_404(self):
        """Guessing a valid reference belonging to another student must not
        reveal that it exists."""
        self.client.force_login(self.student)
        response = self.client.get(f"/complaints/{self.theirs.reference_no}/")
        self.assertEqual(response.status_code, 404)

    def test_a_reference_that_does_not_exist_is_also_a_404(self):
        """Same response as someone else's complaint — the two cases are
        indistinguishable from outside."""
        self.client.force_login(self.student)
        self.assertEqual(self.client.get("/complaints/CMP-2026-9999/").status_code, 404)

    def test_cannot_reply_to_someone_elses_complaint(self):
        from .models import Message

        self.client.force_login(self.student)
        response = self.client.post(
            f"/complaints/{self.theirs.reference_no}/", {'body': "Injected reply"}
        )
        self.assertEqual(response.status_code, 404)
        self.assertFalse(Message.objects.filter(body="Injected reply").exists())

    def test_my_own_complaint_opens(self):
        self.client.force_login(self.student)
        response = self.client.get(f"/complaints/{self.mine.reference_no}/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Cannot log in to the portal")


class InternalMessageTests(StudentComplaintTestCase):
    """The single most important rule on the detail page."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        from .models import Message

        cls.public = Message.objects.create(
            complaint=cls.mine, author=cls.handler,
            body="We are looking into your portal account now.",
            is_internal=False,
        )
        cls.internal = Message.objects.create(
            complaint=cls.mine, author=cls.handler,
            body="SECRET: this student has complained three times this month.",
            is_internal=True,
        )

    def test_internal_notes_are_never_shown_to_the_student(self):
        self.client.force_login(self.student)
        response = self.client.get(f"/complaints/{self.mine.reference_no}/")

        self.assertContains(response, "We are looking into your portal account now.")
        self.assertNotContains(response, "SECRET")

    def test_internal_notes_are_not_even_fetched(self):
        """Excluded in the query, not hidden in the template — so there is
        nothing in the context a careless template edit could leak."""
        self.client.force_login(self.student)
        response = self.client.get(f"/complaints/{self.mine.reference_no}/")

        thread = list(response.context['thread'])
        self.assertEqual(thread, [self.public])
        self.assertNotIn(self.internal, thread)

    def test_a_student_reply_is_never_internal(self):
        from .models import Message

        self.client.force_login(self.student)
        self.client.post(
            f"/complaints/{self.mine.reference_no}/",
            {'body': "Thank you, still not working though.", 'is_internal': 'true'},
        )
        reply = Message.objects.get(body__startswith="Thank you")
        self.assertFalse(reply.is_internal)
        self.assertEqual(reply.author, self.student)


class ComplaintCreateTests(StudentComplaintTestCase):

    def test_filing_a_complaint_sets_up_everything(self):
        from .models import Complaint, Notification, StatusHistory

        self.client.force_login(self.student)
        response = self.client.post(reverse('complaint_create'), {
            'category': self.category.pk,
            'subject': "No water in Block C",
            'description': "The taps have been dry since Monday.",
            'priority': Complaint.Priority.HIGH,
        })

        complaint = Complaint.objects.get(subject="No water in Block C")

        # Owned by the person who filed it, and given a reference.
        self.assertEqual(complaint.student, self.student)
        self.assertTrue(complaint.reference_no.startswith('CMP-'))
        self.assertEqual(complaint.status, Complaint.Status.SUBMITTED)

        # The first history row exists and credits the student.
        history = StatusHistory.objects.filter(complaint=complaint)
        self.assertEqual(history.count(), 1)
        self.assertIsNone(history.first().old_status)
        self.assertEqual(history.first().new_status, Complaint.Status.SUBMITTED)
        self.assertEqual(history.first().changed_by, self.student)

        # A notification was created for the student.
        notification = Notification.objects.get(complaint=complaint)
        self.assertEqual(notification.user, self.student)
        self.assertIn(complaint.reference_no, notification.body)
        self.assertFalse(notification.is_read)

        # And they land on the detail page with the reference in a message.
        self.assertRedirects(response, f"/complaints/{complaint.reference_no}/")
        page = self.client.get(f"/complaints/{complaint.reference_no}/")
        self.assertContains(page, complaint.reference_no)

    def test_student_cannot_file_on_behalf_of_someone_else(self):
        from .models import Complaint

        self.client.force_login(self.student)
        self.client.post(reverse('complaint_create'), {
            'category': self.category.pk,
            'subject': "Filed for another",
            'description': "Trying to set the student field by hand.",
            'priority': Complaint.Priority.NORMAL,
            'student': self.other_student.pk,
            'status': Complaint.Status.RESOLVED,
            'assigned_to': self.handler.pk,
        })
        complaint = Complaint.objects.get(subject="Filed for another")
        self.assertEqual(complaint.student, self.student)
        self.assertEqual(complaint.status, Complaint.Status.SUBMITTED)
        self.assertIsNone(complaint.assigned_to)

    def test_attachment_is_saved(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        from .models import Complaint

        self.client.force_login(self.student)
        self.client.post(reverse('complaint_create'), {
            'category': self.category.pk,
            'subject': "Broken window",
            'description': "See the photo.",
            'priority': Complaint.Priority.NORMAL,
            'attachment': SimpleUploadedFile(
                'window.txt', b'pretend this is a photo', content_type='text/plain'
            ),
        })
        complaint = Complaint.objects.get(subject="Broken window")
        self.assertEqual(complaint.attachments.count(), 1)
        self.assertIn('window', complaint.attachments.first().file.name)

    def test_invalid_submission_creates_nothing(self):
        from .models import Complaint

        self.client.force_login(self.student)
        before = Complaint.objects.count()
        response = self.client.post(reverse('complaint_create'), {
            'category': '', 'subject': '', 'description': '', 'priority': '',
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Complaint.objects.count(), before)

    def test_category_dropdown_is_grouped_by_unit(self):
        from .models import Category

        Category.objects.create(name="School fees payment", unit=self.other_unit)
        self.client.force_login(self.student)
        response = self.client.get(reverse('complaint_create'))
        self.assertContains(response, '<optgroup label="ICT">', html=False)
        self.assertContains(response, '<optgroup label="Bursary">', html=False)


class StatusFilterTests(StudentComplaintTestCase):

    def test_filter_narrows_to_one_status(self):
        from .models import Complaint

        resolved = Complaint.objects.create(
            student=self.student, category=self.category,
            subject="Already sorted", description="Fixed.",
            status=Complaint.Status.RESOLVED,
        )
        self.client.force_login(self.student)

        response = self.client.get(STUDENT_HOME, {'status': Complaint.Status.RESOLVED})
        self.assertEqual(list(response.context['complaints']), [resolved])

        response = self.client.get(STUDENT_HOME, {'status': Complaint.Status.SUBMITTED})
        self.assertEqual(list(response.context['complaints']), [self.mine])

    def test_nonsense_status_falls_back_to_all(self):
        self.client.force_login(self.student)
        response = self.client.get(STUDENT_HOME, {'status': 'not-a-status'})
        self.assertEqual(response.context['selected_status'], '')
        self.assertEqual(list(response.context['complaints']), [self.mine])

    def test_empty_state_invites_a_first_complaint(self):
        from .models import Complaint

        Complaint.objects.filter(student=self.student).delete()
        self.client.force_login(self.student)
        response = self.client.get(STUDENT_HOME)
        self.assertContains(response, "File your first complaint")


class NotificationTests(StudentComplaintTestCase):

    def setUp(self):
        from .notifications import notify

        self.notification = notify(self.student, self.mine, "Your complaint was received.")

    def test_unread_count_appears_in_the_navbar(self):
        self.client.force_login(self.student)
        response = self.client.get(STUDENT_HOME)
        self.assertEqual(response.context['unread_notification_count'], 1)

    def test_opening_marks_read_and_goes_to_the_complaint(self):
        self.client.force_login(self.student)
        response = self.client.get(
            reverse('notification_open', args=[self.notification.pk])
        )
        self.assertRedirects(response, f"/complaints/{self.mine.reference_no}/")
        self.notification.refresh_from_db()
        self.assertTrue(self.notification.is_read)

    def test_cannot_open_someone_elses_notification(self):
        self.client.force_login(self.other_student)
        response = self.client.get(
            reverse('notification_open', args=[self.notification.pk])
        )
        self.assertEqual(response.status_code, 404)
        self.notification.refresh_from_db()
        self.assertFalse(self.notification.is_read)

    def test_list_shows_only_my_notifications(self):
        from .notifications import notify

        notify(self.other_student, self.theirs, "Not for Ada.")
        self.client.force_login(self.student)
        response = self.client.get(reverse('notification_list'))
        self.assertContains(response, "Your complaint was received.")
        self.assertNotContains(response, "Not for Ada.")

    def test_notifications_require_a_login(self):
        response = self.client.get(reverse('notification_list'))
        self.assertRedirects(
            response, f"{reverse('login')}?next={reverse('notification_list')}"
        )


# ---------------------------------------------------------------------------
# Phase 4: the handler side
# ---------------------------------------------------------------------------


class HandlerTestCase(TestCase):
    """Two units, a handler in each, an admin, and a complaint per unit."""

    @classmethod
    def setUpTestData(cls):
        from .models import Category, Complaint

        cls.dept = AcademicDepartment.objects.create(name="Computer Science")
        cls.ict = Unit.objects.create(name="ICT")
        cls.bursary = Unit.objects.create(name="Bursary")
        cls.ict_category = Category.objects.create(name="Portal access", unit=cls.ict)
        cls.bursary_category = Category.objects.create(name="Fees payment", unit=cls.bursary)

        cls.student = User.objects.create_user(
            email='ada@school.edu', password=PASSWORD, full_name="Ada Student",
            matric_no='CSC/2023/001', academic_department=cls.dept,
        )
        cls.ict_handler = User.objects.create_user(
            email='ict@school.edu', password=PASSWORD, full_name="Ife Handler",
            role=User.Role.HANDLER, unit=cls.ict,
        )
        cls.other_ict_handler = User.objects.create_user(
            email='ict2@school.edu', password=PASSWORD, full_name="Ola Handler",
            role=User.Role.HANDLER, unit=cls.ict,
        )
        cls.bursary_handler = User.objects.create_user(
            email='bursary@school.edu', password=PASSWORD, full_name="Bisi Handler",
            role=User.Role.HANDLER, unit=cls.bursary,
        )
        cls.admin = User.objects.create_user(
            email='admin@school.edu', password=PASSWORD, full_name="Chidi Admin",
            role=User.Role.ADMIN,
        )

        cls.ict_complaint = Complaint.objects.create(
            student=cls.student, category=cls.ict_category,
            subject="Portal will not accept my password",
            description="Locked out since Monday.",
        )
        cls.bursary_complaint = Complaint.objects.create(
            student=cls.student, category=cls.bursary_category,
            subject="Fee payment not reflecting",
            description="Paid last week, portal still says unpaid.",
        )

    def queue_url(self, complaint):
        return f"/queue/{complaint.reference_no}/"


class UnitScopingTests(HandlerTestCase):
    """A handler sees their own unit's complaints and no others."""

    def test_queue_lists_only_my_unit(self):
        self.client.force_login(self.ict_handler)
        response = self.client.get(HANDLER_QUEUE)
        self.assertEqual(list(response.context['complaints']), [self.ict_complaint])
        self.assertNotContains(response, self.bursary_complaint.reference_no)

    def test_other_unit_queue_is_scoped_too(self):
        self.client.force_login(self.bursary_handler)
        response = self.client.get(HANDLER_QUEUE)
        self.assertEqual(list(response.context['complaints']), [self.bursary_complaint])

    def test_opening_another_units_complaint_is_a_404(self):
        self.client.force_login(self.ict_handler)
        response = self.client.get(self.queue_url(self.bursary_complaint))
        self.assertEqual(response.status_code, 404)

    def test_a_reference_that_does_not_exist_is_also_a_404(self):
        self.client.force_login(self.ict_handler)
        self.assertEqual(self.client.get("/queue/CMP-2026-9999/").status_code, 404)

    def test_cannot_act_on_another_units_complaint(self):
        from .models import Complaint

        self.client.force_login(self.ict_handler)
        for payload in (
            {'action': 'assign'},
            {'action': 'status', 'status': Complaint.Status.RESOLVED},
            {'action': 'message', 'body': "Injected"},
        ):
            with self.subTest(action=payload['action']):
                response = self.client.post(
                    self.queue_url(self.bursary_complaint), payload
                )
                self.assertEqual(response.status_code, 404)

        self.bursary_complaint.refresh_from_db()
        self.assertIsNone(self.bursary_complaint.assigned_to)
        self.assertEqual(self.bursary_complaint.status, Complaint.Status.SUBMITTED)
        self.assertEqual(self.bursary_complaint.messages.count(), 0)

    def test_handler_with_no_unit_sees_nothing(self):
        """A broken account fails closed, not open."""
        stray = User.objects.create_user(
            email='stray@school.edu', password=PASSWORD, full_name="Stray Handler",
            role=User.Role.HANDLER,
        )
        self.client.force_login(stray)
        response = self.client.get(HANDLER_QUEUE)
        self.assertEqual(list(response.context['complaints']), [])
        self.assertEqual(self.client.get(self.queue_url(self.ict_complaint)).status_code, 404)

    def test_students_still_cannot_reach_the_queue(self):
        self.client.force_login(self.student)
        self.assertRedirects(self.client.get(HANDLER_QUEUE), STUDENT_HOME)
        self.assertRedirects(
            self.client.get(self.queue_url(self.ict_complaint)), STUDENT_HOME
        )


class AdminScopeTests(HandlerTestCase):
    """Admins use the same pages but are not filtered by unit."""

    def test_admin_sees_every_unit_in_the_queue(self):
        self.client.force_login(self.admin)
        response = self.client.get(HANDLER_QUEUE)
        listed = set(response.context['complaints'])
        self.assertEqual(listed, {self.ict_complaint, self.bursary_complaint})

    def test_admin_can_open_any_units_complaint(self):
        self.client.force_login(self.admin)
        for complaint in (self.ict_complaint, self.bursary_complaint):
            with self.subTest(ref=complaint.reference_no):
                response = self.client.get(self.queue_url(complaint))
                self.assertEqual(response.status_code, 200)


class QueueOrderingAndFilterTests(HandlerTestCase):

    def test_unassigned_first_then_oldest_first(self):
        from datetime import timedelta

        from django.utils import timezone
        from .models import Complaint

        older_assigned = Complaint.objects.create(
            student=self.student, category=self.ict_category,
            subject="Older, already claimed", description="x",
            assigned_to=self.ict_handler, status=Complaint.Status.ASSIGNED,
        )
        newer_unassigned = Complaint.objects.create(
            student=self.student, category=self.ict_category,
            subject="Newer, nobody has it", description="x",
        )
        # created_at is auto_now_add, so push the dates around by hand.
        now = timezone.now()
        Complaint.objects.filter(pk=older_assigned.pk).update(
            created_at=now - timedelta(days=30)
        )
        Complaint.objects.filter(pk=self.ict_complaint.pk).update(
            created_at=now - timedelta(days=10)
        )
        Complaint.objects.filter(pk=newer_unassigned.pk).update(created_at=now)

        self.client.force_login(self.ict_handler)
        response = self.client.get(HANDLER_QUEUE)
        order = [c.reference_no for c in response.context['complaints']]

        self.assertEqual(
            order,
            [
                self.ict_complaint.reference_no,   # unassigned, waiting 10 days
                newer_unassigned.reference_no,     # unassigned, waiting 0 days
                older_assigned.reference_no,       # assigned, so last
            ],
        )

    def test_unassigned_and_mine_filters(self):
        from .models import Complaint

        mine = Complaint.objects.create(
            student=self.student, category=self.ict_category,
            subject="Mine", description="x",
            assigned_to=self.ict_handler, status=Complaint.Status.ASSIGNED,
        )
        theirs = Complaint.objects.create(
            student=self.student, category=self.ict_category,
            subject="Someone else's", description="x",
            assigned_to=self.other_ict_handler, status=Complaint.Status.ASSIGNED,
        )
        self.client.force_login(self.ict_handler)

        unassigned = self.client.get(HANDLER_QUEUE, {'filter': 'unassigned'})
        self.assertEqual(list(unassigned.context['complaints']), [self.ict_complaint])

        assigned_to_me = self.client.get(HANDLER_QUEUE, {'filter': 'mine'})
        self.assertEqual(list(assigned_to_me.context['complaints']), [mine])
        self.assertNotIn(theirs, assigned_to_me.context['complaints'])

    def test_status_filter_and_nonsense_fallback(self):
        from .models import Complaint

        self.client.force_login(self.ict_handler)

        submitted = self.client.get(HANDLER_QUEUE, {'filter': Complaint.Status.SUBMITTED})
        self.assertEqual(list(submitted.context['complaints']), [self.ict_complaint])

        resolved = self.client.get(HANDLER_QUEUE, {'filter': Complaint.Status.RESOLVED})
        self.assertEqual(list(resolved.context['complaints']), [])

        bogus = self.client.get(HANDLER_QUEUE, {'filter': 'nonsense'})
        self.assertEqual(bogus.context['selected_filter'], '')
        self.assertEqual(list(bogus.context['complaints']), [self.ict_complaint])


class AssignmentTests(HandlerTestCase):

    def test_assign_to_me_claims_it_and_notifies_the_student(self):
        from .models import Complaint, Notification, StatusHistory

        self.client.force_login(self.ict_handler)
        response = self.client.post(
            self.queue_url(self.ict_complaint), {'action': 'assign'}
        )
        self.assertRedirects(response, self.queue_url(self.ict_complaint))

        self.ict_complaint.refresh_from_db()
        self.assertEqual(self.ict_complaint.assigned_to, self.ict_handler)
        self.assertEqual(self.ict_complaint.status, Complaint.Status.ASSIGNED)

        entry = StatusHistory.objects.filter(complaint=self.ict_complaint).last()
        self.assertEqual(entry.new_status, Complaint.Status.ASSIGNED)
        self.assertEqual(entry.changed_by, self.ict_handler)

        notification = Notification.objects.get(
            complaint=self.ict_complaint, user=self.student
        )
        self.assertIn("Ife Handler", notification.body)

    def test_cannot_assign_to_another_person(self):
        """The form carries no recipient; posting one changes nothing."""
        self.client.force_login(self.ict_handler)
        self.client.post(self.queue_url(self.ict_complaint), {
            'action': 'assign',
            'assigned_to': self.other_ict_handler.pk,
        })
        self.ict_complaint.refresh_from_db()
        self.assertEqual(self.ict_complaint.assigned_to, self.ict_handler)

    def test_assigning_an_already_assigned_complaint_does_not_steal_it(self):
        self.client.force_login(self.ict_handler)
        self.client.post(self.queue_url(self.ict_complaint), {'action': 'assign'})

        self.client.force_login(self.other_ict_handler)
        self.client.post(self.queue_url(self.ict_complaint), {'action': 'assign'})

        self.ict_complaint.refresh_from_db()
        self.assertEqual(self.ict_complaint.assigned_to, self.ict_handler)


class StatusChangeTests(HandlerTestCase):

    def test_each_button_moves_the_complaint_and_notifies(self):
        from .models import Complaint, Notification, StatusHistory

        self.client.force_login(self.ict_handler)
        for status in (
            Complaint.Status.IN_PROGRESS,
            Complaint.Status.RESOLVED,
            Complaint.Status.CLOSED,
        ):
            with self.subTest(status=status):
                self.client.post(
                    self.queue_url(self.ict_complaint),
                    {'action': 'status', 'status': status},
                )
                self.ict_complaint.refresh_from_db()
                self.assertEqual(self.ict_complaint.status, status)

                entry = StatusHistory.objects.filter(complaint=self.ict_complaint).last()
                self.assertEqual(entry.new_status, status)
                self.assertEqual(entry.changed_by, self.ict_handler)

        # One notification per change.
        self.assertEqual(
            Notification.objects.filter(
                complaint=self.ict_complaint, user=self.student
            ).count(),
            3,
        )

    def test_resolving_records_when(self):
        from .models import Complaint

        self.client.force_login(self.ict_handler)
        self.client.post(
            self.queue_url(self.ict_complaint),
            {'action': 'status', 'status': Complaint.Status.RESOLVED},
        )
        self.ict_complaint.refresh_from_db()
        self.assertIsNotNone(self.ict_complaint.resolved_at)

    def test_a_status_outside_the_buttons_is_refused(self):
        from .models import Complaint, StatusHistory

        self.client.force_login(self.ict_handler)
        before = StatusHistory.objects.filter(complaint=self.ict_complaint).count()
        self.client.post(
            self.queue_url(self.ict_complaint),
            {'action': 'status', 'status': 'deleted'},
        )
        self.ict_complaint.refresh_from_db()
        self.assertEqual(self.ict_complaint.status, Complaint.Status.SUBMITTED)
        self.assertEqual(
            StatusHistory.objects.filter(complaint=self.ict_complaint).count(), before
        )

    def test_setting_the_current_status_again_writes_no_history(self):
        from .models import Complaint, StatusHistory

        self.client.force_login(self.ict_handler)
        self.client.post(
            self.queue_url(self.ict_complaint),
            {'action': 'status', 'status': Complaint.Status.IN_PROGRESS},
        )
        before = StatusHistory.objects.filter(complaint=self.ict_complaint).count()
        self.client.post(
            self.queue_url(self.ict_complaint),
            {'action': 'status', 'status': Complaint.Status.IN_PROGRESS},
        )
        self.assertEqual(
            StatusHistory.objects.filter(complaint=self.ict_complaint).count(), before
        )


class HandlerMessageTests(HandlerTestCase):

    def test_public_reply_notifies_the_student(self):
        from .models import Message, Notification

        self.client.force_login(self.ict_handler)
        self.client.post(self.queue_url(self.ict_complaint), {
            'action': 'message',
            'body': "We have reset your portal password.",
        })

        message = Message.objects.get(body__startswith="We have reset")
        self.assertFalse(message.is_internal)
        self.assertEqual(message.author, self.ict_handler)
        self.assertEqual(
            Notification.objects.filter(
                complaint=self.ict_complaint, user=self.student
            ).count(),
            1,
        )

    def test_internal_note_does_not_notify_the_student(self):
        from .models import Message, Notification

        self.client.force_login(self.ict_handler)
        self.client.post(self.queue_url(self.ict_complaint), {
            'action': 'message',
            'body': "Third complaint from this student this month.",
            'is_internal': 'on',
        })

        message = Message.objects.get(body__startswith="Third complaint")
        self.assertTrue(message.is_internal)
        self.assertEqual(
            Notification.objects.filter(complaint=self.ict_complaint).count(), 0
        )

    def test_handler_sees_both_kinds_the_student_sees_only_one(self):
        """The same complaint, read from both sides."""
        from .models import Message

        public = Message.objects.create(
            complaint=self.ict_complaint, author=self.ict_handler,
            body="PUBLIC-we-are-on-it", is_internal=False,
        )
        internal = Message.objects.create(
            complaint=self.ict_complaint, author=self.ict_handler,
            body="INTERNALSECRET-escalate-this", is_internal=True,
        )

        self.client.force_login(self.ict_handler)
        staff_page = self.client.get(self.queue_url(self.ict_complaint))
        self.assertEqual(list(staff_page.context['thread']), [public, internal])
        self.assertContains(staff_page, "INTERNALSECRET")
        self.assertContains(staff_page, "Staff only")

        self.client.force_login(self.student)
        student_page = self.client.get(f"/complaints/{self.ict_complaint.reference_no}/")
        self.assertEqual(list(student_page.context['thread']), [public])
        self.assertNotContains(student_page, "INTERNALSECRET")

    def test_an_empty_message_is_rejected_and_creates_nothing(self):
        from .models import Message

        self.client.force_login(self.ict_handler)
        response = self.client.post(
            self.queue_url(self.ict_complaint), {'action': 'message', 'body': ''}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Message.objects.filter(complaint=self.ict_complaint).count(), 0)


# ---------------------------------------------------------------------------
# Phase 5: the admin dashboard
# ---------------------------------------------------------------------------

ADMIN_COMPLAINTS = '/dashboard/complaints/'


class DashboardTestCase(TestCase):
    """Two units with handlers, and a spread of complaints to count."""

    @classmethod
    def setUpTestData(cls):
        from datetime import timedelta

        from django.utils import timezone
        from .models import Category, Complaint

        cls.dept = AcademicDepartment.objects.create(name="Computer Science")
        cls.ict = Unit.objects.create(name="ICT")
        cls.bursary = Unit.objects.create(name="Bursary")
        cls.empty_unit = Unit.objects.create(name="Works & Maintenance")
        cls.ict_category = Category.objects.create(name="Portal access", unit=cls.ict)
        cls.bursary_category = Category.objects.create(name="Fees", unit=cls.bursary)

        cls.student = User.objects.create_user(
            email='ada@school.edu', password=PASSWORD, full_name="Ada Student",
            matric_no='CSC/2023/001', academic_department=cls.dept,
        )
        cls.ict_handler = User.objects.create_user(
            email='ict@school.edu', password=PASSWORD, full_name="Ife Handler",
            role=User.Role.HANDLER, unit=cls.ict,
        )
        cls.ict_handler2 = User.objects.create_user(
            email='ict2@school.edu', password=PASSWORD, full_name="Ola Handler",
            role=User.Role.HANDLER, unit=cls.ict,
        )
        cls.bursary_handler = User.objects.create_user(
            email='bursary@school.edu', password=PASSWORD, full_name="Bisi Handler",
            role=User.Role.HANDLER, unit=cls.bursary,
        )
        cls.admin = User.objects.create_user(
            email='admin@school.edu', password=PASSWORD, full_name="Chidi Admin",
            role=User.Role.ADMIN,
        )

        now = timezone.now()

        # Unassigned and waiting.
        cls.unassigned = Complaint.objects.create(
            student=cls.student, category=cls.ict_category,
            subject="Portal locked", description="x",
        )
        Complaint.objects.filter(pk=cls.unassigned.pk).update(
            created_at=now - timedelta(days=12)
        )

        # Assigned, but untouched for a long time -> stale.
        cls.stale = Complaint.objects.create(
            student=cls.student, category=cls.ict_category,
            subject="Wi-Fi down in Block C", description="x",
            assigned_to=cls.ict_handler, status=Complaint.Status.IN_PROGRESS,
        )
        Complaint.objects.filter(pk=cls.stale.pk).update(
            created_at=now - timedelta(days=20)
        )
        cls.stale.status_history.update(changed_at=now - timedelta(days=15))

        # Assigned and touched this morning -> in hand, not stale.
        cls.fresh = Complaint.objects.create(
            student=cls.student, category=cls.ict_category,
            subject="Email quota", description="x",
            assigned_to=cls.ict_handler, status=Complaint.Status.ASSIGNED,
        )
        Complaint.objects.filter(pk=cls.fresh.pk).update(
            created_at=now - timedelta(days=90)
        )

        # High priority, open.
        cls.urgent = Complaint.objects.create(
            student=cls.student, category=cls.bursary_category,
            subject="Fees paid but portal says unpaid",
            description="x", priority=Complaint.Priority.HIGH,
            assigned_to=cls.bursary_handler, status=Complaint.Status.ASSIGNED,
        )

        # Resolved four days after filing.
        cls.resolved = Complaint.objects.create(
            student=cls.student, category=cls.bursary_category,
            subject="Refund", description="x",
        )
        Complaint.objects.filter(pk=cls.resolved.pk).update(
            created_at=now - timedelta(days=4),
            resolved_at=now,
            status=Complaint.Status.RESOLVED,
        )


class DashboardAccessTests(DashboardTestCase):

    def test_only_admins_reach_the_dashboard(self):
        for url in (ADMIN_DASHBOARD, ADMIN_COMPLAINTS):
            with self.subTest(url=url):
                self.client.force_login(self.student)
                self.assertRedirects(self.client.get(url), STUDENT_HOME)

                self.client.force_login(self.ict_handler)
                self.assertRedirects(self.client.get(url), HANDLER_QUEUE)

                self.client.logout()
                self.assertRedirects(
                    self.client.get(url), f"{reverse('login')}?next={url}"
                )

                self.client.force_login(self.admin)
                self.assertEqual(self.client.get(url).status_code, 200)


class StatTileTests(DashboardTestCase):

    def test_headline_numbers(self):
        from . import stats

        result = stats.headline_stats()
        self.assertEqual(result['total'], 5)
        # Everything except the resolved one.
        self.assertEqual(result['open'], 4)
        self.assertEqual(result['high_priority_open'], 1)
        self.assertEqual(result['average_days_to_resolve'], 4.0)

    def test_average_ignores_unresolved_complaints(self):
        """Otherwise more outstanding work would make the average look better."""
        from . import stats
        from .models import Complaint

        Complaint.objects.create(
            student=self.student, category=self.ict_category,
            subject="Brand new", description="x",
        )
        self.assertEqual(stats.average_days_to_resolve(), 4.0)

    def test_average_is_none_when_nothing_is_resolved(self):
        from . import stats
        from .models import Complaint

        Complaint.objects.update(resolved_at=None)
        self.assertIsNone(stats.average_days_to_resolve())

    def test_high_priority_tile_excludes_resolved(self):
        from . import stats
        from .models import Complaint

        self.urgent.status = Complaint.Status.RESOLVED
        self.urgent.save()
        self.assertEqual(stats.headline_stats()['high_priority_open'], 0)

    def test_status_breakdown_includes_zeroes(self):
        from . import stats
        from .models import Complaint

        breakdown = dict((value, count) for value, _, count in stats.status_breakdown())
        self.assertEqual(len(breakdown), len(Complaint.Status.choices))
        self.assertEqual(breakdown[Complaint.Status.CLOSED], 0)
        self.assertEqual(breakdown[Complaint.Status.SUBMITTED], 1)
        self.assertEqual(breakdown[Complaint.Status.ASSIGNED], 2)
        self.assertEqual(breakdown[Complaint.Status.IN_PROGRESS], 1)
        self.assertEqual(breakdown[Complaint.Status.RESOLVED], 1)


class StaleCalculationTests(DashboardTestCase):

    def test_stale_uses_last_status_change_not_created_at(self):
        from . import stats

        stale = list(stats.stale_complaints())

        # Untouched for 15 days -> stale.
        self.assertIn(self.stale, stale)
        # Filed 90 days ago but its history row is from today -> not stale.
        self.assertNotIn(self.fresh, stale)
        # Unassigned complaints belong on their own list, not this one.
        self.assertNotIn(self.unassigned, stale)
        # Resolved work is finished, not stale.
        self.assertNotIn(self.resolved, stale)

    def test_touching_a_complaint_clears_it_from_stale(self):
        from . import stats
        from .models import Complaint

        self.assertIn(self.stale, stats.stale_complaints())

        self.client.force_login(self.ict_handler)
        self.client.post(f"/queue/{self.stale.reference_no}/", {
            'action': 'status', 'status': Complaint.Status.RESOLVED,
        })

        self.assertNotIn(self.stale, stats.stale_complaints())

    def test_last_change_is_annotated_for_the_template(self):
        from . import stats

        row = stats.stale_complaints().get(pk=self.stale.pk)
        newest = self.stale.status_history.order_by('-changed_at').first()
        self.assertEqual(row.last_change, newest.changed_at)


class AttentionListTests(DashboardTestCase):

    def test_unassigned_list(self):
        from . import stats

        self.assertEqual(list(stats.unassigned_complaints()), [self.unassigned])

    def test_high_priority_open_list(self):
        from . import stats

        self.assertEqual(list(stats.high_priority_open()), [self.urgent])

    def test_attention_rows_link_to_the_queue_detail_page(self):
        self.client.force_login(self.admin)
        response = self.client.get(ADMIN_DASHBOARD)
        self.assertContains(response, f"/queue/{self.unassigned.reference_no}/")
        self.assertContains(response, f"/queue/{self.stale.reference_no}/")
        self.assertContains(response, f"/queue/{self.urgent.reference_no}/")


class ChartDataTests(DashboardTestCase):

    def test_per_unit_counts_include_units_with_none(self):
        from . import stats

        data = stats.complaints_per_unit()
        counts = dict(zip(data['labels'], data['values']))
        self.assertEqual(counts['ICT'], 3)
        self.assertEqual(counts['Bursary'], 2)
        self.assertEqual(counts['Works & Maintenance'], 0)

    def test_by_month_returns_six_labelled_buckets(self):
        from . import stats

        data = stats.filed_vs_resolved_by_month(months=6)
        self.assertEqual(len(data['labels']), 6)
        self.assertEqual(len(data['filed']), 6)
        self.assertEqual(len(data['resolved']), 6)
        # This month: four filed recently, one resolved.
        self.assertEqual(data['resolved'][-1], 1)

    def test_chart_data_reaches_the_page_as_json(self):
        import json

        self.client.force_login(self.admin)
        response = self.client.get(ADMIN_DASHBOARD)

        self.assertContains(response, 'id="chart-data"')
        html = response.content.decode()
        start = html.index('id="chart-data"')
        payload = html[html.index('>', start) + 1:html.index('</script>', start)]
        data = json.loads(payload)

        self.assertIn('per_unit', data)
        self.assertIn('by_month', data)
        self.assertEqual(len(data['by_month']['labels']), 6)

    def test_chart_js_is_loaded_from_static_not_a_cdn(self):
        self.client.force_login(self.admin)
        response = self.client.get(ADMIN_DASHBOARD)
        self.assertContains(response, 'vendor/chartjs/chart.umd.min.js')
        self.assertNotContains(response, 'cdn.jsdelivr.net')


class AdminComplaintListTests(DashboardTestCase):

    def test_lists_every_unit(self):
        self.client.force_login(self.admin)
        response = self.client.get(ADMIN_COMPLAINTS)
        self.assertEqual(len(response.context['complaints']), 5)

    def test_filters_combine(self):
        from .models import Complaint

        self.client.force_login(self.admin)
        response = self.client.get(ADMIN_COMPLAINTS, {
            'unit': self.bursary.pk,
            'priority': Complaint.Priority.HIGH,
        })
        self.assertEqual(list(response.context['complaints']), [self.urgent])

    def test_search_matches_reference_subject_or_student(self):
        self.client.force_login(self.admin)

        for term in (self.urgent.reference_no, "portal says unpaid", "Ada"):
            with self.subTest(term=term):
                response = self.client.get(ADMIN_COMPLAINTS, {'q': term})
                self.assertIn(self.urgent, list(response.context['complaints']))

    def test_search_with_no_match_is_empty_not_everything(self):
        self.client.force_login(self.admin)
        response = self.client.get(ADMIN_COMPLAINTS, {'q': 'zzzz-no-such-thing'})
        self.assertEqual(list(response.context['complaints']), [])

    def test_unknown_filter_values_narrow_nothing(self):
        self.client.force_login(self.admin)
        response = self.client.get(ADMIN_COMPLAINTS, {
            'status': 'nonsense', 'unit': '9999', 'priority': 'urgent',
        })
        self.assertEqual(len(response.context['complaints']), 5)


class ReassignmentTests(DashboardTestCase):

    def queue_url(self, complaint):
        return f"/queue/{complaint.reference_no}/"

    def test_admin_reassigns_and_notifies_both_sides(self):
        from .models import Notification

        self.client.force_login(self.admin)
        response = self.client.post(self.queue_url(self.stale), {
            'action': 'reassign', 'handler': self.ict_handler2.pk,
        })
        self.assertRedirects(response, self.queue_url(self.stale))

        self.stale.refresh_from_db()
        self.assertEqual(self.stale.assigned_to, self.ict_handler2)

        student_note = Notification.objects.get(complaint=self.stale, user=self.student)
        self.assertIn("Ola Handler", student_note.body)

        handler_note = Notification.objects.get(
            complaint=self.stale, user=self.ict_handler2
        )
        self.assertIn("Chidi Admin", handler_note.body)

    def test_reassignment_is_recorded_in_status_history(self):
        from .models import StatusHistory

        before = StatusHistory.objects.filter(complaint=self.stale).count()
        self.client.force_login(self.admin)
        self.client.post(self.queue_url(self.stale), {
            'action': 'reassign', 'handler': self.ict_handler2.pk,
        })
        entries = StatusHistory.objects.filter(complaint=self.stale)
        self.assertEqual(entries.count(), before + 1)
        self.assertEqual(entries.last().changed_by, self.admin)

    def test_reassigning_a_submitted_complaint_also_marks_it_assigned(self):
        from .models import Complaint, StatusHistory

        self.client.force_login(self.admin)
        self.client.post(self.queue_url(self.unassigned), {
            'action': 'reassign', 'handler': self.ict_handler.pk,
        })
        self.unassigned.refresh_from_db()
        self.assertEqual(self.unassigned.status, Complaint.Status.ASSIGNED)
        self.assertEqual(self.unassigned.assigned_to, self.ict_handler)

        entry = StatusHistory.objects.filter(complaint=self.unassigned).last()
        self.assertEqual(entry.old_status, Complaint.Status.SUBMITTED)
        self.assertEqual(entry.new_status, Complaint.Status.ASSIGNED)

    def test_reassigning_in_progress_does_not_reset_the_status(self):
        from .models import Complaint

        self.client.force_login(self.admin)
        self.client.post(self.queue_url(self.stale), {
            'action': 'reassign', 'handler': self.ict_handler2.pk,
        })
        self.stale.refresh_from_db()
        self.assertEqual(self.stale.status, Complaint.Status.IN_PROGRESS)

    def test_cannot_reassign_to_a_handler_in_another_unit(self):
        self.client.force_login(self.admin)
        self.client.post(self.queue_url(self.stale), {
            'action': 'reassign', 'handler': self.bursary_handler.pk,
        })
        self.stale.refresh_from_db()
        self.assertEqual(self.stale.assigned_to, self.ict_handler)

    def test_cannot_reassign_to_a_student_or_an_admin(self):
        self.client.force_login(self.admin)
        for target in (self.student, self.admin):
            with self.subTest(role=target.role):
                self.client.post(self.queue_url(self.stale), {
                    'action': 'reassign', 'handler': target.pk,
                })
                self.stale.refresh_from_db()
                self.assertEqual(self.stale.assigned_to, self.ict_handler)

    def test_handler_cannot_reassign(self):
        """The dropdown is absent from their page, and the action is refused."""
        self.client.force_login(self.ict_handler)

        page = self.client.get(self.queue_url(self.stale))
        self.assertIsNone(page.context['assignable_handlers'])
        self.assertNotContains(page, 'value="reassign"')

        self.client.post(self.queue_url(self.stale), {
            'action': 'reassign', 'handler': self.ict_handler2.pk,
        })
        self.stale.refresh_from_db()
        self.assertEqual(self.stale.assigned_to, self.ict_handler)

    def test_admin_sees_only_this_units_handlers_in_the_dropdown(self):
        self.client.force_login(self.admin)
        page = self.client.get(self.queue_url(self.stale))
        offered = list(page.context['assignable_handlers'])
        self.assertEqual(offered, [self.ict_handler, self.ict_handler2])


# ---------------------------------------------------------------------------
# Regression: creating staff in the Django admin
# ---------------------------------------------------------------------------


class AdminUserCreationTests(TestCase):
    """
    `User.clean()` enforces which fields go with which role, and a ModelForm
    can only report an error against a field it actually has. When the add-user
    form was missing `unit`, creating a handler crashed with

        ValueError: 'UserForm' has no field named 'unit'

    instead of showing "A handler must belong to a unit". The fix was to put
    the role-specific fields on the add form; these tests hold that in place.
    """

    ADD_URL = '/admin/complaints/user/add/'

    @classmethod
    def setUpTestData(cls):
        cls.unit = Unit.objects.create(name="ICT")
        cls.department = AcademicDepartment.objects.create(name="Computer Science")
        cls.superuser = User.objects.create_superuser(
            email='root@school.edu', password=PASSWORD, full_name="Root Admin",
        )

    def setUp(self):
        self.client.force_login(self.superuser)

    def add(self, **overrides):
        """POST the add-user form with everything blank except what is given."""
        data = {
            'email': 'someone@school.edu',
            'full_name': "Someone",
            'role': User.Role.STUDENT,
            'matric_no': '',
            'academic_department': '',
            'unit': '',
            'password1': PASSWORD,
            'password2': PASSWORD,
        }
        data.update(overrides)
        return self.client.post(self.ADD_URL, data)

    def form_errors(self, response):
        return dict(response.context['adminform'].form.errors)

    # -- The reported crash ------------------------------------------------

    def test_creating_a_handler_without_a_unit_shows_an_error_not_a_crash(self):
        response = self.add(
            email='handler@school.edu', full_name="Ife Handler",
            role=User.Role.HANDLER,
        )
        # A re-rendered form, not a 302 and not an exception.
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            self.form_errors(response),
            {'unit': ["A handler must belong to a unit."]},
        )
        self.assertFalse(User.objects.filter(email='handler@school.edu').exists())

    def test_creating_a_handler_with_a_unit_succeeds(self):
        response = self.add(
            email='handler@school.edu', full_name="Ife Handler",
            role=User.Role.HANDLER, unit=self.unit.pk,
        )
        self.assertEqual(response.status_code, 302)

        handler = User.objects.get(email='handler@school.edu')
        self.assertEqual(handler.role, User.Role.HANDLER)
        self.assertEqual(handler.unit, self.unit)
        self.assertIsNone(handler.matric_no)
        # And the account is actually usable.
        self.assertTrue(handler.check_password(PASSWORD))

    # -- The student case asked about --------------------------------------

    def test_creating_a_student_without_an_academic_department_succeeds(self):
        """
        The same crash cannot happen here: `User.clean()` never requires a
        department. The column is optional on the model, and it is the *signup
        form* — not the model — that insists on one for self-registration. So
        an administrator may create a student and fill the department in later.
        """
        response = self.add(
            email='student@school.edu', full_name="Ada Student",
            role=User.Role.STUDENT,
        )
        self.assertEqual(response.status_code, 302)

        student = User.objects.get(email='student@school.edu')
        self.assertEqual(student.role, User.Role.STUDENT)
        self.assertIsNone(student.academic_department)
        self.assertIsNone(student.matric_no)

    def test_creating_a_student_with_a_unit_shows_an_error_not_a_crash(self):
        """The other direction of the same rule, and the other way `unit`
        can be flagged."""
        response = self.add(
            email='student@school.edu', full_name="Ada Student",
            role=User.Role.STUDENT, unit=self.unit.pk,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            self.form_errors(response),
            {'unit': ["Students do not belong to a unit."]},
        )

    # -- The remaining pairings clean() can flag ---------------------------

    def test_staff_with_student_only_fields_show_errors_not_crashes(self):
        cases = [
            (
                "admin with a matric number",
                {'role': User.Role.ADMIN, 'matric_no': 'ADM/1'},
                {'matric_no': ["Only students have a matriculation number."]},
            ),
            (
                "admin with a department",
                {'role': User.Role.ADMIN, 'academic_department': self.department.pk},
                {'academic_department': ["Only students have an academic department."]},
            ),
            (
                "handler with a matric number",
                {
                    'role': User.Role.HANDLER,
                    'unit': self.unit.pk,
                    'matric_no': 'HND/1',
                },
                {'matric_no': ["Only students have a matriculation number."]},
            ),
            (
                "handler with a department",
                {
                    'role': User.Role.HANDLER,
                    'unit': self.unit.pk,
                    'academic_department': self.department.pk,
                },
                {'academic_department': ["Only students have an academic department."]},
            ),
        ]
        for name, payload, expected in cases:
            with self.subTest(case=name):
                response = self.add(
                    email=f"{name.replace(' ', '-')}@school.edu",
                    full_name="Test Person",
                    **payload,
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(self.form_errors(response), expected)

    # -- The invariant that stops this coming back -------------------------

    def test_every_field_user_clean_can_flag_is_on_the_add_form(self):
        """
        The general rule behind the fix, rather than one more example of it.

        `User.clean()` is asked to validate every invalid role/field pairing it
        knows about, and the field names it complains about are collected. Each
        one has to exist on the add-user form — otherwise Django cannot attach
        the error and raises ValueError instead.

        Written this way, adding a new rule to `clean()` and forgetting to add
        its field to `add_fieldsets` fails here, naming the missing field,
        rather than crashing an administrator on a Tuesday.
        """
        from django.core.exceptions import ValidationError

        invalid_users = [
            User(role=User.Role.HANDLER),                        # no unit
            User(role=User.Role.HANDLER, unit=self.unit,
                 matric_no='X/1', academic_department=self.department),
            User(role=User.Role.STUDENT, unit=self.unit),
            User(role=User.Role.ADMIN, matric_no='X/2',
                 academic_department=self.department),
        ]

        flagged = set()
        for user in invalid_users:
            try:
                user.clean()
            except ValidationError as error:
                flagged.update(error.error_dict)

        # Sanity: clean() really did object to something, so an empty set
        # cannot make this test pass by accident.
        self.assertEqual(flagged, {'unit', 'matric_no', 'academic_department'})

        form_fields = set(self.client.get(self.ADD_URL).context['adminform'].form.fields)
        missing = flagged - form_fields
        self.assertEqual(
            missing, set(),
            f"User.clean() can flag {sorted(missing)}, which the add-user form "
            f"has no field for — creating such a user will raise ValueError.",
        )

    def test_add_form_shows_the_expected_fields(self):
        response = self.client.get(self.ADD_URL)
        fields = list(response.context['adminform'].form.fields)
        for expected in [
            'email', 'full_name', 'role',
            'matric_no', 'academic_department', 'unit',
            'password1', 'password2',
        ]:
            with self.subTest(field=expected):
                self.assertIn(expected, fields)
