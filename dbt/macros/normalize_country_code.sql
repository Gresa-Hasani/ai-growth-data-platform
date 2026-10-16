{% macro normalize_country_code(column_name) %}
{#-
    Maps known messy variants (from Phase 2's injected quality issues:
    "USA"/"us"/"United States"/" US" -> US, "UK"/"gb"/"United Kingdom" -> GB)
    to their canonical ISO-3166-1 alpha-2 code, then falls back to a trimmed
    upper-case 2-letter code for anything else. This is intentionally NOT a
    full geography reference - see is_country_code_recognized() for
    detecting values outside the known-good set rather than silently
    mapping an unrecognized value to the wrong country.
-#}
case
    when upper(trim({{ column_name }})) in ('US', 'USA', 'UNITED STATES') then 'US'
    when upper(trim({{ column_name }})) in ('GB', 'UK', 'UNITED KINGDOM') then 'GB'
    when {{ column_name }} is null then null
    else upper(trim({{ column_name }}))
end
{% endmacro %}


{% macro is_country_code_recognized(normalized_column_name) %}
{#- Membership check against the canonical ISO2 set this synthetic
    environment actually generates (see scripts/generate_data.py::
    CLEAN_COUNTRY_CODES). Anything outside this set is flagged, not
    guessed at, so downstream models can decide how to handle it. -#}
(
    {{ normalized_column_name }} in (
        'US', 'GB', 'DE', 'FR', 'CA', 'AU', 'NL', 'IN', 'BR', 'JP', 'ES', 'IT'
    )
)
{% endmacro %}
