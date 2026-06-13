from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from accounts.decorators import customer_required

from .models import Notification


@customer_required
def notification_list(request):
    notifications = request.user.notifications.all()
    paginator = Paginator(notifications, 20)
    page = paginator.get_page(request.GET.get("page"))
    return render(request, "customer/notifications.html", {"notifications": page})


@customer_required
@require_POST
def mark_all_read(request):
    request.user.notifications.filter(is_read=False).update(is_read=True)
    return redirect("notifications:list")


@customer_required
@require_POST
def mark_read(request, pk):
    request.user.notifications.filter(pk=pk).update(is_read=True)
    return redirect("notifications:list")


@customer_required
@require_POST
def delete_notification(request, pk):
    get_object_or_404(Notification, pk=pk, user=request.user).delete()
    return redirect("notifications:list")


@customer_required
@require_POST
def delete_all_read(request):
    request.user.notifications.filter(is_read=True).delete()
    return redirect("notifications:list")
