import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from algolia_agent.cli import build_parser, load_config, merge_config, parse_vars, resolve_vars


# ── load_config ──────────────────────────────────────────────────────────────

def test_load_config_valid(tmp_path):
    cfg = tmp_path / "config.json"
    cfg.write_text('{"name": "Test", "provider": "gemini"}')
    result = load_config(str(cfg))
    assert result == {"name": "Test", "provider": "gemini"}


def test_load_config_missing_file():
    with pytest.raises(SystemExit, match="not found"):
        load_config("/nonexistent/config.json")


def test_load_config_invalid_json(tmp_path):
    cfg = tmp_path / "bad.json"
    cfg.write_text("{not valid json}")
    with pytest.raises(SystemExit, match="Invalid JSON"):
        load_config(str(cfg))


# ── merge_config ─────────────────────────────────────────────────────────────

def test_merge_config_cli_overrides_file():
    file_config = {"name": "File Name", "provider": "file-provider", "model": "file-model"}
    args = MagicMock()
    args.name = "CLI Name"
    args.provider = None
    args.model = "cli-model"
    args.instructions = None
    args.index = None
    args.replica = None
    result = merge_config(file_config, args)
    assert result["name"] == "CLI Name"
    assert result["provider"] == "file-provider"  # from file
    assert result["model"] == "cli-model"          # from CLI


def test_merge_config_replicas_from_cli():
    file_config = {"replicas": ["old_replica"]}
    args = MagicMock()
    args.name = args.provider = args.model = args.instructions = args.index = None
    args.replica = ["new_replica_asc", "new_replica_desc"]
    result = merge_config(file_config, args)
    assert result["replicas"] == ["new_replica_asc", "new_replica_desc"]


def test_merge_config_no_cli_keeps_file_replicas():
    file_config = {"replicas": ["keep_me"]}
    args = MagicMock()
    args.name = args.provider = args.model = args.instructions = args.index = None
    args.replica = None
    result = merge_config(file_config, args)
    assert result["replicas"] == ["keep_me"]


# ── parse_vars ───────────────────────────────────────────────────────────────

def test_parse_vars_simple():
    assert parse_vars(["event_name=Test Event", "booth=701"]) == {
        "event_name": "Test Event",
        "booth": "701",
    }


def test_parse_vars_value_with_equals():
    result = parse_vars(["key=a=b"])
    assert result == {"key": "a=b"}


def test_parse_vars_none():
    assert parse_vars(None) == {}


def test_parse_vars_invalid_format():
    with pytest.raises(SystemExit, match="key=value"):
        parse_vars(["no-equals-sign"])


# ── resolve_vars ─────────────────────────────────────────────────────────────

def test_resolve_vars_all_provided():
    text = "Hello {{name}} from {{place}}"
    result = resolve_vars(text, {"name": "Alice", "place": "Wonderland"})
    assert result == {"name": "Alice", "place": "Wonderland"}


def test_resolve_vars_interactive(monkeypatch):
    text = "Hello {{name}}"
    monkeypatch.setattr("sys.stdin", MagicMock(isatty=lambda: True))
    with patch("builtins.input", return_value="Alice"):
        result = resolve_vars(text, {})
    assert result["name"] == "Alice"


def test_resolve_vars_non_tty_missing_raises(monkeypatch):
    text = "Hello {{name}} from {{place}}"
    monkeypatch.setattr("sys.stdin", MagicMock(isatty=lambda: False))
    with pytest.raises(SystemExit, match="missing required template variables"):
        resolve_vars(text, {})


def test_resolve_vars_non_tty_lists_missing_vars(monkeypatch):
    text = "{{event_name}} {{booth}}"
    monkeypatch.setattr("sys.stdin", MagicMock(isatty=lambda: False))
    with pytest.raises(SystemExit) as exc_info:
        resolve_vars(text, {})
    msg = str(exc_info.value)
    assert "event_name" in msg
    assert "booth" in msg


