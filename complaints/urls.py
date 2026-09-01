"""
The app's URLs.

The three dashboards sit at the top level (/complaints/, /queue/, /dashboard/)
rather than under a prefix, because they are what each kind of user thinks of
as "the site". The names on the right are what the rest of the code uses —
`redirect('login')`, `{% url 'student_home' %}` — so these paths can be changed
in this one file without breaking anything else.
"""

from django.urls import path

from . import views

urlpatterns = [
    path('', views.root_view, name='root'),

    # Accounts
    path('login/', views.login_view, name='login'),
    path('signup/', views.signup_view, name='signup'),
    path('logout/', views.logout_view, name='logout'),

    # The three dashboards, one per role.
    path('complaints/', views.student_home, name='student_home'),
    path('queue/', views.handler_queue, name='handler_queue'),
    path('dashboard/', views.admin_dashboard, name='admin_dashboard'),
]
