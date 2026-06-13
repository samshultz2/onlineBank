from django.urls import path

from . import views

app_name = "notifications"

urlpatterns = [
    path("", views.notification_list, name="list"),
    path("read-all/", views.mark_all_read, name="mark_all_read"),
    path("delete-read/", views.delete_all_read, name="delete_all_read"),
    path("<int:pk>/read/", views.mark_read, name="mark_read"),
    path("<int:pk>/delete/", views.delete_notification, name="delete_notification"),
]
