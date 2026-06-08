{#
  Persiste el resultado de cada test de calidad en dq.dq_results (ADR-016).
  Se ejecuta en on-run-end: recorre los resultados de la corrida y registra,
  por cada test, su dimensión de calidad (del tag dq:<dimension>), severidad,
  estado (pass/fail/warn/error) y nº de filas que fallaron.

  A diferencia de store_failures (que solo guarda filas ofensoras de los tests
  que fallan), esto deja un audit-trail de TODOS los checks, base de la "marca
  de calidad visible" que consume BI.
#}
{% macro log_dq_results() %}
  {% if execute and results %}
    {% set rows = [] %}
    {% for res in results %}
      {% if res.node.resource_type == 'test' %}
        {% set ns = namespace(dim='otra') %}
        {% for t in res.node.config.tags %}
          {% if t.startswith('dq:') %}{% set ns.dim = t[3:] %}{% endif %}
        {% endfor %}
        {% set failures = res.failures if res.failures is not none else 0 %}
        {% set sev = (res.node.config.severity | string | lower) %}
        {% set name = res.node.name | replace("'", "") %}
        {% set status = res.status | string %}
        {% do rows.append(
            "('" ~ invocation_id ~ "','" ~ name ~ "','" ~ ns.dim ~ "','"
            ~ sev ~ "','" ~ status ~ "'," ~ failures ~ ", current_timestamp)"
        ) %}
      {% endif %}
    {% endfor %}

    {% if rows | length > 0 %}
      {% do run_query("create schema if not exists dq") %}
      {% do run_query(
        "create table if not exists dq.dq_results ("
        ~ "invocation_id text, test_name text, dimension text, "
        ~ "severity text, status text, failures integer, executed_at timestamp)"
      ) %}
      {% do run_query("insert into dq.dq_results values " ~ (rows | join(",\n"))) %}
      {% do log("[dq] " ~ (rows | length) ~ " checks registrados en dq.dq_results", info=true) %}
    {% endif %}
  {% endif %}
{% endmacro %}
