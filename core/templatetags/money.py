from django import template

from core.formatting import format_inr

register = template.Library()


@register.filter
def inr(paise):
    """Usage: {{ amount_paise|inr }}"""
    return format_inr(paise)
