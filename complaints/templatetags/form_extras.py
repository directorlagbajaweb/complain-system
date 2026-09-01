"""
One small template filter, used by partials/_field.html.

Bootstrap shows an invalid field by putting `is-invalid` on the <input> itself.
Whether a field is invalid is only known after the form has been validated —
too late for the form's `__init__` to set it — so the class has to be added at
render time, in the template. Django has no built-in way to do that, hence this.
"""

from django import template

register = template.Library()


@register.filter
def add_class(field, css_class):
    """
    Render a bound form field with an extra CSS class on its widget.

        {{ form.email|add_class:"is-invalid" }}

    `as_widget(attrs=...)` renders the field with those HTML attributes, so we
    read the class the form already set in `forms._style()` and append to it
    rather than replacing it — otherwise adding `is-invalid` would strip
    `form-control` and the box would lose its styling entirely.
    """
    existing = field.field.widget.attrs.get('class', '')
    combined = f"{existing} {css_class}".strip()
    return field.as_widget(attrs={'class': combined})