# ── --dry-run ─────────────────────────────────────────────────────────────────

def test_dry_run(tmp_path, capsys):
    prompt = tmp_path / "PROMPT.md"
    prompt.write_text("Hello {{event_name}}, booth {{booth}}.")
    config = tmp_path / "config.json"
    config.write_text(json.dumps({
        "name": "Test Agent",
        "provider": "hackathon-gemini",
        "model": "gemini-2.5-flash",
        "instructions": str(prompt),
        "index": "products",
    }))

    parser = build_parser()
    args = parser.parse_args([
        "create",
        "--config", str(config),
        "--var", "event_name=MyEvent",
        "--var", "booth=701",
        "--dry-run",
    ])

    from algolia_agent.cli import cmd_create
    cmd_create(None, args)  # None client — no API calls made

    out = capsys.readouterr().out
    assert "DRY RUN" in out
    assert "MyEvent" in out
    assert "701" in out


# ── --json output ─────────────────────────────────────────────────────────────

def test_list_json_output(capsys):
    from algolia_agent.cli import cmd_list
    mock_client = MagicMock()
    mock_client.list_agents.return_value = [
        {"id": "abc", "name": "Test", "status": "draft", "updatedAt": "2026-01-01T00:00:00Z"}
    ]
    args = MagicMock()
    args.json = True
    cmd_list(mock_client, args)
    data = json.loads(capsys.readouterr().out)
    assert data[0]["id"] == "abc"


def test_create_json_output(tmp_path, capsys):
    from algolia_agent.cli import cmd_create
    prompt = tmp_path / "PROMPT.md"
    prompt.write_text("Hello {{event_name}}.")
    config = tmp_path / "config.json"
    config.write_text(json.dumps({
        "name": "Test Agent",
        "provider": "hackathon-gemini",
        "model": "gemini-2.5-flash",
        "instructions": str(prompt),
        "index": "products",
    }))

    mock_client = MagicMock()
    mock_client.resolve_provider_id.return_value = "provider-uuid"
    mock_client.create_agent.return_value = {
        "id": "agent-uuid",
        "name": "Test Agent",
        "status": "draft",
    }

    parser = build_parser()
    args = parser.parse_args([
        "create", "--config", str(config),
        "--var", "event_name=MyEvent",
        "--json",
    ])
    cmd_create(mock_client, args)

    data = json.loads(capsys.readouterr().out)
    assert data["id"] == "agent-uuid"
    assert data["status"] == "draft"


# ── init ─────────────────────────────────────────────────────────────────────

def _mock_init_client(providers):
    """Patch AlgoliaAgentClient so cmd_init gets a pre-configured mock."""
    mock_client = MagicMock()
    mock_client.list_providers.return_value = providers
    return patch("algolia_agent.cli.AlgoliaAgentClient", return_value=mock_client)


def test_init_writes_config_and_prompt(tmp_path, monkeypatch):
    from algolia_agent.cli import cmd_init

    providers = [{"id": "uuid", "name": "hackathon-gemini", "defaultModel": "gemini-2.5-flash"}]
    inputs = iter([
        "1",                      # provider choice
        "gemini-2.5-flash",       # model
        "My Agent",               # name
        "PROMPT.md",              # instructions file
        "products",               # index
        "Main product catalog.",  # index description
        "N",                      # no replicas
    ])
    monkeypatch.setattr("sys.stdin", MagicMock(isatty=lambda: True))
    with _mock_init_client(providers):
        with patch("builtins.input", lambda _: next(inputs)):
            parser = build_parser()
            args = parser.parse_args(["init", "--output-dir", str(tmp_path)])
            cmd_init(args)

    config = json.loads((tmp_path / "agent-config.json").read_text())
    assert config["provider"] == "hackathon-gemini"
    assert config["model"] == "gemini-2.5-flash"
    assert config["index"] == "products"
    assert config["index_description"] == "Main product catalog."
    assert "replicas" not in config
    assert (tmp_path / "PROMPT.md").exists()


