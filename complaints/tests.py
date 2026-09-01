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
