from django import template

register = template.Library()


@register.simple_tag(takes_context=True)
def page_url(context, page_number):
    """Keep the current filters while moving between pages."""
    params = context["request"].GET.copy()
    params["page"] = page_number
    return f"?{params.urlencode()}"
