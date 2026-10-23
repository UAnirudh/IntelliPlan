\set ON_ERROR_STOP on

-- Executed only for a brand-new Docker volume, as the bootstrap postgres role.
-- Read the mounted secret in the server; never put it in an argv or SQL log.
SELECT format('CREATE ROLE intelliplan LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION PASSWORD %L',
              trim(pg_read_file('/run/secrets/app_password')))
\gexec

ALTER DATABASE intelliplan OWNER TO intelliplan;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT ALL ON SCHEMA public TO intelliplan;
