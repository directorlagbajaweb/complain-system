"""
URL configuration for cms project.

Everything the students and staff use comes from the `complaints` app, mounted
at the root. The Django admin sits at /admin/ and is where handler and
administrator accounts get created.
"""

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', include('complaints.urls')),
]

# Uploaded files (complaint attachments) are served by Django itself while
# DEBUG is on. In production a real web server does this instead, which is why
# it is switched off here when DEBUG is False rather than left running.
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
