from django import forms
from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render

from accounts.decorators import customer_required
from accounts.forms import StyledFormMixin
from accounts.models import log_action

from .models import Ticket, TicketMessage


class TicketForm(StyledFormMixin, forms.ModelForm):
    message = forms.CharField(widget=forms.Textarea(attrs={"rows": 4}))

    class Meta:
        model = Ticket
        fields = ("subject", "category", "priority")


class ReplyForm(StyledFormMixin, forms.Form):
    body = forms.CharField(
        label="Your reply", widget=forms.Textarea(attrs={"rows": 3})
    )


@customer_required
def ticket_list(request):
    if request.method == "POST":
        form = TicketForm(request.POST)
        if form.is_valid():
            ticket = form.save(commit=False)
            ticket.user = request.user
            ticket.save()
            TicketMessage.objects.create(
                ticket=ticket, sender=request.user,
                body=form.cleaned_data["message"],
            )
            log_action(request.user, "TICKET_OPEN", f"Ticket #{ticket.pk}", request)
            messages.success(request, "Your ticket has been submitted.")
            return redirect("support:detail", pk=ticket.pk)
    else:
        form = TicketForm()
    tickets = request.user.tickets.all()
    return render(request, "customer/tickets.html", {"tickets": tickets, "form": form})


@customer_required
def ticket_detail(request, pk):
    ticket = get_object_or_404(Ticket, pk=pk, user=request.user)
    if request.method == "POST":
        form = ReplyForm(request.POST)
        if ticket.status == Ticket.Status.CLOSED:
            messages.error(request, "This ticket is closed. Open a new one if you still need help.")
        elif form.is_valid():
            TicketMessage.objects.create(
                ticket=ticket, sender=request.user, body=form.cleaned_data["body"]
            )
            if ticket.status == Ticket.Status.RESOLVED:
                ticket.status = Ticket.Status.OPEN
            ticket.save()  # bump updated_at
            messages.success(request, "Reply sent.")
            return redirect("support:detail", pk=pk)
    else:
        form = ReplyForm()
    return render(request, "customer/ticket_detail.html", {
        "ticket": ticket, "form": form, "thread": ticket.messages.select_related("sender"),
    })
