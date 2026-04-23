#!/bin/bash
# ============================================================
# OpenRadiusWeb Deployment Script
# Reads ORW_HOST from .env.production or uses hostname
# ============================================================

set -euo pipefail

DEPLOY_DIR="/opt/openradiusweb"
COMPOSE_FILE="docker-compose.prod.yml"
ENV_FILE=".env.production"

# Resolve host IP from .env.production or system hostname
if [ -f "$DEPLOY_DIR/$ENV_FILE" ]; then
    ORW_HOST=$(grep -oP '^ORW_HOST=\K.*' "$DEPLOY_DIR/$ENV_FILE" 2>/dev/null || hostname -I | awk '{print $1}')
elif [ -f "$ENV_FILE" ]; then
    ORW_HOST=$(grep -oP '^ORW_HOST=\K.*' "$ENV_FILE" 2>/dev/null || hostname -I | awk '{print $1}')
else
    ORW_HOST=$(hostname -I | awk '{print $1}')
fi

echo "============================================"
echo "  OpenRadiusWeb Deployment - ${ORW_HOST}"
echo "============================================"

# ============================================================
# Step 1: Check prerequisites
# ============================================================
echo ""
echo "[1/6] Checking prerequisites..."

# Check if running as root or with sudo
if [ "$EUID" -ne 0 ]; then
    echo "ERROR: Please run as root (sudo ./deploy.sh)"
    exit 1
fi

# Check Docker
if ! command -v docker &> /dev/null; then
    echo "Docker not found. Installing..."
    curl -fsSL https://get.docker.com | sh
    systemctl enable docker
    systemctl start docker
    echo "Docker installed successfully."
else
    echo "Docker: $(docker --version)"
fi

# Check Docker Compose
if ! docker compose version &> /dev/null; then
    echo "Docker Compose plugin not found. Installing..."
    apt-get update && apt-get install -y docker-compose-plugin
fi
echo "Docker Compose: $(docker compose version)"

# ============================================================
# Step 2: Prepare deployment directory
# ============================================================
echo ""
echo "[2/6] Preparing deployment directory..."

if [ ! -d "$DEPLOY_DIR" ]; then
    mkdir -p "$DEPLOY_DIR"
    echo "Created $DEPLOY_DIR"
fi

# If git repo exists, pull latest
if [ -d "$DEPLOY_DIR/.git" ]; then
    echo "Pulling latest code..."
    cd "$DEPLOY_DIR"
    git pull
else
    echo "NOTE: Copy OpenRadiusWeb source code to $DEPLOY_DIR"
    echo "  scp -r ./openradiusweb/* root@\${TARGET_HOST}:$DEPLOY_DIR/"
fi

cd "$DEPLOY_DIR"

# ============================================================
# Step 3: Configure environment
# ============================================================
echo ""
echo "[3/6] Configuring environment..."

if [ ! -f ".env.production" ]; then
    echo "ERROR: .env.production not found. Copy it first."
    exit 1
fi

# Generate JWT secret if placeholder
if grep -q "CHANGE_ME" .env.production; then
    JWT_SECRET=$(openssl rand -hex 32)
    sed -i "s/CHANGE_ME_USE_openssl_rand_hex_32/$JWT_SECRET/" .env.production
    echo "Generated JWT secret."
fi

# Generate Grafana password if placeholder
if grep -q "CHANGE_ME_Grafana" .env.production; then
    GRAFANA_PASS=$(openssl rand -base64 12)
    sed -i "s/CHANGE_ME_Grafana_Admin/$GRAFANA_PASS/" .env.production
    echo "Generated Grafana password: $GRAFANA_PASS"
fi

echo "Environment configured."

# ============================================================
# Step 4: Configure firewall
# ============================================================
echo ""
echo "[4/6] Configuring firewall..."

# Check if ufw is available
if command -v ufw &> /dev/null; then
    ufw allow 8888/tcp comment "OpenRadiusWeb Web UI"
    ufw allow 8000/tcp comment "OpenRadiusWeb API"
    ufw allow 1812/udp comment "RADIUS Auth"
    ufw allow 1813/udp comment "RADIUS Accounting"
    ufw allow 3799/udp comment "RADIUS CoA"
    echo "Firewall rules added (ufw)."
elif command -v firewall-cmd &> /dev/null; then
    firewall-cmd --permanent --add-port=8888/tcp
    firewall-cmd --permanent --add-port=8000/tcp
    firewall-cmd --permanent --add-port=1812/udp
    firewall-cmd --permanent --add-port=1813/udp
    firewall-cmd --permanent --add-port=3799/udp
    firewall-cmd --reload
    echo "Firewall rules added (firewalld)."
else
    echo "No firewall manager found. Make sure ports 8888, 8000, 1812, 1813, 3799 are open."
fi

# ============================================================
# Step 5: Build and start services
# ============================================================
echo ""
echo "[5/6] Building and starting services..."

docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" build
docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" up -d

echo "Waiting for services to start..."
sleep 10

# ============================================================
# Step 6: Verify deployment
# ============================================================
echo ""
echo "[6/6] Verifying deployment..."

# Check all containers
echo ""
echo "Container status:"
docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" ps

# Health check
echo ""
echo "Health checks:"

# PostgreSQL
if docker exec orw-postgres pg_isready -U orw &> /dev/null; then
    echo "  PostgreSQL:  OK"
else
    echo "  PostgreSQL:  FAILED"
fi

# Redis
if docker exec orw-redis redis-cli ping &> /dev/null; then
    echo "  Redis:       OK"
else
    echo "  Redis:       FAILED"
fi

# NATS
if curl -s http://127.0.0.1:8222/healthz &> /dev/null; then
    echo "  NATS:        OK"
else
    echo "  NATS:        FAILED"
fi

# API Gateway
sleep 5
if curl -sf http://127.0.0.1:8000/health &> /dev/null; then
    echo "  API Gateway: OK"
else
    echo "  API Gateway: Starting... (wait a few more seconds)"
fi

# Web UI
if curl -sf http://127.0.0.1:8888 &> /dev/null; then
    echo "  Web UI:      OK"
else
    echo "  Web UI:      Starting..."
fi

echo ""
echo "============================================"
echo "  Deployment Complete!"
echo "============================================"
echo ""
echo "  Web UI:   http://${ORW_HOST}:8888"
echo "  API Docs: http://${ORW_HOST}:8000/docs"
echo "  RADIUS:   ${ORW_HOST}:1812 (UDP)"
echo "  CoA:      ${ORW_HOST}:3799 (UDP)"
echo ""
echo "  Default login:"
echo "    Username: admin"
echo "    Password: admin (change immediately!)"
echo ""
echo "  View logs:"
echo "    docker compose -f $COMPOSE_FILE --env-file $ENV_FILE logs -f"
echo ""
echo "  Stop services:"
echo "    docker compose -f $COMPOSE_FILE --env-file $ENV_FILE down"
echo ""
