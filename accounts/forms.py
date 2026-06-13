from django import forms
from django.contrib.auth import authenticate
from django.contrib.auth.forms import UserCreationForm

from .models import CustomerProfile, User


class StyledFormMixin:
    """Give every widget the .form-control class used by the stylesheet."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            css = "form-control"
            if isinstance(field.widget, (forms.CheckboxInput,)):
                css = "form-check"
            elif isinstance(field.widget, forms.Select):
                css = "form-control form-select"
            existing = field.widget.attrs.get("class", "")
            field.widget.attrs["class"] = f"{existing} {css}".strip()


class RegistrationForm(StyledFormMixin, UserCreationForm):
    first_name = forms.CharField(max_length=150)
    last_name = forms.CharField(max_length=150)
    phone = forms.CharField(max_length=20)
    date_of_birth = forms.DateField(
        widget=forms.DateInput(attrs={"type": "date"})
    )
    gender = forms.ChoiceField(choices=CustomerProfile.Gender.choices)
    address = forms.CharField(max_length=255)
    city = forms.CharField(max_length=100)
    state = forms.CharField(max_length=100)
    national_id_number = forms.CharField(
        label="National ID / BVN", max_length=30
    )
    occupation = forms.CharField(max_length=100, required=False)

    class Meta:
        model = User
        fields = ("first_name", "last_name", "email", "phone")

    def save(self, commit=True):
        user = super().save(commit=False)
        user.role = User.Role.CUSTOMER
        user.phone = self.cleaned_data["phone"]
        if commit:
            user.save()
            CustomerProfile.objects.create(
                user=user,
                date_of_birth=self.cleaned_data["date_of_birth"],
                gender=self.cleaned_data["gender"],
                address=self.cleaned_data["address"],
                city=self.cleaned_data["city"],
                state=self.cleaned_data["state"],
                national_id_number=self.cleaned_data["national_id_number"],
                occupation=self.cleaned_data.get("occupation", ""),
            )
        return user


class LoginForm(StyledFormMixin, forms.Form):
    email = forms.EmailField(widget=forms.EmailInput(attrs={"autofocus": True}))
    password = forms.CharField(widget=forms.PasswordInput)

    def __init__(self, request=None, *args, **kwargs):
        self.request = request
        self.user = None
        super().__init__(*args, **kwargs)

    def clean(self):
        cleaned = super().clean()
        email = cleaned.get("email")
        password = cleaned.get("password")
        if not email or not password:
            return cleaned

        try:
            existing = User.objects.get(email__iexact=email)
        except User.DoesNotExist:
            existing = None

        if existing and existing.is_locked:
            raise forms.ValidationError(
                "This account is temporarily locked after too many failed "
                "attempts. Please try again later."
            )

        self.user = authenticate(self.request, username=email, password=password)
        if self.user is None:
            if existing:
                existing.register_failed_login()
            raise forms.ValidationError("Invalid email address or password.")
        if not self.user.is_active:
            raise forms.ValidationError("This account has been deactivated.")
        self.user.register_successful_login()
        return cleaned


class PinForm(StyledFormMixin, forms.Form):
    pin = forms.RegexField(
        regex=r"^\d{4}$",
        label="4-digit transaction PIN",
        widget=forms.PasswordInput(attrs={"inputmode": "numeric", "maxlength": "4"}),
        error_messages={"invalid": "PIN must be exactly 4 digits."},
    )


class SetPinForm(PinForm):
    pin_confirm = forms.RegexField(
        regex=r"^\d{4}$",
        label="Confirm PIN",
        widget=forms.PasswordInput(attrs={"inputmode": "numeric", "maxlength": "4"}),
        error_messages={"invalid": "PIN must be exactly 4 digits."},
    )

    def clean(self):
        cleaned = super().clean()
        if (
            cleaned.get("pin")
            and cleaned.get("pin_confirm")
            and cleaned["pin"] != cleaned["pin_confirm"]
        ):
            raise forms.ValidationError("The two PINs do not match.")
        return cleaned


class ChangePinForm(SetPinForm):
    current_pin = forms.RegexField(
        regex=r"^\d{4}$",
        label="Current PIN",
        widget=forms.PasswordInput(attrs={"inputmode": "numeric", "maxlength": "4"}),
        error_messages={"invalid": "PIN must be exactly 4 digits."},
    )

    field_order = ["current_pin", "pin", "pin_confirm"]

    def __init__(self, profile, *args, **kwargs):
        self.profile = profile
        super().__init__(*args, **kwargs)
        self.fields["pin"].label = "New 4-digit PIN"

    def clean_current_pin(self):
        current = self.cleaned_data["current_pin"]
        if self.profile.pin_is_locked:
            raise forms.ValidationError(
                "Your PIN is temporarily locked. Try again later."
            )
        if not self.profile.verify_pin(current):
            raise forms.ValidationError("Current PIN is incorrect.")
        return current


class ProfileUpdateForm(StyledFormMixin, forms.ModelForm):
    first_name = forms.CharField(max_length=150)
    last_name = forms.CharField(max_length=150)
    phone = forms.CharField(max_length=20)

    class Meta:
        model = CustomerProfile
        fields = (
            "address", "city", "state", "country", "occupation", "photo",
            "next_of_kin_name", "next_of_kin_phone", "next_of_kin_relationship",
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        user = self.instance.user
        self.fields["first_name"].initial = user.first_name
        self.fields["last_name"].initial = user.last_name
        self.fields["phone"].initial = user.phone

    def save(self, commit=True):
        profile = super().save(commit=commit)
        user = profile.user
        user.first_name = self.cleaned_data["first_name"]
        user.last_name = self.cleaned_data["last_name"]
        user.phone = self.cleaned_data["phone"]
        if commit:
            user.save(update_fields=["first_name", "last_name", "phone"])
        return profile


class KycDocumentForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = CustomerProfile
        fields = ("photo", "id_document")
