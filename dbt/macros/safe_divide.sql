{% macro safe_divide(numerator, denominator) %}
{#-
    NULL-safe division: returns NULL when the denominator is NULL or zero,
    instead of raising "division by zero" or silently producing a
    misleading 0. First needed in Phase 4 (int_api_usage_by_customer's
    error rate); no Phase 3 staging model divides anything.
-#}
case
    when {{ denominator }} is null or {{ denominator }} = 0 then null
    else {{ numerator }}::numeric / {{ denominator }}::numeric
end
{% endmacro %}
