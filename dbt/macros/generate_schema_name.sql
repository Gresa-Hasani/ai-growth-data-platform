{% macro generate_schema_name(custom_schema_name, node) -%}
{#-
    Overrides dbt's default "<target_schema>_<custom_schema>" naming
    (which would put staging models in "analytics_staging") so models land
    directly in the schema Phase 1 already provisioned for their layer
    (raw / staging / intermediate / analytics / monitoring - see
    app/db/init_schemas.sql). A model with no +schema config still falls
    back to the target's own schema, matching dbt's normal behavior.
-#}
{%- if custom_schema_name is none -%}
    {{ target.schema }}
{%- else -%}
    {{ custom_schema_name | trim }}
{%- endif -%}
{%- endmacro %}
