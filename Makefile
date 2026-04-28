.PHONY: help setup dev up down logs test lint lint-features clean db-init db-reset seed

COMPOSE = docker compose
PYTHON = python3

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

# ============================================================
# Setup
# ============================================================
setup: ## Initial setup - create .env and secrets
	@echo "Setting up OpenRadiusWeb development environment..."
	@cp -n .env.example .env 2>/dev/null || true
	@mkdir -p .secrets
	@echo "Setup complete. Edit .env and set DB_PASSWORD, REDIS_PASSWORD, JWT_SECRET_KEY."

# ============================================================
# Development
# ============================================================
dev: setup ## Start infrastructure services only (DB, Redis, NATS)
	$(COMPOSE) up -d postgres redis nats
	@echo "Infrastructure ready. PostgreSQL: 5432, Redis: 6379, NATS: 4222"

up: setup ## Start all services
	$(COMPOSE) up -d --build

down: ## Stop all services
	$(COMPOSE) down

restart: ## Restart all services
	$(COMPOSE) restart

logs: ## Show logs (use SERVICE=name to filter)
	$(COMPOSE) logs -f $(SERVICE)

# ============================================================
# Database
# ============================================================
db-init: ## Initialize database schema
	$(COMPOSE) exec postgres psql -U orw -d orw -f /docker-entrypoint-initdb.d/01-init.sql

db-reset: ## Reset database (WARNING: destroys all data)
	$(COMPOSE) exec postgres psql -U orw -d orw -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"
	$(MAKE) db-init

seed: ## Seed database with demo data
	$(COMPOSE) exec postgres psql -U orw -d orw -f /docker-entrypoint-initdb.d/02-seed.sql

# ============================================================
# Testing
# ============================================================
test: ## Run unit tests
	$(PYTHON) -m pytest tests/unit/ -v

test-integration: ## Run integration tests
	$(PYTHON) -m pytest tests/integration/ -v

test-all: ## Run all tests
	$(PYTHON) -m pytest tests/ -v --tb=short

# ============================================================
# Quality
# ============================================================
lint: lint-features ## Run linters
	$(PYTHON) -m ruff check shared/ services/
	$(PYTHON) -m mypy shared/ services/ --ignore-missing-imports

lint-features: ## Enforce feature-oriented layout (no new files in gateway/routes/)
	$(PYTHON) scripts/check_no_new_routes.py

format: ## Format code
	$(PYTHON) -m ruff format shared/ services/

# ============================================================
# Monitoring
# ============================================================
monitoring: ## Start with monitoring stack (Prometheus + Grafana)
	$(COMPOSE) --profile monitoring up -d

# ============================================================
# Cleanup
# ============================================================
clean: ## Remove containers, volumes, and build artifacts
	$(COMPOSE) down -v --remove-orphans
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true

status: ## Show service status
	$(COMPOSE) ps
