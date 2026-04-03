# Tommy

Tommy is a Discord-to-GitHub orchestrator bot. It enables users to interact with GitHub repositories directly from Discord, including code scanning, security analysis, project management, PR author assignment, and issue tracking.

## Project Structure

- `src/bot.py` - Main Discord bot (TommyBot extends commands.Bot)
- `src/api/tommy_api.py` - OpenAI-compatible REST API
- `src/services/` - Core services (pollinations, webhooks, code agent, sandbox)
- `src/services/code_agent/tools/tommy_agent.py` - GitHub code tool handler
- `.github/workflows/` - CI/CD workflows for PR review, issue assist, project management
- `tests/` - Test suite

## Key Conventions

- Bot name is **Tommy** everywhere (code, configs, docs, workflows)
- The trigger phrase in GitHub workflows is `tommy` (lowercase)
- Docker sandbox container is named `tommy_sandbox`
- API model name is `tommy`
- The bot is designed to be general-purpose and open source

## Embeddings

Tommy uses a unified embedding provider configured in `config.json` under `"embeddings"`. Two modes:

- **`"provider": "api"`** — Uses any OpenAI-compatible embedding API. Set `model`, `api_base_url` in config.json, and `EMBEDDINGS_API_KEY` in `.env`.
- **`"provider": "local"`** — Runs a sentence_transformers model on the host machine. Set `model` to a HuggingFace model ID or local filesystem path (no API key needed).

All three embedding subsystems (code, docs, session) share the same provider via `src/services/embeddings_utils.py`.

## Development

- Python 3.10+
- Discord.py for bot framework
- FastAPI for the HTTP API
- Docker for sandboxed code execution
