-- Core Insights CSVs carry integers as "6.0" -- float formatting from
-- whatever wrote them. Direct ::integer fails; ::numeric::integer truncates
-- cleanly. nullif handles empty strings where they appear.
{% macro to_int(col) -%}
    nullif({{ col }}, '')::numeric::integer
{%- endmacro %}

{% macro to_num(col) -%}
    nullif({{ col }}, '')::numeric
{%- endmacro %}