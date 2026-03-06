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
