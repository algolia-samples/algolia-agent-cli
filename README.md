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
algolia-agent snapshot <agent_id>      # Write a full config file from the agent's current state
algolia-agent update <agent_id> [options]  # Update an existing agent
algolia-agent publish <agent_id>       # Publish a draft agent
algolia-agent delete <agent_id> --confirm
```

Add `--json` to any command except `init` for machine-readable output.

## Config formats

There are two shapes of `agent-config.json`, and the CLI tells them apart by whether a
`tools` array is present.

**Friendly** — describes an agent with `index`, `replicas`, a `provider` name, an
`instructions` file path, and `{{template}}` variables. Best for provisioning several
agents from one spec. This is what `init` scaffolds.

**Native** — the API's own representation, written by `snapshot`. Carries everything the
service stores, including things the friendly format cannot express: additional tools,
`mode`, `allowUnlistedIndices`, and per-index `searchControls`. A native config is
**literal**: `{{...}}` in a prompt is text to preserve, not a variable to substitute, so
`--var` is rejected. `--index`, `--replica` and `--provider` are rejected too; edit the
file instead.

## Snapshots

`update` sends a whole payload, and the API **replaces** `config`, the `tools` array and
per-index `searchControls` rather than merging them — so anything a config file cannot
express would be deleted. `update` refuses to do that, and `snapshot` is the remedy:

```bash
algolia-agent snapshot <agent_id>                       # -> agent-config.json + PROMPT.md
algolia-agent update <agent_id> --dry-run               # -> "No changes detected."
```

That round-trip is worth running after any snapshot: it is a completeness check. A clean
dry-run means the file holds everything the service does, so editing one value and
applying it changes only that value.

`snapshot` also writes `SYSTEM.md` when the agent has a system prompt, refuses to
overwrite existing files without `--force`, and warns before replacing a prompt file
containing `{{template}}` variables — a snapshot holds rendered text and cannot recover
a local template.

### Cloning an agent

Because `create` also accepts a native config, a snapshot is a complete clone source:

```bash
algolia-agent snapshot <source_id> -o clone/agent-config.json
# edit the name in clone/agent-config.json
algolia-agent create --config clone/agent-config.json
```

The copy carries everything the friendly format cannot express — extra tools, `mode`,
`allowUnlistedIndices`, per-index `searchControls`, the full `config` block. Two
differences by design: `status` is forced to `draft`, since creating from a snapshot of a
live agent should not silently publish the copy, and a native config must carry
`providerId` directly (there is no provider-name lookup on that path — use
`algolia-agent providers` to find one).

Snapshot each agent into its own directory. `PROMPT.md` is the default prompt filename,
so two snapshots in one directory collide — `snapshot` refuses rather than overwriting.

Agent Studio's own templates ship prompts containing placeholders such as
`{{INSERT_BRAND}}` and `{{INSERT_LANGUAGE}}`. A snapshot preserves them verbatim, since
a native config is literal — fill them in by editing `PROMPT.md` and running `update`
again. `--var` is rejected on a native config precisely so that an update cannot
overwrite them.

Note that a snapshot is a point-in-time copy. If someone edits the agent in the
dashboard afterwards, applying an older snapshot reverts them; `--dry-run` shows exactly
what would change before you commit to it.

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

# fuzzy selector — arrow keys to browse, type to filter
? Select a provider: hackathon-gemini
? Select a model: gemini-2.5-flash

Agent name (use {{vars}} for dynamic values) [My Agent]: My Agent for {{event_name}}
Instructions file [PROMPT.md]:

# fuzzy selector — includes <no index — create without tools> at top
? Primary index (arrow keys to browse, Enter to select): products_{{event_id}}
Primary index description [Search index for products_{{event_id}}.]: Product catalog for {{event_name}}.

# fuzzy selector — includes <done — no more replicas> and <custom name>
? Add a replica index: products_{{event_id}}_price_asc
  Replica description [Replica index of Product catalog for {{event_name}}.]: Sorted by price ascending.

? Add a replica index: <done — no more replicas>

Set up searchControls to limit hits or restrict attributes? [N]: y
  Cap hitsPerPage? Enter max (or leave blank to skip): 10
  Cap page? Enter max (or leave blank to skip):
  Restrict attributesToRetrieve? Enter comma-separated list (or leave blank to skip): objectID, name, price
  Enable facets? Enter comma-separated list (or leave blank to skip):
  Restrict responseFields? Enter comma-separated list (or leave blank to skip):

✓ agent-config.json
✓ PROMPT.md

Next steps:
  1. Edit PROMPT.md with your agent instructions
  2. Run: algolia-agent create --var event_id=VALUE --var event_name=VALUE
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
  },
  "searchControls": {
    "hitsPerPage": {
      "exposed": true,
      "default": 5,
      "constraint": { "max": 5 }
    },
    "attributesToRetrieve": {
      "exposed": false,
      "default": ["objectID", "name", "price", "image_url"]
    }
  }
}
```

`searchControls` constrains what the LLM can do at query time. It is applied to every index (primary + replicas). Supported fields:

| Field | What it does |
|---|---|
| `hitsPerPage` | Limit result count. Set `constraint.max` to cap it. |
| `page` | Limit pagination depth. Set `constraint.max` to cap it. |
| `attributesToRetrieve` | Restrict which attributes are returned in each hit. Useful for limiting the LLM payload to only the fields it needs. |
| `facets` | Control which facet attributes are returned in the response. |
| `responseFields` | Restrict which top-level response fields are returned. |

Set `exposed: true` to let the LLM vary the value within the constraint; `exposed: false` to fix it.

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

Required: `--name`, `--provider`, `--model`, `--instructions`
Optional: `--index`, `--replica` (repeatable), `config` block (file only)

## Template variables

`{{variable_name}}` placeholders work anywhere in `agent-config.json` and in your
instructions file. Both are scanned together in a single pass — missing vars are
reported all at once. Supply values with `--var key=value` (repeatable).

A typical setup has placeholders in the config (index names, agent name) and in
the prompt (event-specific context):

**`agent-config.json`**
```json
{
  "name": "My Agent for {{event_name}}",
  "index": "products_{{event_id}}"
}
```

**`PROMPT.md`**
```markdown
You are a helpful assistant at {{event_name}} (booth {{booth}}).
Use the search tool to answer questions about available products.
```

```bash
# All vars via CLI (pipeline-safe)
algolia-agent create --config agent-config.json \
    --var event_id=spring-2026 \
    --var event_name="Spring Conference 2026" \
    --var booth=701 \
    --json

# Missing vars → interactive prompt (TTY only)
algolia-agent create --config agent-config.json

# Missing vars in non-interactive context → error
algolia-agent create --config agent-config.json --json
# ERROR: missing required template variables: event_id, event_name, booth
# Supply them with: --var event_id=VALUE --var event_name=VALUE --var booth=VALUE
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
pip install -e ".[dev]"
pytest tests/ -v
```
