from django import forms

from .parsing import AmountError, parse_inr


class RupeeField(forms.CharField):
    """A text field that yields integer paise.

    Kept as text rather than a number input so Indian grouping with commas is
    accepted on every keyboard, including phone keypads.
    """

    def __init__(self, *, allow_negative=False, **kwargs):
        self.allow_negative = allow_negative
        kwargs.setdefault("max_length", 24)
        kwargs.setdefault(
            "widget",
            forms.TextInput(
                attrs={
                    "inputmode": "decimal",
                    "autocomplete": "off",
                    "placeholder": "0.00",
                }
            ),
        )
        super().__init__(**kwargs)

    def clean(self, value):
        value = super().clean(value)
        try:
            return parse_inr(value, allow_negative=self.allow_negative)
        except AmountError as exc:
            raise forms.ValidationError(str(exc))
