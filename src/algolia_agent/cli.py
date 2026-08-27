#!/usr/bin/env python3
"""
Algolia Agent Studio CLI

Commands:
  init                    Scaffold agent-config.json and PROMPT.md interactively
  list                    List all agents
  get <agent_id>          Get full agent config
  providers               List available LLM providers
  create                  Create a draft agent
  snapshot <agent_id>     Write a full config file from an agent's current state
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

    tool = {
        "name": "algolia_search_index",
        "type": "algolia_search_index",
        "indices": indices,
    }
    if "searchControls" in config:
        for idx in tool["indices"]:
            idx["searchControls"] = config["searchControls"]
    if "predefinedSearchParameters" in config:
        tool["predefinedSearchParameters"] = config["predefinedSearchParameters"]
    return tool


_CHECK = "\033[32m✓\033[0m"  # green checkmark matching InquirerPy's amark style

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


_MISSING = object()

# Tool-field defaults the API applies when a field is omitted, established by probing a
# throwaway agent. A field already at its default loses nothing by being left out, so
# only deviations are reported. Fields absent from this map are reported conservatively.
_TOOL_FIELD_DEFAULTS = {"mode": "static", "allowUnlistedIndices": False}

# Fields that identify a tool or are regenerated by the API, so never a real change:
# name/type identify it, indices and description are diffed separately (the API composes
# the tool description from the index descriptions).
_TOOL_IDENTITY = {"name", "type", "indices", "description"}


def _omitted_field_note(field: str, curr_val) -> str:
    """Describe what happens to a field the payload leaves out.

    The API replaces the tool object, so an omitted field takes its default. Name that
    default when it is known rather than just saying "default", which reads as though
    the current value were the default.
    """
    default = _TOOL_FIELD_DEFAULTS.get(field, _MISSING)
    if default is _MISSING:
        return f"{_fmt(curr_val)} would be dropped from the payload"
    return f"{_fmt(curr_val)} would revert to its default of {_fmt(default)}"


def _tool_key(tool: dict) -> str:
    """Identity of a tool within the tools array."""
    return tool.get("type") or tool.get("name") or ""


def _fmt(value) -> str:
    """Render a diff value: JSON for objects/arrays, repr for scalars."""
    return json.dumps(value) if isinstance(value, (dict, list)) else repr(value)


def _prune_to(curr_val, new_val):
    """Reduce a current (API) value to the shape the new payload actually sends.

    The API expands objects with defaults and nulls we never sent — not only at the
    top level but nested inside the values of keys we did send (e.g. a
    ``constraint: {"min": null}`` under a searchControls entry). Recursing means only
    what we send is ever compared, so those expansions never show up as a change.

    An empty new object is left unpruned, so clearing a value is still detected.
    """
    if isinstance(new_val, dict) and isinstance(curr_val, dict) and new_val:
        return {k: _prune_to(v, new_val[k]) for k, v in curr_val.items() if k in new_val}
    return curr_val


def _field_changed(curr_val, new_val) -> tuple[bool, object]:
    """Compare one payload field, returning (changed, current-value-as-shown)."""
    curr_val = _prune_to(curr_val, new_val)
    return curr_val != new_val, curr_val


def _diff(current: dict, new_payload: dict) -> list[str]:
    """Return human-readable lines describing what would change."""
    lines = []

    for field in ("name", "model"):
        curr = current.get(field, "")
        new = new_payload.get(field, "")
        if curr != new:
            lines.append(f"  {field}: {curr!r} → {new!r}")

    # Compare right-stripped: the API's stored copy and the file read from disk
    # routinely differ only by a trailing newline, which is not a change worth
    # reporting. Leading whitespace is kept significant — indentation at the start of
    # the instructions is content, not noise.
    for field in ("instructions", "systemPrompt"):
        if field == "systemPrompt" and field not in new_payload:
            continue  # preserved when omitted, so absence is not a change
        curr_text = (current.get(field) or "").rstrip()
        new_text = (new_payload.get(field) or "").rstrip()
        if curr_text != new_text:
            lines.append(
                f"  {field}: changed "
                f"({len(curr_text.splitlines())} lines → {len(new_text.splitlines())} lines)"
            )

    # Verified against the API: these are preserved when omitted, so only compare what
    # the payload actually sends. Without this a provider switch, or an edit to a
    # snapshot's description, reported no change at all.
    for field in ("providerId", "templateType", "description"):
        if field not in new_payload:
            continue
        curr_val, new_val = current.get(field), new_payload.get(field)
        if curr_val != new_val:
            lines.append(f"  {field}: {_fmt(curr_val)[:70]} → {_fmt(new_val)[:70]}")

    # searchControls live per index. Omitting them from the payload WIPES them —
    # verified against the live API — so an absent key is a destructive change, not
    # silence. Present values are compared with the prune rule, since the API expands
    # them with defaults and nulls we never sent.
    curr_sc = {
        i["index"]: i.get("searchControls")
        for t in current.get("tools", [])
        for i in t.get("indices", [])
    }
    new_sc = {
        i["index"]: i.get("searchControls")
        for t in new_payload.get("tools", [])
        for i in t.get("indices", [])
    }
    sc_lines = []
    for idx in sorted(set(curr_sc) | set(new_sc)):
        if idx not in new_sc:
            continue  # the index itself is going away; the indices: block reports that
        curr_val, new_val = curr_sc.get(idx), new_sc[idx]
        if new_val is None:
            if curr_val:
                sc_lines.append(f"    {idx}: {_fmt(curr_val)} → none (will be removed)")
        else:
            pruned = _prune_to(curr_val or {}, new_val)
            if pruned != new_val:
                sc_lines.append(f"    {idx}: {_fmt(pruned)} → {_fmt(new_val)}")
    if sc_lines:
        lines.append("  searchControls:")
        lines.extend(sc_lines)

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

    # Tools: report added/removed tools (by type) and per-field changes on matched tools
    # (e.g. isTerminal, minResultsPerGroup, predefinedSearchParameters). Without this,
    # adding or removing a tool shows as "no changes" — and sending a one-tool payload
    # that silently drops an existing tool is invisible. Only fields present in the new
    # payload are compared (same noise-avoidance as searchControls); nested index data
    # and descriptions are diffed above, so they're excluded here.
    curr_tools = {_tool_key(t): t for t in current.get("tools", [])}
    new_tools = {_tool_key(t): t for t in new_payload.get("tools", [])}

    tool_lines = []
    for key in sorted(set(curr_tools) | set(new_tools)):
        if key not in curr_tools:
            tool_lines.append(f"    + {key}")
        elif key not in new_tools:
            tool_lines.append(f"    - {key}")
        else:
            new_fields = {k: v for k, v in new_tools[key].items() if k not in _TOOL_IDENTITY}
            for k in sorted(new_fields):
                changed, curr_shown = _field_changed(curr_tools[key].get(k), new_fields[k])
                if changed:
                    tool_lines.append(
                        f"    ~ {key}.{k}: {_fmt(curr_shown)} → {_fmt(new_fields[k])}"
                    )
            # The tool object is replaced, not merged, so a field the payload omits
            # reverts to its API default — verified: mode "dynamic" became "static" and
            # allowUnlistedIndices true became false. Report those, or the dry-run stays
            # silent while the update turns settings off. "description" is excluded via
            # _TOOL_IDENTITY because the API regenerates it from the index descriptions.
            for k in sorted(set(curr_tools[key]) - set(new_fields) - _TOOL_IDENTITY):
                curr_val = curr_tools[key][k]
                if curr_val is None or curr_val == _TOOL_FIELD_DEFAULTS.get(k, _MISSING):
                    continue  # already at its default; omitting it changes nothing
                default = _TOOL_FIELD_DEFAULTS.get(k, _MISSING)
                becomes = "dropped" if default is _MISSING else _fmt(default)
                tool_lines.append(
                    f"    ~ {key}.{k}: {_fmt(curr_val)} → {becomes} (not sent)"
                )
    if tool_lines:
        lines.append("  tools:")
        lines.extend(tool_lines)

    # config block: the API replaces this object wholesale instead of merging, so any
    # key the payload omits is destroyed. Compare unpruned — the present-keys-only rule
    # used elsewhere would report "no change" for an update that drops keys — and name
    # the casualties explicitly.
    new_cfg = new_payload.get("config")
    if new_cfg is not None:
        curr_cfg = current.get("config") or {}
        if curr_cfg != new_cfg:
            lines.append("  config:")
            for k in sorted(set(curr_cfg) | set(new_cfg)):
                if k not in new_cfg:
                    lines.append(f"    - {k}: {_fmt(curr_cfg[k])} (will be removed)")
                elif k not in curr_cfg:
                    lines.append(f"    + {k}: {_fmt(new_cfg[k])}")
                elif curr_cfg[k] != new_cfg[k]:
                    lines.append(f"    ~ {k}: {_fmt(curr_cfg[k])} → {_fmt(new_cfg[k])}")

    return lines


def _removals(current: dict, new_payload: dict) -> list[str]:
    """Describe losses the config model has no way to ask for.

    PATCH replaces objects rather than merging them, so anything absent from the
    payload is destroyed. agent-config.json cannot express a second tool, a tool-level
    field like mode, or per-index searchControls, so losing those is never something the
    user asked for — it is the config model being narrower than the API.

    Index membership is deliberately excluded: the config names its indices, so
    dropping one is an explicit choice rather than an accident.
    """
    out = []

    curr_tools = {_tool_key(t): t for t in current.get("tools", [])}
    new_tools = {_tool_key(t): t for t in new_payload.get("tools", [])}

    for key in sorted(set(curr_tools) - set(new_tools)):
        out.append(f"  - the {key!r} tool would be deleted")

    for key in sorted(set(curr_tools) & set(new_tools)):
        dropped = set(curr_tools[key]) - set(new_tools[key]) - _TOOL_IDENTITY
        for field in sorted(dropped):
            val = curr_tools[key][field]
            if val is None or val == _TOOL_FIELD_DEFAULTS.get(field, _MISSING):
                continue  # already at its default; omitting it changes nothing
            out.append(f"  - {key}.{field}: {_omitted_field_note(field, val)}")

    curr_sc = {
        i["index"]: i.get("searchControls")
        for t in current.get("tools", [])
        for i in t.get("indices", [])
    }
    new_sc = {
        i["index"]: i.get("searchControls")
        for t in new_payload.get("tools", [])
        for i in t.get("indices", [])
    }
    for idx in sorted(curr_sc):
        if idx in new_sc and curr_sc[idx] and not new_sc[idx]:
            out.append(f"  - searchControls on {idx!r} would be wiped")

    new_cfg = new_payload.get("config")
    if new_cfg is not None:
        for k in sorted(set(current.get("config") or {}) - set(new_cfg)):
            out.append(f"  - config key {k!r} would be removed")

    return out


# Server-owned fields, excluded from a snapshot. The API ignores them on write, so this
# is tidiness rather than correctness — which means a mistake here surfaces as a phantom
# diff on the very next dry-run instead of destroying anything.
_SNAPSHOT_SKIP_TOP = {"id", "createdAt", "updatedAt", "lastUsedAt"}
# enhancedDescription is regenerated by the platform from the index contents; a copy
# frozen in a checked-in file would only go stale.
_SNAPSHOT_SKIP_INDEX = {"enhancedDescription"}

# templateType is carried through, but it is a provenance label rather than a selector:
# the dashboard's template picker populates instructions and tools at creation time and
# stamps which template was used. The field accepts any string (only the type is
# validated) and setting it has no side effects, so editing it in a snapshot relabels the
# agent without applying anything.


def build_snapshot(agent: dict, instructions_file: str,
                   system_prompt_file: str | None = None) -> dict:
    """A native-format config mirroring the agent's current server state.

    Everything else the API stores is carried through verbatim, because the API accepts
    its own GET response as a PATCH body. That is what makes a snapshot safe to send
    back: nothing has to be reconstructed, so nothing can be left out.

    Instructions are externalised to a file — a long prompt embedded as a JSON string
    is unreadable and uneditable — which also keeps "instructions" meaning a file path
    in both config formats.
    """
    snap = {k: v for k, v in agent.items() if k not in _SNAPSHOT_SKIP_TOP}
    snap["instructions"] = instructions_file
    if system_prompt_file and (agent.get("systemPrompt") or "").strip():
        snap["systemPrompt"] = system_prompt_file
    if isinstance(snap.get("tools"), list):
        snap["tools"] = [
            {
                **tool,
                **({"indices": [
                    {k: v for k, v in idx.items() if k not in _SNAPSHOT_SKIP_INDEX}
                    for idx in tool["indices"]
                ]} if isinstance(tool.get("indices"), list) else {}),
            }
            for tool in snap["tools"]
        ]
    return snap


def is_native_config(config: dict) -> bool:
    """Native configs carry the API's own tools array; friendly ones describe `index`."""
    return isinstance(config.get("tools"), list)


