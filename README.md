# algolia-agent

A minimal CLI for the [Algolia Agent Studio](https://www.algolia.com/doc/guides/algolia-ai/agent-studio/) REST API.

Zero dependencies — uses only Python stdlib.

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
algolia-agent list                     # List all agents
algolia-agent get <agent_id>           # Full agent config
algolia-agent providers                # List available LLM providers
algolia-agent create [options]         # Create a draft agent
algolia-agent publish <agent_id>       # Publish a draft agent
algolia-agent delete <agent_id> --confirm
```

Add `--json` to any command for machine-readable output.

## Creating an agent

### File-based (recommended)

Put shared settings in `agent-config.json` and pass dynamic values as CLI flags:

```json
{
  "provider": "hackathon-gemini",
  "model": "gemini-2.5-flash",
  "instructions": "PROMPT.md",
  "config": {
    "suggestions": { "enabled": true }
  }
}
```

```bash
algolia-agent create \
    --config agent-config.json \
    --name "My Agent" \
    --index products \
    --replica products_price_asc \
    --replica products_price_desc \
    --var event_name="eTail Palm Springs 2026" \
    --var booth=701
```

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
pip install pytest
pytest tests/ -v
```
