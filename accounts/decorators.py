from functools import wraps

from django.contrib import messages
from django.shortcuts import redirect

from .models import User


def customer_required(view_func):
    """Customer-facing pages: requires login as a customer with a profile."""

    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect("accounts:login")
        if request.user.is_bank_staff:
            return redirect("staffportal:dashboard")
        if not hasattr(request.user, "profile"):
            messages.error(request, "Your customer profile is incomplete. Contact support.")
            return redirect("accounts:login")
        return view_func(request, *args, **kwargs)

    return wrapper


def pin_setup_required(view_func):
    """Money-moving pages additionally require a transaction PIN on file."""

    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.profile.has_pin:
            messages.warning(
                request, "Set your transaction PIN before performing transactions."
            )
            return redirect("accounts:set_pin")
        return view_func(request, *args, **kwargs)

    return customer_required(wrapper)


def staff_required(*roles):
    """Staff portal pages. Optionally restrict to specific roles, e.g.
    @staff_required(User.Role.MANAGER, User.Role.ADMIN)."""
    allowed = set(roles) or {User.Role.TELLER, User.Role.MANAGER, User.Role.ADMIN}

    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect("accounts:login")
            if not request.user.is_bank_staff:
                return redirect("banking:dashboard")
            if request.user.role not in allowed:
                messages.error(request, "You do not have permission to access that page.")
                return redirect("staffportal:dashboard")
            return view_func(request, *args, **kwargs)

        return wrapper

    return decorator
