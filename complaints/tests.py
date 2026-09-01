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
