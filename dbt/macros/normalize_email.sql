{% macro normalize_email(column_name) %}
{#- Deterministic, non-destructive normalization: trim + lowercase only.
    We never rewrite the local part beyond that - a normalized-but-still-
    invalid email is exactly what is_email_valid() is for. -#}
lower(trim({{ column_name }}))
{% endmacro %}


{% macro is_email_valid(column_name) %}
{#-
    Structural validation only - proves the string has the SHAPE of an
    email (one @, a domain with a dot, no embedded whitespace). This does
    NOT prove the mailbox exists or is deliverable; it only rules out the
    quality issues Phase 2 injects (no "@", no domain, embedded spaces).
-#}
(
    {{ column_name }} is not null
    and {{ column_name }} ~ '^[^@\s]+@[^@\s]+\.[^@\s]+$'
)
{% endmacro %}
