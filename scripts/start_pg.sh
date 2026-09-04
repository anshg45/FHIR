#!/bin/bash
# Idempotent helper to make sure PostgreSQL is running (survives pod restarts)
mkdir -p /var/run/postgresql /app/pglog
chown -R postgres:postgres /var/run/postgresql /app/pgdata /app/pgconf /app/pglog 2>/dev/null
chmod 700 /app/pgdata
supervisorctl status postgresql >/dev/null 2>&1 || { supervisorctl reread; supervisorctl update; }
supervisorctl start postgresql 2>/dev/null
sleep 3
su postgres -c "psql -c 'select 1'" >/dev/null 2>&1 && echo "PostgreSQL is UP" || echo "PostgreSQL FAILED - check /var/log/supervisor/postgres.err.log"
