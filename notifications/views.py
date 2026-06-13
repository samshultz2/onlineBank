from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.shortcuts import redirect, render


@login_required
def notification_list(request):
    notifications = request.user.notifications.all()
    paginator = Paginator(notifications, 20)
    page = paginator.get_page(request.GET.get("page"))
    return render(request, "customer/notifications.html", {"notifications": page})


@login_required
def mark_all_read(request):
    if request.method == "POST":
        request.user.notifications.filter(is_read=False).update(is_read=True)
    return redirect("notifications:list")


@login_required
def mark_read(request, pk):
    if request.method == "POST":
        request.user.notifications.filter(pk=pk).update(is_read=True)
    return redirect("notifications:list")
