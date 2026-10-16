{% test valid_currency_code(model, column_name) %}
{#-
    Fails on any normalized currency value outside this environment's known
    set. In practice this should never fire - normalize_currency() only
    trims/uppercases, it doesn't invent values - but it's a cheap guard
    against a currency showing up that staging isn't prepared for.
-#}
select *
from {{ model }}
where {{ column_name }} is not null
  and {{ column_name }} not in ('USD', 'EUR', 'GBP')

{% endtest %}
