{% macro deduplicate(relation, partition_by, order_by) %}
{#-
    Deterministic dedup pattern used by every staging model that has a
    mutable business key (see docs/dbt_staging.md "Deduplication"):
    rank rows within each business key and keep exactly one.

    In practice, raw.* already enforces primary-key uniqueness at the
    ingestion layer (Phase 2 upserts on the source's own id), so this
    currently removes zero rows - it exists as a defensive, documented
    guarantee that staging output is unique by primary key regardless of
    how raw is loaded in the future, not as a fix for an observed problem.

    `relation` is a CTE alias already selected earlier in the model (not a
    dbt ref/source - the macro only adds a ranking + filter on top of it).
    `order_by` should end in a deterministic, non-null tiebreaker (this
    project uses the primary key itself) so ties never resolve arbitrarily.
-#}
select *
from (
    select
        *,
        row_number() over (
            partition by {{ partition_by }}
            order by {{ order_by }}
        ) as _dedup_row_number
    from {{ relation }}
) ranked
where _dedup_row_number = 1
{% endmacro %}
