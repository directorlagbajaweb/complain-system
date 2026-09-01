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
    path(
        'dashboard/complaints/',
        views.admin_complaint_list,
        name='admin_complaint_list',
    ),

    # The student's complaints.
    #
    # 'new/' has to be listed before '<str:reference_no>/', because Django
    # tries these patterns in order and '<str:...>' would happily swallow the
    # word "new" and go looking for a complaint with that reference.
    #
    # Complaints are addressed by reference_no, not by primary key. The
    # reference is the number the student already has written down and quotes
    # at the counter, so /complaints/CMP-2026-0004/ is a URL they can recognise
    # — and it does not publish how many complaints the system holds, which
    # sequential ids in the address bar would.
    path('complaints/new/', views.complaint_create, name='complaint_create'),
    path(
        'complaints/<str:reference_no>/',
        views.complaint_detail,
        name='complaint_detail',
    ),

    # The handler's queue. Same reasoning as the student detail URL: addressed
    # by reference_no, and there is no 'queue/new/' to collide with because
    # staff do not file complaints.
    path(
        'queue/<str:reference_no>/',
        views.queue_detail,
        name='queue_detail',
    ),

    # Notifications.
    path('notifications/', views.notification_list, name='notification_list'),
    path(
        'notifications/<int:pk>/',
        views.notification_open,
        name='notification_open',
    ),
]
