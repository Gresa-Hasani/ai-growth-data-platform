{% test positive_or_zero(model, column_name) %}
{#-
    Fails on any negative value in a monetary/count column. NULL passes
    (that's "unknown", handled separately by safe_cast_* + its own
    not_null/quality-flag tests) - zero and unknown are not equivalent,
    so this test only ever objects to a genuinely negative number.
-#}
select *
from {{ model }}
where {{ column_name }} is not null
  and {{ column_name }} < 0

{% endtest %}
