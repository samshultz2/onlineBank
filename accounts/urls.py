from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    path("", views.home, name="home"),
    path("register/", views.register, name="register"),
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("pin/set/", views.set_pin, name="set_pin"),
    path("pin/change/", views.change_pin, name="change_pin"),
    path("profile/", views.profile, name="profile"),
    path("profile/kyc/", views.upload_kyc, name="upload_kyc"),
    path("password/change/", views.change_password, name="change_password"),
    path("password/reset/", views.BankPasswordResetView.as_view(), name="password_reset"),
    path("password/reset/done/", views.BankPasswordResetDoneView.as_view(), name="password_reset_done"),
    path("password/reset/<uidb64>/<token>/", views.BankPasswordResetConfirmView.as_view(), name="password_reset_confirm"),
    path("password/reset/complete/", views.BankPasswordResetCompleteView.as_view(), name="password_reset_complete"),
]
