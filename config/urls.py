from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("django-admin/", admin.site.urls),
    path("", include("accounts.urls")),
    path("bank/", include("banking.urls")),
    path("cards/", include("cards.urls")),
    path("loans/", include("loans.urls")),
    path("support/", include("support.urls")),
    path("notifications/", include("notifications.urls")),
    path("staff/", include("staffportal.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
