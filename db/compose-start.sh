#!/bin/sh
set -eu

for attempt in $(seq 1 150); do
    if [ -f /run/repeat/admin/ready ]; then
        IFS= read -r MSSQL_SA_PASSWORD < /run/repeat/admin/sa-password
        export MSSQL_SA_PASSWORD
        cp /run/repeat/mssql.conf /var/opt/mssql/mssql.conf
        exec /opt/mssql/bin/launch_sqlservr.sh /opt/mssql/bin/sqlservr
    fi
    sleep 1
done

echo "Timed out waiting for the Compose SQL bootstrap." >&2
exit 1
