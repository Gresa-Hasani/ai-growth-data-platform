{% macro normalize_text(column_name, lower=False) %}
{#-
    Trims whitespace from a text column. Casing is preserved by default
    (organization_name, email local-part, etc. have presentational meaning)
    - pass lower=True only for categorical/canonical-value columns where
    casing carries no business meaning (statuses, channels, plan names...).
-#}
{%- if lower -%}
lower(trim({{ column_name }}))
{%- else -%}
trim({{ column_name }})
{%- endif -%}
{% endmacro %}
