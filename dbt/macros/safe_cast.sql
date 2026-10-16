{% macro safe_cast_numeric(column_name) %}
{#-
    NULL-safe cast to numeric: a value that fails to parse becomes NULL
    rather than crashing the model or (worse) silently becoming 0 - zero
    and unknown are not the same thing. Uses PostgreSQL 16's
    pg_input_is_valid(), which checks parseability without raising.
-#}
case
    when {{ column_name }} is null then null
    when pg_input_is_valid(trim({{ column_name }}), 'numeric') then trim({{ column_name }})::numeric
    else null
end
{% endmacro %}


{% macro safe_cast_integer(column_name) %}
{#- Same NULL-safe pattern as safe_cast_numeric(), for integer-typed source columns. -#}
case
    when {{ column_name }} is null then null
    when pg_input_is_valid(trim({{ column_name }}), 'bigint') then trim({{ column_name }})::bigint
    else null
end
{% endmacro %}


{% macro safe_cast_timestamp(column_name) %}
{#-
    NULL-safe cast to timestamptz. Source timestamps are ISO-8601 UTC
    strings ("...Z") emitted by scripts/generate_data.py; anything that
    doesn't parse becomes NULL instead of failing the whole model.
-#}
case
    when {{ column_name }} is null then null
    when pg_input_is_valid(trim({{ column_name }}), 'timestamptz') then trim({{ column_name }})::timestamptz
    else null
end
{% endmacro %}
