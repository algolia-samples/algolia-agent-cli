#!/usr/bin/env python3
"""
Algolia Agent Studio CLI

Commands:
  init                    Scaffold agent-config.json and PROMPT.md interactively
  list                    List all agents
  get <agent_id>          Get full agent config
  providers               List available LLM providers
  create                  Create a draft agent
  publish <agent_id>      Publish a draft agent
  delete <agent_id>       Delete an agent
"""

import argparse
import getpass
import json
import sys
from pathlib import Path

from InquirerPy import inquirer

from .client import AgentAPIError, AlgoliaAgentClient
from .template import extract_variables, render


def load_config(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        raise SystemExit(f"ERROR: Config file not found: {path}", )
    try:
        with open(p) as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        raise SystemExit(f"ERROR: Invalid JSON in {path}: {e}")


def merge_config(file_config: dict, args: argparse.Namespace) -> dict:
    """Merge file config with CLI flags. CLI flags always win."""
    merged = dict(file_config)
    for key in ("name", "provider", "model", "instructions", "index"):
        val = getattr(args, key, None)
        if val is not None:
            merged[key] = val
    # --replica is a list; CLI replaces file config replicas entirely if provided
    if getattr(args, "replica", None):
        merged["replicas"] = args.replica
    return merged


def resolve_vars(instructions_text: str, cli_vars: dict) -> dict:
    """Resolve template variables: CLI --var flags, then interactive prompt if TTY."""
    needed = extract_variables(instructions_text)
    resolved = dict(cli_vars)
    missing = [v for v in needed if v not in resolved]

    if not missing:
        return resolved

    if sys.stdin.isatty():
        for var in missing:
            resolved[var] = input(f"Enter value for '{var}': ").strip()
    else:
        var_flags = " ".join(f"--var {v}=VALUE" for v in missing)
        raise SystemExit(
            f"ERROR: missing required template variables: {', '.join(missing)}\n"
            f"Supply them with: {var_flags}"
        )
    return resolved


def parse_vars(var_list: list[str]) -> dict:
    """Parse ['key=value', ...] into a dict."""
    result = {}
    for item in var_list or []:
        if "=" not in item:
            raise SystemExit(f"ERROR: --var must be in key=value format, got: {item!r}")
        key, _, value = item.partition("=")
        result[key.strip()] = value
    return result


def build_tool(config: dict) -> dict:
    """Build the algolia_search_index tool payload.

    The API requires a description on each index entry. Config may supply
    them as {"index": "name", "description": "..."} objects; plain strings
    fall back to using the index name as the description.
    """
    def _index_entry(raw) -> dict:
        if isinstance(raw, dict):
            return {"index": raw["index"], "description": raw.get("description", raw["index"])}
        return {"index": raw, "description": raw}

    primary_desc = config.get("index_description", config["index"])
    indices = [{"index": config["index"], "description": primary_desc}]
    for r in config.get("replicas", []):
        indices.append(_index_entry(r))

    return {
        "name": "algolia_search_index",
        "type": "algolia_search_index",
        "indices": indices,
    }


# ── Output helpers ──────────────────────────────────────────────────────────

def _out(data: dict | list, as_json: bool):
    if as_json:
        print(json.dumps(data, indent=2))
    return data


def _format_agent(agent: dict):
    status_indicator = "●" if agent["status"] == "published" else "○"
    print(f"{status_indicator} {agent['name']}")
    print(f"  ID:      {agent['id']}")
    print(f"  Status:  {agent['status']}")
    print(f"  Model:   {agent.get('model') or '(not set)'}")
    tools = agent.get("tools", [])
    if tools:
        for tool in tools:
            indices = [i["index"] for i in tool.get("indices", [])]
            print(f"  Tool:    {tool['type']} → {', '.join(indices)}")
    print(f"  Updated: {agent['updatedAt'][:10]}")
    print()


# ── Commands ────────────────────────────────────────────────────────────────

def cmd_list(client: AlgoliaAgentClient, args: argparse.Namespace):
    agents = client.list_agents()
    if args.json:
        print(json.dumps(agents, indent=2))
        return
    if not agents:
        print("No agents found.")
        return
    for agent in agents:
        _format_agent(agent)


def cmd_get(client: AlgoliaAgentClient, args: argparse.Namespace):
    agent = client.get_agent(args.agent_id)
    if args.json:
        print(json.dumps(agent, indent=2))
        return

    print(f"Name:        {agent['name']}")
    print(f"ID:          {agent['id']}")
    print(f"Status:      {agent['status']}")
    print(f"Model:       {agent.get('model') or '(not set)'}")
    print(f"Created:     {agent['createdAt'][:10]}")
    print(f"Updated:     {agent['updatedAt'][:10]}")

    tools = agent.get("tools", [])
    if tools:
        print(f"\nTools ({len(tools)}):")
        for tool in tools:
            print(f"  - {tool['type']}")
            for idx in tool.get("indices", []):
                lines = idx.get("description", "").splitlines()
                print(f"      {idx['index']}: {lines[0] if lines else '(no description)'}")

    print(f"\nInstructions:\n{'-' * 60}")
    print(agent.get("instructions") or "(none)")

    config = agent.get("config", {})
    if config:
        print(f"\nConfig:\n{json.dumps(config, indent=2)}")


def cmd_providers(client: AlgoliaAgentClient, args: argparse.Namespace):
    providers = client.list_providers()
    if args.json:
        print(json.dumps(providers, indent=2))
        return
    if not providers:
        print("No providers found.")
        return
    for provider in providers:
        print(f"  {provider['name']}")
        print(f"    ID:       {provider['id']}")
        print(f"    Provider: {provider.get('providerName', '(unknown)')}")
        print()


def cmd_create(client: AlgoliaAgentClient, args: argparse.Namespace):
    # Load and merge config; auto-detect agent-config.json if --config not given
    config_path = args.config or (Path("agent-config.json") if Path("agent-config.json").exists() else None)
    file_config = load_config(config_path) if config_path else {}
    config = merge_config(file_config, args)

    # Auto-detect PROMPT.md if instructions not specified
    if not config.get("instructions") and Path("PROMPT.md").exists():
        config["instructions"] = "PROMPT.md"

    # Validate required fields (pre-rendering)
    required = ["name", "provider", "model", "instructions"]
    missing = [k for k in required if not config.get(k)]
    if missing:
        raise SystemExit(
            f"ERROR: missing required fields: {', '.join(missing)}\n"
            f"Provide them via --config, CLI flags, or both."
        )

    # Load instructions file
    instructions_path = Path(config["instructions"])
    if not instructions_path.exists():
        if args.config:
            instructions_path = Path(args.config).parent / config["instructions"]
    if not instructions_path.exists():
        raise SystemExit(f"ERROR: instructions file not found: {config['instructions']}")

    instructions_template = instructions_path.read_text()

    # Resolve template variables across BOTH config (serialized) and instructions
    # in a single pass — missing vars are reported together regardless of source.
    config_json = json.dumps(config)
    cli_vars = parse_vars(getattr(args, "var", None) or [])
    variables = resolve_vars(config_json + "\n" + instructions_template, cli_vars)

    # Render config with JSON-safe values (escape quotes/backslashes so the
    # substitution doesn't break the serialized JSON string).
    json_safe_vars = {k: v.replace("\\", "\\\\").replace('"', '\\"') for k, v in variables.items()}
    config = json.loads(render(config_json, json_safe_vars))

    # Render instructions with raw values
    instructions = render(instructions_template, variables)

    # Build tool from rendered config (only if index is provided)
    tool = build_tool(config) if config.get("index") else None

    if args.dry_run:
        print("=== DRY RUN ===")
        print(f"\nResolved config:")
        dry_config = {k: config[k] for k in required if config.get(k)}
        if config.get("index"):
            dry_config["index"] = config["index"]
        if config.get("replicas"):
            dry_config["replicas"] = config["replicas"]
        print(json.dumps(dry_config, indent=2))
        if tool:
            print(f"\nTool payload:")
            print(json.dumps(tool, indent=2))
        else:
            print("\nNo tools configured.")
        print(f"\n--- Rendered instructions ---\n{instructions}")
        return

    # Resolve provider name → UUID
    provider_id = client.resolve_provider_id(config["provider"])

    payload = {
        "name": config["name"],
        "providerId": provider_id,
        "model": config["model"],
        "instructions": instructions,
        "status": "draft",
        "tools": [tool] if tool else [],
    }
    if config.get("config"):
        payload["config"] = config["config"]

    agent = client.create_agent(payload)

    if args.json:
        print(json.dumps({"id": agent["id"], "name": agent["name"], "status": agent["status"]}))
        return

    print(f"Created agent: {agent['name']}")
    print(f"Agent ID:      {agent['id']}")
    print(f"Status:        {agent['status']}")
    print(f"\nTo publish: algolia-agent publish {agent['id']}")


def _diff(current: dict, new_payload: dict) -> list[str]:
    """Return human-readable lines describing what would change."""
    lines = []

    for field in ("name", "model"):
        curr = current.get(field, "")
        new = new_payload.get(field, "")
        if curr != new:
            lines.append(f"  {field}: {curr!r} → {new!r}")

    curr_instr = current.get("instructions", "")
    new_instr = new_payload.get("instructions", "")
    if curr_instr != new_instr:
        lines.append(
            f"  instructions: changed "
            f"({len(curr_instr.splitlines())} lines → {len(new_instr.splitlines())} lines)"
        )

    curr_idx = {
        i["index"]: i.get("description", "")
        for t in current.get("tools", [])
        for i in t.get("indices", [])
    }
    new_idx = {
        i["index"]: i.get("description", "")
        for t in new_payload.get("tools", [])
        for i in t.get("indices", [])
    }
    if curr_idx != new_idx:
        lines.append("  indices:")
        for idx in sorted(set(curr_idx) | set(new_idx)):
            if idx not in curr_idx:
                lines.append(f"    + {idx!r}: {new_idx[idx]!r}")
            elif idx not in new_idx:
                lines.append(f"    - {idx!r}")
            elif curr_idx[idx] != new_idx[idx]:
                lines.append(f"    ~ {idx!r}")
                lines.append(f"        was: {curr_idx[idx]!r}")
                lines.append(f"        now: {new_idx[idx]!r}")

    return lines


def cmd_update(client: AlgoliaAgentClient, args: argparse.Namespace):
    current = client.get_agent(args.agent_id)

    # Load and merge config; auto-detect agent-config.json if --config not given
    config_path = args.config or (Path("agent-config.json") if Path("agent-config.json").exists() else None)
    file_config = load_config(config_path) if config_path else {}
    config = merge_config(file_config, args)

    # Fill in any fields not provided from the current agent state
    if not config.get("name"):
        config["name"] = current["name"]
    if not config.get("model"):
        config["model"] = current.get("model", "")
    if not config.get("index"):
        # Infer from current tools if possible
        indices = [
            i["index"]
            for t in current.get("tools", [])
            for i in t.get("indices", [])
        ]
        if indices:
            config["index"] = indices[0]
            config.setdefault("replicas", [
                {"index": i["index"], "description": i.get("description", i["index"])}
                for t in current.get("tools", [])
                for i in t.get("indices", [])[1:]
            ])

    # Render template vars across config + instructions (if instructions provided)
    instructions = current.get("instructions", "")
    if config.get("instructions"):
        instructions_path = Path(config["instructions"])
        if not instructions_path.exists() and args.config:
            instructions_path = Path(args.config).parent / config["instructions"]
        if instructions_path.exists():
            instructions_template = instructions_path.read_text()
            config_json = json.dumps(config)
            cli_vars = parse_vars(getattr(args, "var", None) or [])
            variables = resolve_vars(config_json + "\n" + instructions_template, cli_vars)
            config = json.loads(render(config_json, variables))
            instructions = render(instructions_template, variables)
        else:
            raise SystemExit(f"ERROR: instructions file not found: {config['instructions']}")
    elif getattr(args, "var", None):
        # Vars provided but no instructions file — render config only
        config_json = json.dumps(config)
        cli_vars = parse_vars(args.var)
        variables = resolve_vars(config_json, cli_vars)
        config = json.loads(render(config_json, variables))

    if config.get("index"):
        tool = build_tool(config)
    else:
        existing_tools = current.get("tools", [])
        if not existing_tools or not existing_tools[0].get("indices"):
            raise SystemExit(
                "ERROR: no index defined. Provide --index or --config with an index key."
            )
        tool = existing_tools[0]

    # Resolve provider: only call API if provider changed
    current_provider_id = current.get("providerId", "")
    if config.get("provider"):
        provider_id = client.resolve_provider_id(config["provider"])
    else:
        provider_id = current_provider_id

    new_payload = {
        "name": config.get("name", current["name"]),
        "providerId": provider_id,
        "model": config.get("model", current.get("model", "")),
        "instructions": instructions,
        "status": current.get("status", "draft"),
        "tools": [tool],
    }
    cfg_block = config.get("config") or current.get("config")
    if cfg_block:
        new_payload["config"] = cfg_block

    if args.dry_run:
        changes = _diff(current, new_payload)
        print(f"=== UPDATE DRY RUN: {args.agent_id} ===")
        print(f"  Agent: {current['name']}")
        if changes:
            print("\nChanges:")
            print("\n".join(changes))
        else:
            print("\n  No changes detected.")
        return

    agent = client.update_agent(args.agent_id, new_payload)

    if args.json:
        print(json.dumps({"id": agent["id"], "name": agent["name"], "status": agent["status"]}))
        return

    print(f"Updated agent: {agent['name']}")
    print(f"Agent ID:      {agent['id']}")
    print(f"Status:        {agent['status']}")

    if getattr(args, "publish", False):
        agent = client.publish_agent(args.agent_id)
        if args.json:
            print(json.dumps({"id": agent["id"], "name": agent["name"], "status": agent["status"]}))
        else:
            print(f"Published:     {agent['status']}")


def cmd_publish(client: AlgoliaAgentClient, args: argparse.Namespace):
    agent = client.publish_agent(args.agent_id)
    if args.json:
        print(json.dumps({"id": agent["id"], "name": agent["name"], "status": agent["status"]}))
        return
    print(f"Published agent: {agent['name']}")
    print(f"Agent ID:        {agent['id']}")
    print(f"Status:          {agent['status']}")


def cmd_delete(client: AlgoliaAgentClient, args: argparse.Namespace):
    if not args.confirm:
        raise SystemExit(
            f"ERROR: add --confirm to delete agent {args.agent_id}"
        )
    result = client.delete_agent(args.agent_id)
    if args.json:
        print(json.dumps(result))
        return
    print(f"Deleted agent: {args.agent_id}")


_STARTER_PROMPT = """\
You are a helpful assistant with access to a product search tool.

Use the search tool to answer questions about available products or inventory.
If the user asks for something not available in the index, say so clearly.

Reply in the user's language, falling back to English.
"""


def _select(message: str, choices: list) -> str:
    """Fuzzy selector: arrow keys to browse, type to filter. Raises SystemExit on cancel."""
    try:
        return inquirer.fuzzy(message=message, choices=choices, max_height="40%", border=True, amark="✓").execute()
    except KeyboardInterrupt:
        raise SystemExit("Aborted.")


def _ask(prompt: str, default: str = "") -> str:
    """Prompt the user for input, showing default in brackets."""
    display = f"{prompt} [{default}]: " if default else f"{prompt}: "
    val = input(display).strip()
    return val or default



def _resolve_credentials_interactively(args: argparse.Namespace) -> AlgoliaAgentClient:
    """Try to build a client from existing credentials; prompt and optionally
    save to .env if they're missing."""
    try:
        return AlgoliaAgentClient(
            app_id=getattr(args, "app_id", None),
            api_key=getattr(args, "api_key", None),
        )
    except ValueError:
        pass

    print("No Algolia credentials found.\n")
    app_id = _ask("Algolia App ID")
    api_key = getpass.getpass("Algolia API Key: ")
    if not app_id or not api_key:
        raise SystemExit("ERROR: App ID and API Key are required.")

    save = _ask("Save credentials to .env?", "Y")
    if save.lower() != "n":
        env_path = Path(".env")
        lines = env_path.read_text().splitlines() if env_path.exists() else []
        # Remove existing entries, handling optional leading whitespace and export prefix
        filtered = []
        for line in lines:
            stripped = line.lstrip()
            if stripped.startswith("export "):
                stripped = stripped[len("export "):]
            if stripped.startswith("ALGOLIA_APP_ID=") or stripped.startswith("ALGOLIA_API_KEY="):
                continue
            filtered.append(line)
        filtered += [f"ALGOLIA_APP_ID={app_id}", f"ALGOLIA_API_KEY={api_key}"]
        env_path.write_text("\n".join(filtered) + "\n")
        print(f"✓ .env\n")

    return AlgoliaAgentClient(app_id=app_id, api_key=api_key)


def cmd_init(args: argparse.Namespace):
    if not sys.stdin.isatty():
        raise SystemExit("ERROR: algolia-agent init requires an interactive terminal.")

    out_dir = Path(args.output_dir)
    config_path = out_dir / "agent-config.json"
    prompt_path = out_dir / "PROMPT.md"

    # Warn if files already exist
    existing = [p for p in (config_path, prompt_path) if p.exists()]
    if existing:
        names = ", ".join(p.name for p in existing)
        confirm = _ask(f"  {names} already exist. Overwrite?", "N")
        if confirm.lower() != "y":
            print("Aborted.")
            return

    # Resolve credentials — prompts if missing
    client = _resolve_credentials_interactively(args)

    print("\nFetching available providers...")
    try:
        providers = client.list_providers()
    except AgentAPIError as e:
        raise SystemExit(f"ERROR: {e}")

    if not providers:
        raise SystemExit(
            "No providers found. Set one up in Agent Studio first:\n"
            "  https://www.algolia.com/doc/guides/algolia-ai/agent-studio/how-to/quickstart"
        )

    provider_name = _select("Select a provider:", [p["name"] for p in providers])
    provider = next(p for p in providers if p["name"] == provider_name)

    models = []
    try:
        models = client.list_provider_models(provider["id"])
    except AgentAPIError:
        pass  # fall through to free-text input

    if models:
        model = _select("Select a model:", models)
    else:
        model = _ask("Model", provider.get("defaultModel") or "")
        if not model:
            raise SystemExit("ERROR: model is required.")

    print()
    name = _ask("Agent name (use {{vars}} for dynamic values)", "My Agent")
    instructions_file = _ask("Instructions file", "PROMPT.md")

    _NO_INDEX = "<no index — create without tools>"
    indices = client.list_indices()
    selection = _select(
        "Primary index (arrow keys to browse, Enter to select):",
        [_NO_INDEX] + indices,
    )
    index = None if selection == _NO_INDEX else selection

    if index:
        index_description = _ask(
            "Primary index description (use {{vars}} for dynamic values)",
            f"Search index for {index}.",
        )
        _DONE = "<done — no more replicas>"
        _CUSTOM_REPLICA = "<custom name>"
        replicas = []
        selected_replica_indices: set[str] = set()
        while True:
            print()
            available = [i for i in indices if i != index and i not in selected_replica_indices]
            selection = _select(
                "Add a replica index:",
                [_DONE] + available + [_CUSTOM_REPLICA],
            )
            if selection == _DONE:
                break
            if selection == _CUSTOM_REPLICA:
                replica_index = _ask("  Replica index name")
                if not replica_index:
                    continue
            else:
                replica_index = selection
                selected_replica_indices.add(replica_index)
            replica_desc = _ask("  Replica description", replica_index)
            replicas.append({"index": replica_index, "description": replica_desc})
    else:
        index_description = None
        replicas = []

    config = {
        "_note": "Generated by algolia-agent init. Use --var key=value to supply template variables.",
        "name": name,
        "provider": provider["name"],
        "model": model,
        "instructions": instructions_file,
    }
    if index:
        config["index"] = index
        config["index_description"] = index_description
    if replicas:
        config["replicas"] = replicas

    out_dir.mkdir(parents=True, exist_ok=True)

    with open(config_path, "w") as f:
        json.dump(config, f, indent=2)
        f.write("\n")
    print(f"\n✓ {config_path}")

    if not prompt_path.exists() or _ask(f"  {prompt_path.name} exists. Overwrite?", "N").lower() == "y":
        with open(prompt_path, "w") as f:
            f.write(_STARTER_PROMPT)
        print(f"✓ {prompt_path}")

    # Identify any template vars across both files
    all_vars = list(dict.fromkeys(
        extract_variables(json.dumps(config)) +
        extract_variables(_STARTER_PROMPT)
    ))

    print("\nNext steps:")
    print(f"  1. Edit {prompt_path.name} with your agent instructions")
    if all_vars:
        var_flags = " ".join(f"--var {v}=VALUE" for v in all_vars)
        print(f"  2. Run: algolia-agent create --config {config_path.name} {var_flags}")
    else:
        print(f"  2. Run: algolia-agent create --config {config_path.name}")


# ── Argument parser ──────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="algolia-agent",
        description="Algolia Agent Studio CLI",
    )
    parser.add_argument("--app-id", help="Algolia Application ID (overrides env/dotenv)")
    parser.add_argument("--api-key", help="Algolia API Key (overrides env/dotenv)")

    sub = parser.add_subparsers(dest="command")

    # init
    init_p = sub.add_parser("init", help="Scaffold agent-config.json and PROMPT.md interactively")
    init_p.add_argument("--output-dir", default=".", metavar="DIR",
                        help="Directory to write files into (default: current directory)")

    # list
    list_p = sub.add_parser("list", help="List all agents")
    list_p.add_argument("--json", action="store_true", help="Output JSON")

    # get
    get_p = sub.add_parser("get", help="Get full config for an agent")
    get_p.add_argument("agent_id", help="Agent ID (UUID)")
    get_p.add_argument("--json", action="store_true", help="Output JSON")

    # providers
    prov_p = sub.add_parser("providers", help="List available LLM providers")
    prov_p.add_argument("--json", action="store_true", help="Output JSON")

    # create
    create_p = sub.add_parser("create", help="Create a draft agent")
    create_p.add_argument("--config", metavar="FILE", help="Path to agent-config.json")
    create_p.add_argument("--name", help="Agent name")
    create_p.add_argument("--provider", help="Provider name (e.g. hackathon-gemini)")
    create_p.add_argument("--model", help="Model name (e.g. gemini-2.5-flash)")
    create_p.add_argument("--instructions", metavar="FILE", help="Path to instructions/prompt file")
    create_p.add_argument("--index", help="Primary Algolia index name")
    create_p.add_argument("--replica", metavar="INDEX", action="append",
                          help="Replica index name (repeatable)")
    create_p.add_argument("--var", metavar="KEY=VALUE", action="append",
                          help="Template variable substitution (repeatable)")
    create_p.add_argument("--dry-run", action="store_true",
                          help="Show resolved config and rendered instructions; no API call")
    create_p.add_argument("--json", action="store_true", help="Output JSON")

    # update
    update_p = sub.add_parser("update", help="Update an existing agent")
    update_p.add_argument("agent_id", help="Agent ID (UUID)")
    update_p.add_argument("--config", metavar="FILE", help="Path to agent-config.json")
    update_p.add_argument("--name", help="New agent name")
    update_p.add_argument("--provider", help="New provider name")
    update_p.add_argument("--model", help="New model name")
    update_p.add_argument("--instructions", metavar="FILE", help="Path to instructions file")
    update_p.add_argument("--index", help="New primary index name")
    update_p.add_argument("--replica", metavar="INDEX", action="append")
    update_p.add_argument("--var", metavar="KEY=VALUE", action="append",
                          help="Template variable substitution (repeatable)")
    update_p.add_argument("--publish", action="store_true",
                          help="Publish the agent after updating")
    update_p.add_argument("--dry-run", action="store_true",
                          help="Show what would change without making API calls")
    update_p.add_argument("--json", action="store_true", help="Output JSON")

    # publish
    pub_p = sub.add_parser("publish", help="Publish a draft agent")
    pub_p.add_argument("agent_id", help="Agent ID (UUID)")
    pub_p.add_argument("--json", action="store_true", help="Output JSON")

    # delete
    del_p = sub.add_parser("delete", help="Delete an agent")
    del_p.add_argument("agent_id", help="Agent ID (UUID)")
    del_p.add_argument("--confirm", action="store_true", help="Required to confirm deletion")
    del_p.add_argument("--json", action="store_true", help="Output JSON")

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(0)

    # --dry-run on create and init both handle credentials themselves
    if args.command == "create" and getattr(args, "dry_run", False):
        cmd_create(None, args)
        return

    if args.command == "init":
        try:
            cmd_init(args)
        except (AgentAPIError, SystemExit):
            raise
        except Exception as e:
            print(f"ERROR: {e}", file=sys.stderr)
            sys.exit(1)
        return

    try:
        client = AlgoliaAgentClient(
            app_id=getattr(args, "app_id", None),
            api_key=getattr(args, "api_key", None),
        )
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        if args.command == "list":
            cmd_list(client, args)
        elif args.command == "get":
            cmd_get(client, args)
        elif args.command == "providers":
            cmd_providers(client, args)
        elif args.command == "create":
            cmd_create(client, args)
        elif args.command == "update":
            cmd_update(client, args)
        elif args.command == "publish":
            cmd_publish(client, args)
        elif args.command == "delete":
            cmd_delete(client, args)
    except AgentAPIError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
    except SystemExit:
        raise
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
