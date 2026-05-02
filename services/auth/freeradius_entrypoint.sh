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

# proxy.conf and clients.conf: symlinks, not copies, so watcher updates
# take effect on HUP. (Was cp pre-PR #36 — see PR #34 / #36.)
#
# IMPORTANT: must symlink at /etc/freeradius/3.0/ (the actual config dir
# Debian's freeradius package reads from), NOT at /etc/freeradius/.
# Dockerfile.freeradius creates /etc/freeradius/<file> -> 3.0/<file>
# convenience symlinks for the upstream layout, but radiusd.conf does
# `$INCLUDE clients.conf` which resolves relative to its own location
# (/etc/freeradius/3.0/radiusd.conf) and looks for
# /etc/freeradius/3.0/clients.conf. Symlinking only at the upper level
# leaves the stock Debian /etc/freeradius/3.0/clients.conf in place
# (with only localhost) and freeradius silently rejects every request
# from real NAS clients with "Ignoring request from unknown client".
# Took 4+ hours to diagnose this in the 2026-05-02 deployment.
FR_CONF_DIR=/etc/freeradius/3.0
if [ -f /etc/freeradius/orw-managed/proxy.conf ]; then
    rm -f "$FR_CONF_DIR/proxy.conf"
    ln -sf /etc/freeradius/orw-managed/proxy.conf "$FR_CONF_DIR/proxy.conf"
    echo "Linked $FR_CONF_DIR/proxy.conf -> orw-managed/proxy.conf"
fi
if [ -f /etc/freeradius/orw-managed/clients.conf ]; then
    rm -f "$FR_CONF_DIR/clients.conf"
    ln -sf /etc/freeradius/orw-managed/clients.conf "$FR_CONF_DIR/clients.conf"
    echo "Linked $FR_CONF_DIR/clients.conf -> orw-managed/clients.conf"
fi

# Make freeradius log to stdout so `docker logs orw-freeradius` shows
# auth events. By default Debian's radiusd.conf writes to a log file
# (/var/log/freeradius/radius.log) — fine on a VM but invisible to
# docker logs. Also enable auth logging so Login OK/incorrect events
# are recorded. Both lost on container recreation but baked-in here
# so they re-apply automatically on every start.
#
# auth_goodpass / auth_badpass MUST stay no — when on, every Auth: line
# logs the cleartext password (`[user/PASSWORD]`). Stock Debian ships them
# off; explicitly force off here in case a future Debian point release
# flips the default.
sed -i \
    -e 's|^\([[:space:]]*\)destination = files|\1destination = stdout|' \
    -e 's|^\([[:space:]]*\)auth = no|\1auth = yes|' \
    -e 's|^\([[:space:]]*\)auth_badpass = yes|\1auth_badpass = no|' \
    -e 's|^\([[:space:]]*\)auth_goodpass = yes|\1auth_goodpass = no|' \
    "$FR_CONF_DIR/radiusd.conf"

# Permissions: cert manager writes server.key directly into certs/
# (not into a subdir), so the chmod target is the file itself, not a
# glob over a subdir which used to silently match nothing.
chown -R freerad:freerad /etc/freeradius/certs/ 2>/dev/null || true
chmod 600 /etc/freeradius/certs/server.key 2>/dev/null || true
chmod 600 /etc/freeradius/certs/dh.pem 2>/dev/null || true

echo "=== FreeRADIUS configuration applied ==="
echo "Starting radiusd..."
exec "$@"
