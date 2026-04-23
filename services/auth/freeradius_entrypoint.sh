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

# Create certificate directories
mkdir -p /etc/freeradius/certs/ca
mkdir -p /etc/freeradius/certs/server
mkdir -p /etc/freeradius/certs/trusted-cas
mkdir -p /etc/freeradius/certs/crl
mkdir -p /etc/freeradius/orw-managed/mods-available
mkdir -p /etc/freeradius/orw-managed/sites-available

# Generate FreeRADIUS configs from database
echo "Generating FreeRADIUS configuration from database..."
python3 /opt/orw/freeradius_config_manager.py --generate-and-apply || {
    echo "WARNING: Config generation failed, using defaults"
}

# Enable rlm_python module
ln -sf /etc/freeradius/mods-available/python /etc/freeradius/mods-enabled/python 2>/dev/null || true

# Enable managed configs
for conf in /etc/freeradius/orw-managed/mods-available/*; do
    if [ -f "$conf" ]; then
        name=$(basename "$conf")
        # Remove default version first if exists
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

# Copy proxy.conf if generated
if [ -f /etc/freeradius/orw-managed/proxy.conf ]; then
    cp /etc/freeradius/orw-managed/proxy.conf /etc/freeradius/proxy.conf
    echo "Applied proxy.conf"
fi

# Copy clients.conf if generated
if [ -f /etc/freeradius/orw-managed/clients.conf ]; then
    cp /etc/freeradius/orw-managed/clients.conf /etc/freeradius/clients.conf
    echo "Applied clients.conf"
fi

# Set permissions
chown -R freerad:freerad /etc/freeradius/certs/ 2>/dev/null || true
chmod 600 /etc/freeradius/certs/server/*.key 2>/dev/null || true

echo "=== FreeRADIUS configuration applied ==="
echo "Starting radiusd..."
exec "$@"
