#!/bin/sh
set -eu
export PODMENA_DB_PASSWORD
PODMENA_DB_PASSWORD="$(cat /run/secrets/db_password)"
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<'SQL'
\getenv app_password PODMENA_DB_PASSWORD
CREATE ROLE podmena LOGIN PASSWORD :'app_password';
ALTER DATABASE podmena OWNER TO podmena;
SQL
