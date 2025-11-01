# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Magentic Marketplace is a Python SDK for running simulations of agentic markets. The system enables business and customer agents to interact in a marketplace environment, transact, and evaluate market welfare through experimentation.

Documentation: https://microsoft.github.io/multi-agent-marketplace/

This is a research project from Microsoft exploring how AI agents behave in market environments to understand agent biases, market efficiency, and potential malicious behavior.

## Essential Development Commands

### Environment Setup
```bash
# Install dependencies (requires uv: https://docs.astral.sh/uv/)
uv sync --all-extras

# Activate virtual environment
source .venv/bin/activate

# Configure environment variables
cp sample.env .env
# Edit .env with your API keys (OPENAI_API_KEY, ANTHROPIC_API_KEY, or GEMINI_API_KEY)

# Start PostgreSQL database
docker compose up -d
```

### Running Experiments
```bash
# Run experiment with data directory
magentic-marketplace run data/mexican_3_9 --experiment-name test_exp

# Analyze results (saves analytics_results_<name>.json)
magentic-marketplace analyze test_exp

# Analyze with fuzzy matching (tolerates typos in menu items)
magentic-marketplace analyze test_exp --fuzzy-match-distance 2

# List all experiments in PostgreSQL
magentic-marketplace list

# Launch interactive visualizer (web UI)
magentic-marketplace ui test_exp

# Export experiment from PostgreSQL to SQLite file
magentic-marketplace export test_exp -o ./exports

# Run from Python script
uv run experiments/example.py
```

### Testing
```bash
# Run all tests
pytest

# Run tests with specific markers
pytest -m postgres      # PostgreSQL tests
pytest -m rnr          # Retrieve-and-rank tests
pytest -m "not skip_ci" # Skip CI-excluded tests

# Run specific test file
pytest packages/magentic-marketplace/tests/platform/test_launcher.py

# Run with verbose output
pytest -v
```

### Code Quality
```bash
# Check all (format, lint, type, spell)
poe check-all

# Fix formatting and linting
poe fix-all

# Individual checks
poe format      # Check formatting
poe format-fix  # Fix formatting
poe lint        # Check linting
poe lint-fix    # Fix linting with unsafe fixes
poe type        # Type checking with pyright
poe spell       # Spell checking
```

## Architecture Overview

### High-Level Structure

The codebase follows a monorepo pattern managed by `uv` workspace:
- `packages/magentic-marketplace/` - Core SDK package (Python)
- `packages/marketplace-visualizer/` - Web-based visualization tool (React/TypeScript)
- CLI entry point: `magentic_marketplace.cli:main` (defined in package pyproject.toml)

### Core Components

**Platform Layer** (`magentic_marketplace.platform`):
- **MarketplaceLauncher**: Orchestrates server startup, database initialization, and agent coordination
- **MarketplaceServer**: FastAPI-based HTTP server exposing marketplace REST APIs
- **MarketplaceClient**: HTTP client for agents to communicate with the marketplace
- **BaseDatabaseController**: Abstraction over SQLite and PostgreSQL for experiment data storage
- **MarketplaceLogger**: Structured logging system for marketplace events

**Marketplace Layer** (`magentic_marketplace.marketplace`):
- **Agents**: Business and customer agents that interact in the marketplace
  - `BusinessAgent`: Responds to customer queries with proposals
  - `CustomerAgent`: Searches for services, evaluates proposals, makes purchases
- **Actions**: Marketplace operations (Search, SendMessage, FetchMessages)
- **Protocol**: `SimpleMarketplaceProtocol` defines marketplace rules and action execution logic

**Experiments** (`magentic_marketplace.experiments`):
- **run_experiment.py**: Main experiment runner that loads YAML configs and executes simulations
- **run_analytics.py**: Post-experiment analysis computing welfare metrics (see Utility Calculation below)
- **run_audit.py**: Validates experiment integrity (e.g., proposal delivery)
- **export_experiment.py**: Converts PostgreSQL experiments to SQLite files

**Analytics & Utility Calculation**:
- Customer utility formula: `utility = match_score - total_payments`
  - `match_score = 2 × Σ(customer.menu_features.values())` (only counted once if needs met)
  - Needs are "met" if: (1) all requested menu items are in proposal AND (2) all required amenities match
- Business utility: Total revenue received from payments
- Market welfare: Sum of all customer utilities
- Invalid proposals tracked: wrong menu items, incorrect prices, calculation errors (with Levenshtein distance for fuzzy matching)

### Key Architectural Patterns

**Agent-Marketplace Communication**:
1. Agents use `MarketplaceClient` to send HTTP requests to marketplace server
2. Server validates requests and delegates to protocol's action executors
3. Protocol interacts with database to record actions and query state
4. Results flow back through server to agents

