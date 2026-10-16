{% macro monthly_equivalent_amount(amount_column, billing_interval_column) %}
{#-
    Normalizes a subscription's recurring amount to a monthly figure, the
    standard SaaS MRR convention: divide an annual amount by 12 calendar
    months. This is deliberately NOT the inverse of whatever discount
    factor scripts/generate_data.py baked into its annual pricing (verified:
    annual amounts there are ~10x the monthly price, a promotional discount,
    not a 12x calendar multiple) - MRR normalization reflects calendar time,
    not the vendor's original discount structure. Any billing_interval other
    than 'monthly'/'annual' (there should be none - both are tested as
    accepted_values in staging) yields NULL rather than a guessed value.
-#}
case
    when {{ billing_interval_column }} = 'monthly' then {{ amount_column }}
    when {{ billing_interval_column }} = 'annual' then {{ amount_column }} / 12
    else null
end
{% endmacro %}