def test_init_with_replicas(tmp_path, monkeypatch):
    from algolia_agent.cli import cmd_init

    providers = [{"id": "uuid", "name": "hackathon-gemini", "defaultModel": "gemini-2.5-flash"}]
    inputs = iter([
        "1",                               # provider
        "gemini-2.5-flash",                # model
        "My Agent",                        # name
        "PROMPT.md",                       # instructions
        "products_{{event_id}}",           # index
        "Product catalog.",                # index description
        "y",                               # add replica
        "products_{{event_id}}_price_asc", # replica index
        "Sorted by price asc.",            # replica description
        "N",                               # no more replicas
    ])
    monkeypatch.setattr("sys.stdin", MagicMock(isatty=lambda: True))
    with _mock_init_client(providers):
        with patch("builtins.input", lambda _: next(inputs)):
            parser = build_parser()
            args = parser.parse_args(["init", "--output-dir", str(tmp_path)])
            cmd_init(args)

    config = json.loads((tmp_path / "agent-config.json").read_text())
    assert len(config["replicas"]) == 1
    assert config["replicas"][0]["index"] == "products_{{event_id}}_price_asc"
    assert config["replicas"][0]["description"] == "Sorted by price asc."


def test_init_prompts_for_missing_credentials(tmp_path, monkeypatch):
    from algolia_agent.cli import cmd_init

    providers = [{"id": "uuid", "name": "hackathon-gemini", "defaultModel": "gemini-2.5-flash"}]
    inputs = iter([
        "MYAPPID",        # App ID prompt
        "myapikey",       # API Key prompt
        "n",              # don't save to .env
        "1",              # provider
        "gemini-2.5-flash",
        "My Agent",
        "PROMPT.md",
        "products",
        "Product catalog.",
        "N",
    ])
    monkeypatch.setattr("sys.stdin", MagicMock(isatty=lambda: True))
    monkeypatch.delenv("ALGOLIA_APP_ID", raising=False)
    monkeypatch.delenv("ALGOLIA_API_KEY", raising=False)

    # First call raises (no creds), second call with explicit creds succeeds
    mock_client = MagicMock()
    mock_client.list_providers.return_value = providers
    with patch("algolia_agent.cli.AlgoliaAgentClient", side_effect=[
        ValueError("Missing credentials"),
        mock_client,
    ]):
        with patch("algolia_agent.cli.Path.cwd", return_value=MagicMock(
            __truediv__=lambda self, other: MagicMock(exists=lambda: False)
        )):
            with patch("builtins.input", lambda _: next(inputs)):
                parser = build_parser()
                args = parser.parse_args(["init", "--output-dir", str(tmp_path)])
                cmd_init(args)

    config = json.loads((tmp_path / "agent-config.json").read_text())
    assert config["provider"] == "hackathon-gemini"