**Database Schema**:
- Each experiment creates a separate PostgreSQL schema (or SQLite file)
- Core tables: `agents`, `actions`, `logs`
- Actions table stores all marketplace interactions as JSONB with composite indexes for efficient queries

**YAML Configuration**:
- Experiments are configured via YAML files in `data/` directory
- Each directory must contain `businesses/` and `customers/` subdirectories
- Business YAML: id, name, description, rating, menu_features (item -> price mapping), amenity_features (feature -> boolean)
- Customer YAML: id, name, request, menu_features (item -> willingness-to-pay), amenity_features (required features list)
- Example: `data/mexican_3_9/` contains 3 customers and 9 Mexican restaurant businesses

**LLM Integration**:
- Agents use LLM providers (OpenAI, Anthropic, Google) for decision-making
- Configure via environment variables: `LLM_PROVIDER`, `LLM_MODEL`, `LLM_REASONING_EFFORT`, `LLM_MAX_CONCURRENCY`
- Agent prompts defined in `marketplace/agents/{business,customer}/prompts.py`
- All LLM calls are logged to database for analysis (success/failure, tokens, latency)

### Important Implementation Details

**AsyncIO Patterns**:
- Platform uses async/await extensively with context managers
- `MarketplaceLauncher` implements async context manager protocol
- `AgentLauncher` coordinates parallel agent execution with semaphore-based concurrency control

**Database Abstraction**:
- Two implementations: PostgreSQL (production) and SQLite (export/testing)
- PostgreSQL uses schemas for multi-tenancy (one schema per experiment)
- Functional JSONB indexes on actions table optimize queries on `to_agent_id`, `from_agent_id`, and `request.name`
- Database initialized via `BaseDatabaseController` with protocol-specific indexes in `protocol.initialize()`

**Testing Strategy**:
- Uses pytest with async support (pytest-asyncio)
- Markers distinguish test types: `skip_ci`, `rnr`, `postgres`
- Test environment configured via `dev.env` file

## Common Development Workflows

### Adding New Marketplace Actions
1. Define action model in `marketplace/actions/actions.py` (inherit from `BaseAction`)
2. Implement executor function in `marketplace/protocol/` (e.g., `execute_my_action.py`)
3. Register in `SimpleMarketplaceProtocol.get_actions()` method
4. Add action execution logic to `protocol.execute_action()` method
5. Add tests in `packages/magentic-marketplace/tests/protocol/`

### Creating Custom Agent Types
1. Inherit from `BaseMarketplaceAgent` in `marketplace/agents/base.py`
2. Implement `run()` method with agent logic loop
3. Define prompts in new `prompts.py` file
4. Create YAML loader utility in `experiments/utils/`
5. Register agent in experiment runner

### Running Custom Experiments
1. Create data directory structure: `my_experiment/{businesses,customers}/`
2. Populate with YAML files following existing format (see `data/mexican_3_9/` for examples)
3. Run: `magentic-marketplace run my_experiment --experiment-name my_exp`
4. Analyze: `magentic-marketplace analyze my_exp`
5. Results saved to `analytics_results_my_exp.json`

### Debugging Experiments
1. Check experiment logs: `magentic-marketplace list` shows recent activity timestamps
2. Use `--fuzzy-match-distance N` in analyze command to tolerate menu item typos
3. Export to SQLite for easier querying: `magentic-marketplace export my_exp -o ./exports`
4. Launch visualizer UI for interactive exploration: `magentic-marketplace ui my_exp`
5. Inspect database directly: Tables are `agents`, `actions`, `logs` in schema `my_exp`

## Environment Configuration

Key environment variables (see `sample.env`):
- **LLM Settings**:
  - `LLM_PROVIDER`: "openai", "anthropic", or "google"
  - `LLM_MODEL`: Model name (e.g., "gpt-5-nano", "claude-3-5-sonnet-20241022")
  - `LLM_REASONING_EFFORT`: "minimal", "low", "medium", "high" (for reasoning models)
  - `LLM_MAX_CONCURRENCY`: Max concurrent LLM requests (default: 64)
  - `LLM_TEMPERATURE`: Temperature for generation (optional)
  - `LLM_MAX_TOKENS`: Max tokens to generate (optional)
- **Database Settings**:
  - `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`: PostgreSQL credentials
  - `POSTGRES_MAX_CONNECTIONS`: Connection pool size (default: 100)
- **API Keys**:
  - `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`

## Code Style Notes

- Line length: 88 characters (ruff/black standard)
- Python version: 3.11+
- Docstring style: Google-style docstrings (enforced by ruff rule D)
- Import sorting: isort with first-party as `agentic_economics`
- Type hints: Required for public APIs, checked with pyright
