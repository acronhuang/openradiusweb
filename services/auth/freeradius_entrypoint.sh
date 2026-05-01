#!/bin/bash
set -e

echo "=== OpenRadiusWeb FreeRADIUS Starting ==="

# Wait for PostgreSQL to be ready
echo "Waiting for PostgreSQL..."
until python3 -c "
import psycopg2
conn = psycopg2.connect('${ORW_DB_URL}')
conn.close()
print('PostgreSQL is ready')
" 2>/dev/null; do
    sleep 2
done

# Make sure the orw-managed directories exist (they're shared volumes that
# the watcher writes to). The certs dir and its subdirs are also mounted
# from a shared volume so the cert manager and FreeRADIUS see the same
# files.
mkdir -p /etc/freeradius/orw-managed/mods-available \
         /etc/freeradius/orw-managed/sites-available \
         /etc/freeradius/certs/trusted-cas \
         /etc/freeradius/certs/ldap

# Generate FreeRADIUS configs from database. With PR #36's conditional
# generation, this is safe even when no certs / no LDAP / no NAS clients
# are configured — the generated site default won't reference modules
# that weren't generated.
echo "Generating FreeRADIUS configuration from database..."
python3 /opt/orw/freeradius_config_manager.py --generate-and-apply || {
    echo "WARNING: Config generation failed, using defaults"
}

# Symlink (not copy) every generated module + site into FreeRADIUS' read
# path. Symlinks let the watcher's runtime updates be picked up at the
# next HUP without a container restart.
for conf in /etc/freeradius/orw-managed/mods-available/*; do
    if [ -f "$conf" ]; then
        name=$(basename "$conf")
        rm -f "/etc/freeradius/mods-enabled/$name"
        ln -sf "$conf" "/etc/freeradius/mods-enabled/$name"
        echo "Enabled module: $name"
    fi
done

for site in /etc/freeradius/orw-managed/sites-available/*; do
    if [ -f "$site" ]; then
        name=$(basename "$site")
        rm -f "/etc/freeradius/sites-enabled/$name"
        ln -sf "$site" "/etc/freeradius/sites-enabled/$name"
        echo "Enabled site: $name"
    fi
done

# proxy.conf and clients.conf: also symlinks, not copies, so watcher
# updates take effect on HUP. (Was cp pre-PR #36 — see PR #34 / #36.)
if [ -f /etc/freeradius/orw-managed/proxy.conf ]; then
    rm -f /etc/freeradius/proxy.conf
    ln -sf /etc/freeradius/orw-managed/proxy.conf /etc/freeradius/proxy.conf
    echo "Linked proxy.conf -> orw-managed/proxy.conf"
fi
if [ -f /etc/freeradius/orw-managed/clients.conf ]; then
    rm -f /etc/freeradius/clients.conf
    ln -sf /etc/freeradius/orw-managed/clients.conf /etc/freeradius/clients.conf
    echo "Linked clients.conf -> orw-managed/clients.conf"
fi

# Permissions: cert manager writes server.key directly into certs/
# (not into a subdir), so the chmod target is the file itself, not a
# glob over a subdir which used to silently match nothing.
chown -R freerad:freerad /etc/freeradius/certs/ 2>/dev/null || true
chmod 600 /etc/freeradius/certs/server.key 2>/dev/null || true
chmod 600 /etc/freeradius/certs/dh.pem 2>/dev/null || true

echo "=== FreeRADIUS configuration applied ==="
echo "Starting radiusd..."
exec "$@"