def cmd_snapshot(client: AlgoliaAgentClient, args: argparse.Namespace):
    agent = client.get_agent(args.agent_id)
    out_path = Path(args.output)
    instr_path = out_path.parent / args.instructions_file
    has_system = bool((agent.get("systemPrompt") or "").strip())
    system_path = out_path.parent / args.system_prompt_file if has_system else None

    targets = [p for p in (out_path, instr_path, system_path) if p is not None]
    existing = [p for p in targets if p.exists()]
    if existing and not args.force:
        raise SystemExit(
            "ERROR: refusing to overwrite:\n"
            + "\n".join(f"  {p}" for p in existing)
            + "\nPass --force to overwrite, or -o/--instructions-file to write elsewhere."
        )

    # A snapshot holds rendered server state, so overwriting a templated prompt would
    # replace {{placeholders}} with the values they resolved to — an unrecoverable loss,
    # since the template only ever existed locally.
    for p in existing:
        if p.suffix.lower() == ".md" and extract_variables(p.read_text()):
            print(
                f"WARNING: {p} contains template variables and will be replaced with "
                f"rendered text.\n         The template exists only locally; a snapshot "
                f"cannot recover it.",
                file=sys.stderr,
            )

    snapshot = build_snapshot(agent, args.instructions_file,
                             args.system_prompt_file if has_system else None)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    instr_path.write_text((agent.get("instructions") or "").rstrip() + "\n")
    if system_path:
        system_path.write_text((agent.get("systemPrompt") or "").rstrip() + "\n")
    out_path.write_text(json.dumps(snapshot, indent=2) + "\n")

    if args.json:
        result = {"config": str(out_path), "instructions": str(instr_path)}
        if system_path:
            result["systemPrompt"] = str(system_path)
        print(json.dumps(result))
        return

    tool_types = [t.get("type") for t in agent.get("tools") or []]
    print(f"Snapshotted: {agent['name']}")
    print(f"  Config:       {out_path}")
    print(f"  Instructions: {instr_path}")
    if system_path:
        print(f"  System:       {system_path}")
    print(f"  Tools:        {', '.join(tool_types) or 'none'}")
    print(f"  Config keys:  {', '.join(sorted((agent.get('config') or {}).keys())) or 'none'}")
    print(f"\nVerify it round-trips: algolia-agent update {args.agent_id} --config {out_path} --dry-run")


