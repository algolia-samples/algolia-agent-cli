# algolia-agent

A minimal CLI for the [Algolia Agent Studio](https://www.algolia.com/doc/guides/algolia-ai/agent-studio/) REST API.

## Install

```bash
pip install -e .
```

## Credentials

Priority order: CLI flags → environment variables → `.env` in current directory.

```bash
# Environment variables
export ALGOLIA_APP_ID=YOURAPPID
export ALGOLIA_API_KEY=yourapikey

# Or .env file
echo "ALGOLIA_APP_ID=YOURAPPID" >> .env
echo "ALGOLIA_API_KEY=yourapikey" >> .env

# Or CLI flags (useful in scripts)
algolia-agent --app-id YOURAPPID --api-key yourapikey list
```

## Commands

```bash
algolia-agent init [--output-dir .]    # Scaffold agent-config.json + PROMPT.md interactively
algolia-agent list                     # List all agents
algolia-agent get <agent_id>           # Full agent config
algolia-agent providers                # List available LLM providers
algolia-agent create [options]         # Create a draft agent
algolia-agent update <agent_id> [options]  # Update an existing agent
algolia-agent publish <agent_id>       # Publish a draft agent
algolia-agent delete <agent_id> --confirm
```

Add `--json` to any command except `init` for machine-readable output.

## Getting started: `init`

The fastest way to get started is `algolia-agent init`. It checks for credentials
(prompting and optionally saving to `.env` if they're missing), fetches your available
providers, and walks you through building `agent-config.json` and a starter `PROMPT.md`:

```
$ algolia-agent init

No Algolia credentials found.

Algolia App ID: YOURAPPID
Algolia API Key: ****
Save credentials to .env? [Y/n]: Y
  ✓ .env

Fetching available providers...

  [1] hackathon-gemini
  [2] openai

Provider: 1
Model [gemini-2.5-flash]:
Agent name (use {{vars}} for dynamic values) [My Agent]: My Agent for {{event_name}}
Instructions file [PROMPT.md]:
Primary index name (use {{vars}} for dynamic values): products_{{event_id}}
Primary index description: Product catalog for {{event_name}}.

Add a replica index? [y/N]: y
  Replica index name: products_{{event_id}}_price_asc
  Replica description [products_{{event_id}}_price_asc]: Sorted by price ascending.

Add a replica index? [y/N]: N

  ✓ agent-config.json
  ✓ PROMPT.md

Next steps:
  1. Edit PROMPT.md with your agent instructions
  2. Run: algolia-agent create --config agent-config.json --var event_id=VALUE --var event_name=VALUE
```

## Creating an agent

### File-based (recommended)

Put all settings in `agent-config.json`, using `{{variable}}` placeholders for anything
that changes between runs. Template variables are resolved across both the config file
and the instructions file in a single pass — missing vars are reported together.

```json
{
  "name": "My Agent for {{event_name}}",
  "provider": "hackathon-gemini",
  "model": "gemini-2.5-flash",
  "instructions": "PROMPT.md",
  "index": "products_{{event_id}}",
  "index_description": "Product catalog for {{event_name}}. Use for search and inventory queries.",
  "replicas": [
    {
      "index": "products_{{event_id}}_price_asc",
      "description": "Products sorted by price ascending (lowest first)."
    },
    {
      "index": "products_{{event_id}}_price_desc",
      "description": "Products sorted by price descending (highest first)."
    }
  ],
  "config": {
    "suggestions": { "enabled": true }
  }
}
```

```bash
algolia-agent create \
    --config agent-config.json \
    --var event_id=spring-2026 \
    --var event_name="Spring Conference 2026" \
    --var booth=701
```

> **Note:** The Agent Studio API requires a `description` on every index entry. The
> `index_description` key sets the description for the primary index; each replica object
> must include a `description` field. If omitted, the index name is used as a fallback.

### CLI-only

```bash
algolia-agent create \
    --name "My Agent" \
    --provider "hackathon-gemini" \
    --model "gemini-2.5-flash" \
    --instructions PROMPT.md \
    --index products
```

### Flag resolution order

```
--flag  >  agent-config.json  >  interactive prompt (TTY)  >  error
```

Required: `name`, `provider`, `model`, `instructions`, `index`
Optional: `--replica` (repeatable), `config` block (file only)

## Template variables

The `--instructions` file is scanned for `{{variable_name}}` placeholders. Supply
values with `--var key=value` (repeatable).

```bash
# All vars via CLI (pipeline-safe)
algolia-agent create --config agent-config.json \
    --var event_name="eTail Palm Springs 2026" \
    --var booth=701 \
    --json

# Missing vars → interactive prompt (TTY only)
algolia-agent create --config agent-config.json

# Missing vars in non-interactive context → error
algolia-agent create --config agent-config.json --json
# ERROR: missing required template variables: event_name, booth
# Supply them with: --var event_name=VALUE --var booth=VALUE
```

## Updating agents

Use `update` to push changes to an existing agent. It fetches the current agent state,
applies your config/flags on top, and sends a PUT. Fields not specified are preserved
from the current agent.

```bash
# Update instructions and re-render template vars
algolia-agent update <agent_id> \
    --config agent-config.json \
    --var event_name="Spring Conference 2026" \
    --var event_id=spring-2026

# See what would change before updating (dry run)
algolia-agent update <agent_id> \
    --config agent-config.json \
    --var event_name="Spring Conference 2026" \
    --var event_id=spring-2026 \
    --dry-run

# Update and publish in one step
algolia-agent update <agent_id> --config agent-config.json \
    --var event_name="Spring Conference 2026" \
    --var event_id=spring-2026 \
    --publish
```

The `--dry-run` output shows a diff: which fields changed (name, model, instructions
line count, index descriptions).

## Dry run

Preview the resolved config and rendered instructions without making any API calls:

```bash
algolia-agent create --config agent-config.json \
    --var event_name="Test Event" --var booth=701 \
    --dry-run
```

## Pipeline usage

```bash
AGENT_ID=$(algolia-agent create \
    --config agent-config.json \
    --name "My Agent" \
    --index products \
    --var event_name="Test" --var booth=1 \
    --json | python -c "import sys,json; print(json.load(sys.stdin)['id'])")

algolia-agent publish "$AGENT_ID" --json
```

## Exit codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | API / HTTP error |
| 2 | Validation / input error |

## Examples

See [`examples/tcg/`](examples/tcg/) for a reference implementation using a Pokemon
card vending machine agent with per-event index names and template variables.

## Running tests

```bash
pip install pytest pick
pytest tests/ -v
```
