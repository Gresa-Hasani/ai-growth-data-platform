{% test valid_timestamp_order(model, start_column, end_column) %}
{#-
    Reusable pairwise ordering check: end_column must not precede
    start_column when both are present. NULL on either side passes (that's
    "not applicable", e.g. a subscription with no trial) - only an actual
    end-before-start violation fails.
-#}
select *
from {{ model }}
where {{ start_column }} is not null
  and {{ end_column }} is not null
  and {{ end_column }} < {{ start_column }}

{% endtest %}
