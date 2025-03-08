# Poliswag: Pokémon GO Scanning System Management

## Overview

Poliswag is a Python project that manages and enhances the Pokémon GO scanning infrastructure for the Leiria and Marinha Grande regions of Portugal. It serves as a control and management layer for the [Pokémon Scanner](https://pogoleiria.pt) operated by [PoGoLeiria](https://discord.gg/pASCYbp). Poliswag streamlines scanner operations, ensures continuous functionality, manages collected data, and provides tools for users and administrators to interact with the scanning system.

## Key Features

* **Scanning System Management**
  * Provides fail-safes to maintain continuous operation of the scanner
  * Enables remote control via Discord commands
  * Automates re-scans for special quests and events to ensure up-to-date data

* **Quest Management**
  * Lists available quests in the Leiria and Marinha Grande areas
  * Allows moderators to clear the current quest list
  * Enables on-demand quest scans
  * Tracks specific quest rewards and enables alerts
  * Schedules and manages rescans, including automatic event-based rescans

* **Discord Integration**
  * Offers remote control through Discord text channels
  * Manages Discord server roles
  * Provides a user-friendly interface for accessing scanning data
  * Automatically updates voice channel names to display scanner status
  * Provides a rules embed for the Discord server
  * Tracks available Pokémon GO accounts used by the scanning system
  * Allows administrators to retrieve lures from leveled accounts

* **Scanner Status Monitoring**
  * Continuously monitors scanner status in Leiria and Marinha Grande
  * Reflects scanner availability in real-time via Discord voice channel names

* **Utility Features**
  * Advanced logging system
  * External data retrieval
  * Development environment with mock data
  * Automatic code formatting with Black
  * Pre-commit hooks for code style

## Prerequisites

* **Docker:** Installed and running
* **Docker Compose:** Installed
* **Make:** Available on your system
* **Python 3.9+:** For running the bot outside of Docker
* **Discord Bot Token:** Valid token required
* **MariaDB Database:** Running instance to store data
* **.env file:** Must be at the root folder with `ENV` defined as either `PRODUCTION` or `DEVELOPMENT`
* **Environment variables:** Multiple variables needed (see .env.example)
* **Black and pre-commit:** For code quality (will be installed with `make install-hooks`)

## Installation

### 1. Clone the repository

```bash
git clone https://github.com/rubendgpedrosa/Poliswag
cd Poliswag
```

### 2. Set up environment

Create and configure your environment variables:

```bash
cp .env.example .env
```

Edit the .env file with your specific configuration:
- `ENV`: Set to either `DEVELOPMENT` or `PRODUCTION`
- `DISCORD_API_KEY`: Your Discord bot's API token. This is required for the bot to connect to Discord and interact with servers.
- `GOOGLE_API_KEY`: Your Google Maps Static API key. Used to generate static map images.
- `CHANNELS SECTION`: Target channel(s) and voice channels for status update.
- `USER IDS`: User(s) that have access to certain commands.
- `DATABASE_HOST`, `DATABASE_USER`, `DATABASE_PASSWORD`, `DATABASE_NAME`: MySQL connection details (User and Password match what's in docker-compose.yaml)
- Additional variables as listed in .env.example

### 3. Set up Black and pre-commit hooks

```bash
make install-hooks
```

### 4. Start the application using Docker Compose

```bash
make up
```

## Usage

The project is managed through the `Makefile` with the following commands:

* `make help` - Display available commands and descriptions
* `make up` - Start the full application in the specified environment
* `make down` - Stop and clean up all containers, including volumes
* `make stop` - Stop all containers without removing them
* `make logs` - View application logs with tail
* `make run` - Start the application without rebuilding images
* `make install` - Install requirements inside the container (not recommended)
* `make test` - Run tests using pytest
* `make reload` - Reload the Python script and clean log files
* `make install-hooks` - Installes Black and pre-commit hooks

## Discord Bot Commands

### Quest Management
* `!questleiria` - Search quests in Leiria
* `!questmarinha` - Search quests in Marinha Grande
* `!questclear` - Clear the list of quests
* `!scan` - Force a new scan of quests
* `!upcoming` - List scheduled quest rescans
* `!track <reward>` - Add a reward to quest summary tracking
* `!untrack <reward>` - Remove a reward from quest summary tracking
* `!tracklist` - List rewards flagged as important
* `!untrackall` - Clear all tracked rewards

### Account Management
* `!lures` - List accounts with available lures
* `!uselure <username> <number>` - Add or remove lures from a specific account

### System Commands
* `!logs` - Access the bot's logs

### Planned Features
* `!alertas` - List Pokémon included in notification filters
* `!add <pokemon> <channel>` - Add a Pokémon to a specific notification channel
* `!remove <pokemon> <channel>` - Remove a Pokémon from a notification channel
* `!reload` - Submit changes to the notification filters

## Project Structure

* `Makefile` - Build and management instructions
* `.env` - Environment-specific variables
* `docker-compose.yaml` - Docker Compose setup for DEVELOPMENT environment
* `docker-compose.prod.yaml` - Docker Compose setup for PRODUCTION environment
* `cogs/` - Discord command modules (cogs) that extend bot functionality, with each file representing a distinct cog.
* `data/` - Contains project-related static content and persistent data, primarily for quests.
* `logs/` - Stores log files, including actions.log (bot activity) and error.log (errors).
* `mock_data/` - Provides sample JSON data for development and testing.
* `mock_database/` - Contains data.sql, which is used to populate the database with mock data for the DEVELOPMENT environment. This file is excluded from the check-added-large-files pre-commit hook.
* `modules/` - Holds core Python components managing the scanner, database, Discord bot, and other key functionalities.
* `templates/` - Contains template files for dynamic content generation, such as the quests images.
* `tests/` - Houses test scripts to validate application features.

### Core Modules
* `modules/`
  * `database_connector.py` - Connects to and retrieves data from MySQL
  * `event_manager.py` - Manages events from external sources
  * `role_manager.py` - Manages Discord server roles
  * `scanner_status.py` - Updates voice channel names with scanner status
  * `utility.py` - Utility functions for logging, data fetching, and Discord interactions

### Discord Commands
* `cogs/`
  * `quests.py` - Commands for quest search and scan
  * `accounts.py` - Commands for current accounts status

### Legacy Modules (Under Rework)
* `modules/`
  * `events.py` - Legacy event-related logic
  * `poliswag.py` - Legacy notification filters and PvP data
  * `poligpt.py` - Quick mockup of OpenAI API implementation.

## Environments

* **DEVELOPMENT** (default)
  * Database initialized with sample data
  * Mock data used for API calls
  * Logs automatically started

* **PRODUCTION**
  * No database or mock_data actions performed
  * Connects to live services

## Testing

Run the test suite using pytest:

```bash
make test
```

Test files are to be located in the `tests/` directory (Not implemented yet).

## License
[MIT](https://choosealicense.com/licenses/mit/)