def _native_payload(config: dict, config_path, args) -> dict:
    """Payload for a native config: send what the file says, verbatim.

    No tool is reconstructed and no provider name is resolved — providerId is already in
    the file — so there is nothing for the CLI's narrower model to drop.
    """
    for flag in ("index", "replica", "provider"):
        if getattr(args, flag, None):
            raise SystemExit(
                f"ERROR: --{flag.replace('_', '-')} cannot be combined with a native config.\n"
                "A native config carries the API's own tools array and providerId; edit "
                "the file directly instead."
            )
    if getattr(args, "var", None):
        raise SystemExit(
            "ERROR: --var cannot be combined with a native config.\n"
            "A native config is literal: it holds the prompt as the service stores it, so "
            "{{...}} is content to preserve, not a variable to substitute. Agent Studio's\n"
            "own templates ship placeholders like {{INSERT_BRAND}}, and substituting them\n"
            "on every update would overwrite them.\n\n"
            f"To fill them in, edit {config.get('instructions') or 'the prompt file'} "
            "directly and run update again.\n"
            "For templating across several agents, use the friendly config format "
            "(index/replicas)."
        )

    def _read(field: str) -> str:
        """Read an externalised prompt file, resolved relative to the config."""
        if not config.get(field):
            return ""
        path = Path(config[field])
        if not path.exists() and config_path:
            path = Path(config_path).parent / config[field]
        if not path.exists():
            raise SystemExit(f"ERROR: {field} file not found: {config[field]}")
        # Right-stripped so snapshot -> update is a true no-op: the file gets a trailing
        # newline that the stored value did not have, and sending it back would rewrite
        # the field on every round-trip.
        return path.read_text().rstrip()

    payload = {k: v for k, v in config.items() if k not in _SNAPSHOT_SKIP_TOP}
    # Deliberately NOT template-rendered. Several live agents carry literal {{...}} in
    # their prompts ({{INSERT_BRAND}}, and incidental prose like {{facet}}); rendering a
    # snapshot would demand values for text that is simply content, and substituting
    # would destroy it.
    payload["instructions"] = _read("instructions")
    if config.get("systemPrompt"):
        payload["systemPrompt"] = _read("systemPrompt")

    for key in ("name", "model"):
        val = getattr(args, key, None)
        if val is not None:
            payload[key] = val
    return payload


