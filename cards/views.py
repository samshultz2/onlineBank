from django import forms
from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render

from accounts.decorators import customer_required, pin_setup_required
from accounts.forms import StyledFormMixin
from accounts.models import log_action
from banking.models import BankAccount
from notifications.services import notify

from .models import Card


class CardRequestForm(StyledFormMixin, forms.Form):
    account = forms.ModelChoiceField(queryset=BankAccount.objects.none())
    scheme = forms.ChoiceField(choices=Card.Scheme.choices)

    def __init__(self, user, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["account"].queryset = user.bank_accounts.filter(
            status=BankAccount.Status.ACTIVE
        )
        self.fields["account"].label_from_instance = (
            lambda a: f"{a.account_number} - {a.account_type.name}"
        )


@customer_required
def card_list(request):
    cards = request.user.cards.select_related("account")
    if request.method == "POST":
        form = CardRequestForm(request.user, request.POST)
        if form.is_valid():
            account = form.cleaned_data["account"]
            if account.cards.exclude(
                status__in=[Card.Status.BLOCKED, Card.Status.EXPIRED]
            ).filter(scheme=form.cleaned_data["scheme"]).exists():
                messages.error(
                    request,
                    "You already have an active or pending card of this scheme "
                    "on that account.",
                )
            else:
                Card.objects.create(
                    user=request.user,
                    account=account,
                    scheme=form.cleaned_data["scheme"],
                )
                log_action(request.user, "CARD_REQUEST",
                           f"{form.cleaned_data['scheme']} card on "
                           f"{account.account_number}", request)
                messages.success(
                    request,
                    "Card request submitted. You will be notified once it is issued.",
                )
                return redirect("cards:list")
    else:
        form = CardRequestForm(request.user)
    return render(request, "customer/cards.html", {"cards": cards, "form": form})


@pin_setup_required
def toggle_freeze(request, pk):
    card = get_object_or_404(Card, pk=pk, user=request.user)
    if request.method == "POST":
        pin = request.POST.get("pin", "")
        if not request.user.profile.verify_pin(pin):
            messages.error(request, "Incorrect transaction PIN.")
        elif card.status == Card.Status.ACTIVE:
            card.status = Card.Status.FROZEN
            card.save(update_fields=["status"])
            log_action(request.user, "CARD_FREEZE", card.masked_number, request)
            notify(request.user, "Card frozen",
                   f"Your card {card.masked_number} has been frozen.", "warning")
            messages.success(request, "Card frozen. No transactions will be allowed.")
        elif card.status == Card.Status.FROZEN:
            card.status = Card.Status.ACTIVE
            card.save(update_fields=["status"])
            log_action(request.user, "CARD_UNFREEZE", card.masked_number, request)
            notify(request.user, "Card unfrozen",
                   f"Your card {card.masked_number} is active again.", "success")
            messages.success(request, "Card unfrozen.")
        else:
            messages.error(request, "This card cannot be frozen or unfrozen.")
    return redirect("cards:list")


@pin_setup_required
def block_card(request, pk):
    card = get_object_or_404(Card, pk=pk, user=request.user)
    if request.method == "POST":
        pin = request.POST.get("pin", "")
        if not request.user.profile.verify_pin(pin):
            messages.error(request, "Incorrect transaction PIN.")
        elif card.status in (Card.Status.ACTIVE, Card.Status.FROZEN):
            card.status = Card.Status.BLOCKED
            card.save(update_fields=["status"])
            log_action(request.user, "CARD_BLOCK", card.masked_number, request)
            notify(request.user, "Card blocked",
                   f"Your card {card.masked_number} has been permanently blocked.",
                   "danger")
            messages.success(
                request,
                "Card blocked permanently. Request a new card if you need a replacement.",
            )
        else:
            messages.error(request, "This card cannot be blocked.")
    return redirect("cards:list")
