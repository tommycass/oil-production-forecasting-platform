{#
  Override del naming de esquemas: usa el +schema configurado tal cual
  (silver, gold, dq) en lugar de prefijarlo con el esquema del target.
  Así el linaje y los permisos se leen por capa Medallion (ADR-014).
#}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {%- if custom_schema_name is none -%}
        {{ target.schema }}
    {%- else -%}
        {{ custom_schema_name | trim }}
    {%- endif -%}
{%- endmacro %}
