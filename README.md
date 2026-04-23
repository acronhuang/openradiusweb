# OpenRadiusWeb

Open-source Network Access Control (NAC) system with RADIUS-based 802.1X authentication, device visibility, and policy enforcement.

## Features

- **802.1X Authentication** - PEAP, EAP-TLS, EAP-TTLS via FreeRADIUS
- **Dynamic VLAN Assignment** - AD/LDAP group-based VLAN assignment
- **MAC Authentication Bypass (MAB)** - Whitelist for non-802.1X devices
- **Device Discovery** - Automatic ARP/DHCP/SNMP/Nmap scanning
- **Policy Engine** - Condition-based access control with automated actions
- **Switch Management** - SNMP/SSH port and VLAN control
- **Change of Authorization (CoA)** - Real-time session control
- **Web UI** - React-based management interface
- **Multi-Tenant** - Full tenant isolation
- **Audit Logging** - Complete audit trail for compliance

## Architecture

```
Browser --> Nginx (8888) --> FastAPI Gateway (8000) --> PostgreSQL + Redis + NATS
                                                    --> FreeRADIUS (1812/1813 UDP)
                                                    --> Discovery / Inventory / Policy / Switch Mgmt
```

12 Docker containers, event-driven microservices via NATS JetStream.

| Component | Technology |
|-----------|-----------|
| Frontend | React 18, Ant Design 5, TypeScript |
| API | FastAPI, Python 3.11 |
| Database | PostgreSQL 15 + TimescaleDB |
| Cache | Redis 7 |
| Message Bus | NATS JetStream |
| RADIUS | FreeRADIUS 3.2.3 |

## Quick Start

```bash
# 1. Clone
git clone https://github.com/YOUR_ORG/openradiusweb.git
cd openradiusweb

# 2. Configure
cp .env.example .env.production
# Edit .env.production (set DB_PASSWORD, REDIS_PASSWORD, JWT_SECRET_KEY)

# 3. Deploy
docker compose -f docker-compose.prod.yml --env-file .env.production up -d --build

# 4. Access
# Web UI:  http://localhost:8888  (admin / OpenNAC2026)
# API:     http://localhost:8000/docs
```

## Documentation

| Document | Description |
|----------|-------------|
| [Deployment Guide](docs/deployment-guide.md) | Full installation and deployment instructions |
| [Operations Manual](docs/operations-manual.md) | Day-to-day operations, configuration, features |
| [Architecture](docs/architecture.md) | SDD/BDD/DDD analysis, design patterns, component mapping |
| [Backup & Recovery](docs/backup-recovery-troubleshooting.md) | Backup, disaster recovery, troubleshooting |

## Project Structure

```
openradiusweb/
  docker-compose.prod.yml       # Production deployment
  .env.example                   # Configuration template
  migrations/                    # Database schemas
  services/
    gateway/                     # FastAPI REST API (20 route modules)
    discovery/                   # Network device discovery
    device_inventory/            # Device management
    policy_engine/               # Policy evaluation
    switch_mgmt/                 # Switch SNMP/SSH control
    event_service/               # Event aggregation
    auth/                        # FreeRADIUS + CoA + Config Watcher
  shared/orw_common/             # Shared Python library
  frontend/                      # React + TypeScript + Ant Design
  tests/                         # Unit and integration tests
  docs/                          # Documentation
```

## Ports

| Port | Protocol | Service |
|------|----------|---------|
| 8888 | TCP | Web UI |
| 8000 | TCP | REST API |
| 1812 | UDP | RADIUS Auth |
| 1813 | UDP | RADIUS Acct |
| 3799 | UDP | RADIUS CoA |

## License

MIT
