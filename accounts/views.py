from django.contrib import messages
from django.contrib.auth import login as auth_login
from django.contrib.auth import logout as auth_logout
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib.auth.views import (
    PasswordResetCompleteView,
    PasswordResetConfirmView,
    PasswordResetDoneView,
    PasswordResetView,
)
from django.shortcuts import redirect, render
from django.urls import reverse_lazy

from banking.models import AccountType, BankAccount

from .decorators import customer_required
from .forms import (
    ChangePinForm,
    KycDocumentForm,
    LoginForm,
    ProfileUpdateForm,
    RegistrationForm,
    SetPinForm,
)
from .models import log_action


def home(request):
    if request.user.is_authenticated:
        if request.user.is_bank_staff:
            return redirect("staffportal:dashboard")
        return redirect("banking:dashboard")
    return render(request, "home.html")


def register(request):
    if request.user.is_authenticated:
        return redirect("accounts:home")
    if request.method == "POST":
        form = RegistrationForm(request.POST)
        if form.is_valid():
            user = form.save()
            # Open a default savings account immediately.
            default_type = (
                AccountType.objects.filter(is_active=True, code="SAV").first()
                or AccountType.objects.filter(is_active=True).first()
            )
            if default_type:
                BankAccount.objects.create(user=user, account_type=default_type)
            auth_login(request, user)
            log_action(user, "REGISTER", "Customer self-registration", request)
            messages.success(
                request,
                "Welcome! Your application has been received. Your account is "
                "awaiting approval by our team — set your transaction PIN now so "
                "you're ready to bank as soon as it's activated.",
            )
            return redirect("accounts:set_pin")
    else:
        form = RegistrationForm()
    return render(request, "auth/register.html", {"form": form})


def login_view(request):
    if request.user.is_authenticated:
        return redirect("accounts:home")
    if request.method == "POST":
        form = LoginForm(request, request.POST)
        if form.is_valid():
            auth_login(request, form.user)
            log_action(form.user, "LOGIN", "Successful login", request)
            next_url = request.GET.get("next")
            if next_url and next_url.startswith("/"):
                return redirect(next_url)
            if form.user.is_bank_staff:
                return redirect("staffportal:dashboard")
            return redirect("banking:dashboard")
    else:
        form = LoginForm(request)
    return render(request, "auth/login.html", {"form": form})


def logout_view(request):
    if request.user.is_authenticated:
        log_action(request.user, "LOGOUT", "User logged out", request)
    auth_logout(request)
    messages.info(request, "You have been signed out.")
    return redirect("accounts:login")


@customer_required
def set_pin(request):
    profile = request.user.profile
    if request.method == "POST":
        form = SetPinForm(request.POST)
        if form.is_valid():
            profile.set_pin(form.cleaned_data["pin"])
            log_action(request.user, "PIN_SET", "Transaction PIN set", request)
            messages.success(request, "Your transaction PIN has been set.")
            return redirect("banking:dashboard")
    else:
        form = SetPinForm()
    return render(
        request, "auth/set_pin.html", {"form": form, "has_pin": profile.has_pin}
    )


@customer_required
def change_pin(request):
    profile = request.user.profile
    if not profile.has_pin:
        return redirect("accounts:set_pin")
    if request.method == "POST":
        form = ChangePinForm(profile, request.POST)
        if form.is_valid():
            profile.set_pin(form.cleaned_data["pin"])
            log_action(request.user, "PIN_CHANGE", "Transaction PIN changed", request)
            messages.success(request, "Your transaction PIN has been changed.")
            return redirect("accounts:profile")
    else:
        form = ChangePinForm(profile)
    return render(request, "auth/change_pin.html", {"form": form})


@customer_required
def profile(request):
    user_profile = request.user.profile
    if request.method == "POST":
        form = ProfileUpdateForm(request.POST, request.FILES, instance=user_profile)
        if form.is_valid():
            form.save()
            log_action(request.user, "PROFILE_UPDATE", "Profile details updated", request)
            messages.success(request, "Your profile has been updated.")
            return redirect("accounts:profile")
    else:
        form = ProfileUpdateForm(instance=user_profile)
    return render(
        request,
        "customer/profile.html",
        {"form": form, "profile": user_profile},
    )


@customer_required
def upload_kyc(request):
    user_profile = request.user.profile
    if request.method == "POST":
        form = KycDocumentForm(request.POST, request.FILES, instance=user_profile)
        if form.is_valid():
            user_profile = form.save(commit=False)
            user_profile.kyc_status = user_profile.KycStatus.PENDING
            user_profile.kyc_rejection_reason = ""
            user_profile.save()
            log_action(request.user, "KYC_UPLOAD", "KYC documents submitted", request)
            messages.success(
                request, "Documents submitted. Our team will review them shortly."
            )
            return redirect("accounts:profile")
    else:
        form = KycDocumentForm(instance=user_profile)
    return render(request, "customer/upload_kyc.html", {"form": form})


@customer_required
def change_password(request):
    if request.method == "POST":
        form = PasswordChangeForm(request.user, request.POST)
        if form.is_valid():
            user = form.save()
            update_session_auth_hash(request, user)
            log_action(request.user, "PASSWORD_CHANGE", "Password changed", request)
            messages.success(request, "Your password has been changed.")
            return redirect("accounts:profile")
    else:
        form = PasswordChangeForm(request.user)
    for field in form.fields.values():
        field.widget.attrs["class"] = "form-control"
    return render(request, "auth/change_password.html", {"form": form})


class BankPasswordResetView(PasswordResetView):
    template_name = "auth/password_reset.html"
    email_template_name = "auth/password_reset_email.html"
    success_url = reverse_lazy("accounts:password_reset_done")


class BankPasswordResetDoneView(PasswordResetDoneView):
    template_name = "auth/password_reset_done.html"


class BankPasswordResetConfirmView(PasswordResetConfirmView):
    template_name = "auth/password_reset_confirm.html"
    success_url = reverse_lazy("accounts:password_reset_complete")


class BankPasswordResetCompleteView(PasswordResetCompleteView):
    template_name = "auth/password_reset_complete.html"
