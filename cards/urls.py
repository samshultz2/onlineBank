from django.urls import path

from . import views

app_name = "cards"

urlpatterns = [
    path("", views.card_list, name="list"),
    path("<int:pk>/freeze/", views.toggle_freeze, name="toggle_freeze"),
    path("<int:pk>/block/", views.block_card, name="block"),
]