def _apply_update(client, args, current: dict, new_payload: dict, config_path=None):
    """Dry-run, guard, send. Shared by the friendly and native config paths so both
    get the same reporting and the same refusal."""
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

    # The API replaces objects rather than merging, so a config narrower than the
    # agent silently deletes the difference. Refuse rather than destroy.
    removals = _removals(current, new_payload)
    if removals and not getattr(args, "force", False):
        source = f"{config_path}" if config_path else "the fields you supplied"
        raise SystemExit(
            "ERROR: this update would remove configuration the agent currently has:\n"
            + "\n".join(removals)
            + "\n\nThe Agent Studio API replaces these fields instead of merging them, so\n"
            f"anything missing from the payload is lost. {source} cannot express them,\n"
            "which is why they are absent.\n\n"
            f"Take a full snapshot to keep them: algolia-agent snapshot {args.agent_id}\n"
            "Or pass --force to accept the removals. Add --dry-run to see the full diff."
        )

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


def cmd_update(client: AlgoliaAgentClient, args: argparse.Namespace):
    current = client.get_agent(args.agent_id)

    # Load and merge config; auto-detect agent-config.json if --config not given
    config_path = args.config or (Path("agent-config.json") if Path("agent-config.json").exists() else None)
    file_config = load_config(config_path) if config_path else {}

    if is_native_config(file_config):
        new_payload = _native_payload(file_config, config_path, args)
        _apply_update(client, args, current, new_payload)
        return

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

    _apply_update(client, args, current, new_payload, config_path)


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

