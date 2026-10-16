{% test percentage_between_zero_and_one(model, column_name) %}
{#-
    Fails if a rate/percentage column (stored as a 0-1 fraction, not 0-100)
    falls outside [0, 1]. NULL passes - that's "undefined" (e.g. a zero
    denominator via safe_divide), not a violation. First needed in Phase 5:
    Phase 3 had no percentage-typed columns to apply this to.
-#}
select *
from {{ model }}
where {{ column_name }} is not null
  and ({{ column_name }} < 0 or {{ column_name }} > 1)

{% endtest %}
