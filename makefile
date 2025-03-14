# Makefile for Poliswag Project

# Read environment from .env file
ENV_FILE := .env
ENV := $(shell grep -s '^ENV=' $(ENV_FILE) | cut -d '=' -f2 | tr -d '"' | tr -d "'" | tr -d ' ')
CONTAINER_NAME := poliswag
ifeq ($(ENV),PRODUCTION)
    DOCKER_COMPOSE_FILE := docker-compose.prod.yaml
else
    ENV := dev
    DOCKER_COMPOSE_FILE := docker-compose.yaml
endif

# Directory for mock data
MOCK_DATA_DIR := mock_data

.PHONY: all help up down build logs install run stop test reload install-hooks

all: help

help: ## Display available commands
	@echo "Poliswag Project Commands:"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-15s\033[0m %s\n", $$1, $$2}'
	@echo "\nEnvironment: $(ENV) (from .env file)"

up: ## Start the full application
	@echo "Starting Poliswag in $(ENV) environment..."
	docker compose -f $(DOCKER_COMPOSE_FILE) build
	docker compose -f $(DOCKER_COMPOSE_FILE) up -d --build
	@echo "Creating log files..."
	docker compose -f $(DOCKER_COMPOSE_FILE) exec poliswag /bin/bash -c "mkdir -p /app/logs && touch /app/logs/actions.log && touch /app/logs/error.log"
ifneq ($(ENV),PRODUCTION) ## Extras for development environment, logs are automatically started once container is built
	@sleep 5
	@echo "Initializing database..."
	@mkdir -p $(MOCK_DATA_DIR)
	@cp -n mock_data_sample/*.json $(MOCK_DATA_DIR) || true
	docker compose -f $(DOCKER_COMPOSE_FILE) logs -f --tail=20
endif
	@echo "Poliswag started successfully in $(ENV) environment."

down: ## Stop and clean up all containers
	@echo "Stopping and cleaning up Poliswag in $(ENV) environment..."
	docker compose -f $(DOCKER_COMPOSE_FILE) down --volumes --remove-orphans
	@echo "Cleanup complete."

stop: ## Stop all containers without removing them
	@echo "Stopping Poliswag containers in $(ENV) environment..."
	docker compose -f $(DOCKER_COMPOSE_FILE) stop
	@echo "Poliswag containers stopped."

logs: ## View application logs
	docker compose -f $(DOCKER_COMPOSE_FILE) logs -f --tail=50

run: ## Start the app only (without rebuilding)
	@echo "Starting the app only..."
	docker compose -f $(DOCKER_COMPOSE_FILE) up -d poliswag
	@echo "App started."

install: ## Install dependencies
	# This target is kept for reference. In general, you should not run pip here.
	# It is redundant, the correct way to do is inside the dockerfile.
	@echo "Running the install command is redudant. Packages are installed in the dockerfile"
	@echo "docker compose -f $(DOCKER_COMPOSE_FILE) exec poliswag pip install -r requirements.txt"

test: ## Run the tests with pytest
	@echo "Running tests..."
	pytest -v --cov=modules tests/
	@echo "Tests finished."

reload: ## Reload the Python script inside the container, cleaning log files.
	@echo "Reloading Poliswag application..."
	docker compose -f $(DOCKER_COMPOSE_FILE) exec poliswag /bin/bash -c "echo '' > /app/logs/actions.log && echo '' > /app/logs/error.log"
	docker compose -f $(DOCKER_COMPOSE_FILE) restart poliswag
	docker compose -f $(DOCKER_COMPOSE_FILE) logs -f --tail=20
	@echo "Poliswag application reloaded."

install-hooks: ## Install pre-commit hooks
	python3 -m pip install pre-commit --break-system-packages
	pre-commit install