**SEARCH TOOL USAGE**
- If the user mentions only filterable attributes (not a specific item name), use a blank ("") query and apply facets/filters rather than a keyword search.
- For "more than" / "less than" questions on numeric fields, use comparison operators in searchParams filters rather than facets: e.g. `searchParams: { filters: 'price > 50' }`
- Limit yourself to 5 search tool calls per session. If you reach the limit without success, say: "Sorry, I couldn't find any matching items."
- On tool error or timeout, apologize once and invite the user to rephrase.
- When you have search results, keep your response concise: a short 2–3 sentence summary.
- If confidence is low, ask up to 2 clarifying questions before searching.

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
    try:
        val = input(display).strip()
    except KeyboardInterrupt:
        raise SystemExit("\nAborted.")
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
        print(f"{_CHECK} .env\n")

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
            replica_desc = _ask("  Replica description", f"Replica index of {index_description}")
            replicas.append({"index": replica_index, "description": replica_desc})

        search_controls: dict = {}
        print()
        if _ask("Set up searchControls to limit hits or restrict attributes?", "N").lower() == "y":
            while True:
                max_hits = _ask("  Cap hitsPerPage? Enter max (or leave blank to skip)")
                if not max_hits:
                    break
                try:
                    n = int(max_hits)
                    search_controls["hitsPerPage"] = {"exposed": False, "default": n, "constraint": {"max": n}}
                    break
                except ValueError:
                    print("  Please enter a whole number.")
            while True:
                max_page = _ask("  Cap page? Enter max (or leave blank to skip)")
                if not max_page:
                    break
                try:
                    n = int(max_page)
                    search_controls["page"] = {"exposed": False, "default": 0, "constraint": {"max": n}}
                    break
                except ValueError:
                    print("  Please enter a whole number.")
            attrs_raw = _ask("  Restrict attributesToRetrieve? Enter comma-separated list (or leave blank to skip)")
            if attrs_raw:
                attrs = [a.strip() for a in attrs_raw.split(",") if a.strip()]
                if attrs:
                    search_controls["attributesToRetrieve"] = {"exposed": False, "default": attrs}
            facets_raw = _ask("  Enable facets? Enter comma-separated list (or leave blank to skip)")
            if facets_raw:
                facets_list = [f.strip() for f in facets_raw.split(",") if f.strip()]
                if facets_list:
                    search_controls["facets"] = {"exposed": False, "default": facets_list}
            fields_raw = _ask("  Restrict responseFields? Enter comma-separated list (or leave blank to skip)")
            if fields_raw:
                fields_list = [f.strip() for f in fields_raw.split(",") if f.strip()]
                if fields_list:
                    search_controls["responseFields"] = {"exposed": False, "default": fields_list}
    else:
        index_description = None
        replicas = []
        search_controls = {}

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
    if search_controls:
        config["searchControls"] = search_controls

    out_dir.mkdir(parents=True, exist_ok=True)

    with open(config_path, "w") as f:
        json.dump(config, f, indent=2)
        f.write("\n")
    print(f"\n{_CHECK} {config_path}")

    if not prompt_path.exists() or _ask(f"  {prompt_path.name} exists. Overwrite?", "N").lower() == "y":
        with open(prompt_path, "w") as f:
            f.write(_STARTER_PROMPT)
        print(f"{_CHECK} {prompt_path}")

    # Identify any template vars across both files
    all_vars = list(dict.fromkeys(
        extract_variables(json.dumps(config)) +
        extract_variables(_STARTER_PROMPT)
    ))

    config_flag = f"--config {config_path} " if out_dir != Path(".") else ""
    var_flags = (" ".join(f"--var {v}=VALUE" for v in all_vars) + " ") if all_vars else ""
    print("\nNext steps:")
    print(f"  1. Edit {prompt_path.name} with your agent instructions")
    print(f"  2. Run: algolia-agent create {config_flag}{var_flags}".rstrip())


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
    update_p.add_argument("--force", action="store_true",
                          help="Proceed even if the update would remove existing configuration")
    update_p.add_argument("--dry-run", action="store_true",
                          help="Show what would change without making API calls")
    update_p.add_argument("--json", action="store_true", help="Output JSON")

    # publish
    snap_p = sub.add_parser("snapshot",
                            help="Write a full config file from an agent's current state")
    snap_p.add_argument("agent_id", help="Agent ID (UUID)")
    snap_p.add_argument("-o", "--output", default="agent-config.json", metavar="FILE",
                        help="Config file to write (default: agent-config.json)")
    snap_p.add_argument("--instructions-file", default="PROMPT.md", metavar="FILE",
                        help="Instructions file written alongside (default: PROMPT.md)")
    snap_p.add_argument("--system-prompt-file", default="SYSTEM.md", metavar="FILE",
                        help="System prompt file, written only if the agent has one "
                             "(default: SYSTEM.md)")
    snap_p.add_argument("--force", action="store_true",
                        help="Overwrite existing files")
    snap_p.add_argument("--json", action="store_true", help="Output JSON")

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
    try:
        _main()
    except KeyboardInterrupt:
        print("\nAborted.", file=sys.stderr)
        sys.exit(130)


def _main():
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
        elif args.command == "snapshot":
            cmd_snapshot(client, args)
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
