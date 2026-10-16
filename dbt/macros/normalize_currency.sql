{% macro normalize_currency(column_name) %}
{#- Trim + uppercase to a canonical ISO-4217-style code ("usd"/" USD " -> USD).
    No currency conversion here - that is explicitly out of scope for
    staging (see docs/dbt_staging.md). -#}
upper(trim({{ column_name }}))
{% endmacro %}


{% macro is_currency_code_recognized(normalized_column_name) %}
{#- Membership check against the currencies this synthetic environment
    actually generates (see scripts/generate_data.py::CURRENCIES). -#}
(
    {{ normalized_column_name }} in ('USD', 'EUR', 'GBP')
)
{% endmacro %}
