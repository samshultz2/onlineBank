from django.core.mail import send_mail


def notify(user, title, message, level="info", send_email=True):
    """Create an in-app notification and mirror it by email (console backend
    in development)."""
    from .models import Notification

    notification = Notification.objects.create(
        user=user, title=title, message=message, level=level
    )
    if send_email and user.email:
        send_mail(title, message, None, [user.email], fail_silently=True)
    return notification
