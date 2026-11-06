#!/bin/sh
# Start or stop one disposable Odoo for the live tests (tests/live/), apart from
# the Flow Steward stack: own network (fs-odoo-live), own container names.
#
#   tests/live/odoo_matrix.sh up 18     # postgres + Odoo 18.0 with account, sale, stock
#   tests/live/odoo_matrix.sh down 18
#
# "up" prints KEY=<api key> for the admin user of database live<version>.
set -eu
ACTION=${1:?up or down}
V=${2:?version 16..20}
NET=fs-odoo-live
PG=fs-odoo-live-pg$V
OD=fs-odoo-live-$V
DB=live$V
DBARGS="--db_host $PG --db_user odoo --db_password odoo"

if [ "$ACTION" = down ]; then
  docker rm -f "$OD" "$PG" >/dev/null 2>&1 || true
  exit 0
fi

docker network create "$NET" >/dev/null 2>&1 || true
docker rm -f "$OD" "$PG" >/dev/null 2>&1 || true
# Odoo 20 needs PostgreSQL 16 or later (it uses any_value()).
PGIMAGE=postgres:15
[ "$V" -ge 20 ] && PGIMAGE=postgres:16-alpine
docker run -d --name "$PG" --network "$NET" -e POSTGRES_USER=odoo -e POSTGRES_PASSWORD=odoo \
  -e POSTGRES_DB=postgres "$PGIMAGE" >/dev/null
i=0
until docker exec "$PG" pg_isready -U odoo >/dev/null 2>&1; do
  i=$((i + 1)); [ "$i" -gt 60 ] && { echo "postgres did not start" >&2; exit 1; }; sleep 1
done
DEMO=""
[ "$V" -ge 19 ] && DEMO="--with-demo"
ENVS="-e HOST=$PG -e USER=odoo -e PASSWORD=odoo"
# The image's entrypoint passes the database settings from HOST/USER/PASSWORD.
docker run --rm --network "$NET" $ENVS "odoo:$V.0" odoo -d "$DB" -i account,sale,stock \
  --stop-after-init $DEMO >/dev/null 2>&1
docker run -d --name "$OD" --network "$NET" $ENVS "odoo:$V.0" odoo -d "$DB" \
  --db-filter "^$DB\$" >/dev/null
EXPIRY=""
[ "$V" -ge 18 ] && EXPIRY=", __import__('datetime').datetime.now() + __import__('datetime').timedelta(days=1)"
KEY=$(printf "%s\n" \
  "admin = env.ref('base.user_admin')" \
  "key = env['res.users.apikeys'].with_user(admin)._generate('rpc', 'flow steward live'$EXPIRY)" \
  "env.cr.commit()" \
  "print('KEY=' + key)" |
  docker exec -i "$OD" odoo shell $DBARGS -d "$DB" --no-http 2>/dev/null | grep '^KEY=' | tail -1)
i=0
until docker exec "$OD" python3 -c "import urllib.request;urllib.request.urlopen('http://localhost:8069/web/login')" >/dev/null 2>&1; do
  i=$((i + 1)); [ "$i" -gt 90 ] && { echo "odoo did not start" >&2; exit 1; }; sleep 1
done
echo "$KEY"
