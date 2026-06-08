-- Bootstrap del Data Warehouse (PostgreSQL / RDS).
--
-- Crea las bases del DW de forma IDEMPOTENTE. Postgres no soporta
-- CREATE DATABASE IF NOT EXISTS, así que se genera el comando condicionalmente
-- y se ejecuta con \gexec (meta-comando de psql).
--
-- Una sola instancia RDS hospeda DOS bases (aislamiento dentro del free tier):
--   - oil_dw_staging : entorno de staging (EC2 api-dev)
--   - oil_dw_prod    : entorno de producción
--
-- Los SCHEMAS (bronze/silver/gold/dq) NO se crean acá a propósito: los provisiona
-- el pipeline de forma idempotente (load_bronze.py hace
-- `create schema if not exists bronze` y dbt crea silver/gold/dq vía el macro
-- generate_schema_name). Una sola fuente de verdad evita drift.
--
-- Uso (conecta a la base de mantenimiento `postgres`; el secreto va por el
-- entorno, NUNCA al repo):
--   psql -h <RDS_ENDPOINT> -U <master_user> -d postgres -v ON_ERROR_STOP=1 \
--        -f infra/db/bootstrap.sql

SELECT 'CREATE DATABASE oil_dw_staging'
 WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'oil_dw_staging')\gexec

SELECT 'CREATE DATABASE oil_dw_prod'
 WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'oil_dw_prod')\gexec
