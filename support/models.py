from django.conf import settings
from django.db import models


class Ticket(models.Model):
    class Status(models.TextChoices):
        OPEN = "OPEN", "Open"
        IN_PROGRESS = "IN_PROGRESS", "In progress"
        RESOLVED = "RESOLVED", "Resolved"
        CLOSED = "CLOSED", "Closed"

    class Category(models.TextChoices):
        TRANSACTION = "TRANSACTION", "Transaction dispute"
        CARD = "CARD", "Card issue"
        LOAN = "LOAN", "Loan enquiry"
        ACCOUNT = "ACCOUNT", "Account issue"
        OTHER = "OTHER", "Other"

    class Priority(models.TextChoices):
        LOW = "LOW", "Low"
        NORMAL = "NORMAL", "Normal"
        HIGH = "HIGH", "High"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="tickets"
    )
    subject = models.CharField(max_length=200)
    category = models.CharField(
        max_length=20, choices=Category.choices, default=Category.OTHER
    )
    priority = models.CharField(
        max_length=10, choices=Priority.choices, default=Priority.NORMAL
    )
    status = models.CharField(max_length=15, choices=Status.choices, default=Status.OPEN)
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_tickets",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self):
        return f"#{self.pk} {self.subject}"


class TicketMessage(models.Model):
    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name="messages")
    sender = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    body = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"Message on {self.ticket} by {self.sender}"
