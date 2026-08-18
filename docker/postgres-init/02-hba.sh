# Ensures pg_hba.conf allows TCP ("host") connections for any role/database,
# not just the Unix-socket ("local") connections trusted by default.
# Container-to-container traffic (web/celery -> postgres) is always "host",
# never "local" -- without this, no role (not even the superuser) can
# connect over the docker network, regardless of the appuser/RLS setup in
# 01-appuser.sql. The official image is supposed to add this itself via
# pg_setup_hba_conf when POSTGRES_PASSWORD is set, but that didn't take
# effect reliably in this environment, so it's added explicitly here too.
if ! grep -q '^host[[:space:]]\+all[[:space:]]\+all[[:space:]]\+all' "$PGDATA/pg_hba.conf" 2>/dev/null; then
    echo "host all all all scram-sha-256" >> "$PGDATA/pg_hba.conf"
fi