def test_init_saves_credentials_to_dotenv(tmp_path, monkeypatch):
    from algolia_agent.cli import cmd_init

    providers = [{"id": "uuid", "name": "hackathon-gemini", "defaultModel": "gemini-2.5-flash"}]
    inputs = iter([
        "MYAPPID", "myapikey", "Y",  # creds + save to .env
        "1", "gemini-2.5-flash", "My Agent", "PROMPT.md", "products", "Product catalog.", "N",
    ])
    monkeypatch.setattr("sys.stdin", MagicMock(isatty=lambda: True))
    monkeypatch.delenv("ALGOLIA_APP_ID", raising=False)
    monkeypatch.delenv("ALGOLIA_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)

    mock_client = MagicMock()
    mock_client.list_providers.return_value = providers
    with patch("algolia_agent.cli.AlgoliaAgentClient", side_effect=[
        ValueError("Missing credentials"),
        mock_client,
    ]):
        with patch("builtins.input", lambda _: next(inputs)):
            args = build_parser().parse_args(["init", "--output-dir", str(tmp_path)])
            cmd_init(args)

    env_content = (tmp_path / ".env").read_text()
    assert "ALGOLIA_APP_ID=MYAPPID" in env_content
    assert "ALGOLIA_API_KEY=myapikey" in env_content


def test_init_non_tty_errors(monkeypatch):
    from algolia_agent.cli import cmd_init
    monkeypatch.setattr("sys.stdin", MagicMock(isatty=lambda: False))
    with pytest.raises(SystemExit, match="interactive terminal"):
        cmd_init(MagicMock(output_dir="."))


# ── cmd_update ────────────────────────────────────────────────────────────────

def _make_current_agent(name="Old Agent", model="gemini-2.5-flash", instructions="Old instructions."):
    return {
        "id": "agent-uuid",
        "name": name,
        "model": model,
        "instructions": instructions,
        "status": "draft",
        "providerId": "provider-uuid",
        "tools": [
            {
                "type": "algolia_search_index",
                "indices": [
                    {"index": "products", "description": "Product catalog."},
                    {"index": "products_price_asc", "description": "Sorted by price ascending."},
                ],
            }
        ],
        "createdAt": "2026-01-01T00:00:00Z",
        "updatedAt": "2026-01-01T00:00:00Z",
    }


def test_update_dry_run_no_changes(tmp_path, capsys):
    from algolia_agent.cli import cmd_update

    prompt = tmp_path / "PROMPT.md"
    prompt.write_text("Old instructions.")
    config = tmp_path / "config.json"
    config.write_text(json.dumps({
        "name": "Old Agent",
        "provider": "hackathon-gemini",
        "model": "gemini-2.5-flash",
        "instructions": str(prompt),
        "index": "products",
        "index_description": "Product catalog.",
        "replicas": [{"index": "products_price_asc", "description": "Sorted by price ascending."}],
    }))

    mock_client = MagicMock()
    mock_client.get_agent.return_value = _make_current_agent()
    mock_client.resolve_provider_id.return_value = "provider-uuid"

    parser = build_parser()
    args = parser.parse_args([
        "update", "agent-uuid",
        "--config", str(config),
        "--dry-run",
    ])
    cmd_update(mock_client, args)

    out = capsys.readouterr().out
    assert "DRY RUN" in out
    assert "No changes" in out
    mock_client.update_agent.assert_not_called()


def test_update_dry_run_shows_changes(tmp_path, capsys):
    from algolia_agent.cli import cmd_update

    prompt = tmp_path / "PROMPT.md"
    prompt.write_text("New instructions.")
    config = tmp_path / "config.json"
    config.write_text(json.dumps({
        "name": "New Agent Name",
        "provider": "hackathon-gemini",
        "model": "gemini-2.5-flash",
        "instructions": str(prompt),
        "index": "products",
        "index_description": "Updated description.",
    }))

    mock_client = MagicMock()
    mock_client.get_agent.return_value = _make_current_agent()
    mock_client.resolve_provider_id.return_value = "provider-uuid"

    parser = build_parser()
    args = parser.parse_args([
        "update", "agent-uuid",
        "--config", str(config),
        "--dry-run",
    ])
    cmd_update(mock_client, args)

    out = capsys.readouterr().out
    assert "DRY RUN" in out
    assert "New Agent Name" in out or "name" in out
    mock_client.update_agent.assert_not_called()


def test_update_makes_api_call(tmp_path, capsys):
    from algolia_agent.cli import cmd_update

    prompt = tmp_path / "PROMPT.md"
    prompt.write_text("Updated instructions.")
    config = tmp_path / "config.json"
    config.write_text(json.dumps({
        "name": "Updated Agent",
        "provider": "hackathon-gemini",
        "model": "gemini-2.5-flash",
        "instructions": str(prompt),
        "index": "products",
        "index_description": "Product catalog.",
    }))

    mock_client = MagicMock()
    mock_client.get_agent.return_value = _make_current_agent()
    mock_client.resolve_provider_id.return_value = "provider-uuid"
    mock_client.update_agent.return_value = {
        "id": "agent-uuid",
        "name": "Updated Agent",
        "status": "draft",
    }

    parser = build_parser()
    args = parser.parse_args([
        "update", "agent-uuid",
        "--config", str(config),
    ])
    cmd_update(mock_client, args)

    mock_client.update_agent.assert_called_once()
    call_payload = mock_client.update_agent.call_args[0][1]
    assert call_payload["name"] == "Updated Agent"
    assert call_payload["instructions"] == "Updated instructions."


def test_update_with_template_vars(tmp_path, capsys):
    from algolia_agent.cli import cmd_update

    prompt = tmp_path / "PROMPT.md"
    prompt.write_text("Agent for {{event_name}} at booth {{booth}}.")
    config = tmp_path / "config.json"
    config.write_text(json.dumps({
        "name": "Agent for {{event_name}}",
        "provider": "hackathon-gemini",
        "model": "gemini-2.5-flash",
        "instructions": str(prompt),
        "index": "products_{{event_id}}",
        "index_description": "Catalog for {{event_name}}.",
    }))

    mock_client = MagicMock()
    mock_client.get_agent.return_value = _make_current_agent()
    mock_client.resolve_provider_id.return_value = "provider-uuid"
    mock_client.update_agent.return_value = {
        "id": "agent-uuid",
        "name": "Agent for Spring 2026",
        "status": "draft",
    }

    parser = build_parser()
    args = parser.parse_args([
        "update", "agent-uuid",
        "--config", str(config),
        "--var", "event_name=Spring 2026",
        "--var", "event_id=spring-2026",
        "--var", "booth=701",
    ])
    cmd_update(mock_client, args)

    call_payload = mock_client.update_agent.call_args[0][1]
    assert call_payload["name"] == "Agent for Spring 2026"
    assert "Spring 2026" in call_payload["instructions"]
    assert call_payload["tools"][0]["indices"][0]["index"] == "products_spring-2026"


def test_update_json_output(tmp_path, capsys):
    from algolia_agent.cli import cmd_update

    prompt = tmp_path / "PROMPT.md"
    prompt.write_text("Instructions.")
    config = tmp_path / "config.json"
    config.write_text(json.dumps({
        "name": "My Agent",
        "provider": "hackathon-gemini",
        "model": "gemini-2.5-flash",
        "instructions": str(prompt),
        "index": "products",
        "index_description": "Products.",
    }))

    mock_client = MagicMock()
    mock_client.get_agent.return_value = _make_current_agent()
    mock_client.resolve_provider_id.return_value = "provider-uuid"
    mock_client.update_agent.return_value = {
        "id": "agent-uuid",
        "name": "My Agent",
        "status": "draft",
    }

    parser = build_parser()
    args = parser.parse_args([
        "update", "agent-uuid",
        "--config", str(config),
        "--json",
    ])
    cmd_update(mock_client, args)

    data = json.loads(capsys.readouterr().out)
    assert data["id"] == "agent-uuid"
    assert data["status"] == "draft"


# ── Exit codes ────────────────────────────────────────────────────────────────

def test_missing_credentials_exits_1(monkeypatch, capsys):
    monkeypatch.delenv("ALGOLIA_APP_ID", raising=False)
    monkeypatch.delenv("ALGOLIA_API_KEY", raising=False)

    with patch("algolia_agent.client.Path.cwd", return_value=MagicMock(
        __truediv__=lambda self, other: MagicMock(exists=lambda: False)
    )):
        with patch("sys.argv", ["algolia-agent", "list"]):
            from algolia_agent.cli import main
            with pytest.raises(SystemExit) as exc_info:
                main()
    assert exc_info.value.code == 1
