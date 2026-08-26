import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from algolia_agent.cli import build_parser, build_tool, load_config, merge_config, parse_vars, resolve_vars, _diff


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


# ── agent-config.json auto-detection ─────────────────────────────────────────

def test_create_autodetects_agent_config_json(tmp_path, monkeypatch, capsys):
    """create uses agent-config.json in CWD when --config is not provided."""
    from algolia_agent.cli import cmd_create

    prompt = tmp_path / "PROMPT.md"
    prompt.write_text("Hello.")
    config = tmp_path / "agent-config.json"
    config.write_text(json.dumps({
        "name": "Auto Agent",
        "provider": "hackathon-gemini",
        "model": "gemini-2.5-flash",
        "instructions": str(prompt),
        "index": "products",
    }))
    monkeypatch.chdir(tmp_path)

    mock_client = MagicMock()
    mock_client.resolve_provider_id.return_value = "provider-uuid"
    mock_client.create_agent.return_value = {"id": "new-id", "name": "Auto Agent", "status": "draft"}

    args = build_parser().parse_args(["create"])
    cmd_create(mock_client, args)

    mock_client.create_agent.assert_called_once()
    assert mock_client.create_agent.call_args[0][0]["name"] == "Auto Agent"


def test_create_autodetects_prompt_md(tmp_path, monkeypatch, capsys):
    """create uses PROMPT.md in CWD when --instructions is not provided."""
    from algolia_agent.cli import cmd_create

    (tmp_path / "PROMPT.md").write_text("Hello from auto-detected prompt.")
    monkeypatch.chdir(tmp_path)

    mock_client = MagicMock()
    mock_client.resolve_provider_id.return_value = "provider-uuid"
    mock_client.create_agent.return_value = {"id": "new-id", "name": "My Agent", "status": "draft"}

    args = build_parser().parse_args([
        "create",
        "--name", "My Agent",
        "--provider", "hackathon-gemini",
        "--model", "gemini-2.5-flash",
        "--index", "products",
    ])
    cmd_create(mock_client, args)

    call_payload = mock_client.create_agent.call_args[0][0]
    assert call_payload["instructions"] == "Hello from auto-detected prompt."


def test_create_no_config_and_no_agent_config_json(tmp_path, monkeypatch):
    """create raises when --config is absent and no agent-config.json exists."""
    from algolia_agent.cli import cmd_create

    monkeypatch.chdir(tmp_path)
    mock_client = MagicMock()
    args = build_parser().parse_args(["create"])
    with pytest.raises(SystemExit, match="missing required fields"):
        cmd_create(mock_client, args)


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

def _mock_init_client(providers, models=None, indices=None):
    """Patch AlgoliaAgentClient so cmd_init gets a pre-configured mock."""
    mock_client = MagicMock()
    mock_client.list_providers.return_value = providers
    mock_client.list_provider_models.return_value = models or []
    mock_client.list_indices.return_value = indices or []
    return patch("algolia_agent.cli.AlgoliaAgentClient", return_value=mock_client)


def _mock_select(responses):
    """Return a context manager that mocks _select() with a sequence of string values."""
    it = iter(responses)
    return patch("algolia_agent.cli._select", side_effect=lambda *a, **k: next(it))


def test_init_writes_config_and_prompt(tmp_path, monkeypatch):
    from algolia_agent.cli import cmd_init

    providers = [{"id": "uuid", "name": "hackathon-gemini", "defaultModel": "gemini-2.5-flash"}]
    # _select: provider, index, replica(done). input: model (text fallback), name, instructions, description, searchControls(skip)
    inputs = iter(["gemini-2.5-flash", "My Agent", "PROMPT.md", "Main product catalog.", "N"])
    monkeypatch.setattr("sys.stdin", MagicMock(isatty=lambda: True))
    with _mock_init_client(providers):
        with _mock_select(["hackathon-gemini", "products", "<done — no more replicas>"]):
            with patch("builtins.input", lambda _: next(inputs)):
                cmd_init(build_parser().parse_args(["init", "--output-dir", str(tmp_path)]))

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
        "gemini-2.5-flash", "My Agent", "PROMPT.md",
        "Product catalog.",
        "products_{{event_id}}_price_asc", "Sorted by price asc.",
        "N",
    ])
    monkeypatch.setattr("sys.stdin", MagicMock(isatty=lambda: True))
    with _mock_init_client(providers):
        with _mock_select(["hackathon-gemini", "products_{{event_id}}", "<custom name>", "<done — no more replicas>"]):
            with patch("builtins.input", lambda _: next(inputs)):
                cmd_init(build_parser().parse_args(["init", "--output-dir", str(tmp_path)]))

    config = json.loads((tmp_path / "agent-config.json").read_text())
    assert len(config["replicas"]) == 1
    assert config["replicas"][0]["index"] == "products_{{event_id}}_price_asc"
    assert config["replicas"][0]["description"] == "Sorted by price asc."


def test_init_prompts_for_missing_credentials(tmp_path, monkeypatch):
    from algolia_agent.cli import cmd_init

    providers = [{"id": "uuid", "name": "hackathon-gemini", "defaultModel": "gemini-2.5-flash"}]
    # input: app_id, save_to_env, model (text), name, instructions, description, searchControls(skip)
    inputs = iter(["MYAPPID", "n", "gemini-2.5-flash", "My Agent", "PROMPT.md", "Product catalog.", "N"])
    monkeypatch.setattr("sys.stdin", MagicMock(isatty=lambda: True))
    monkeypatch.delenv("ALGOLIA_APP_ID", raising=False)
    monkeypatch.delenv("ALGOLIA_API_KEY", raising=False)

    mock_client = MagicMock()
    mock_client.list_providers.return_value = providers
    mock_client.list_provider_models.return_value = []
    mock_client.list_indices.return_value = []
    with patch("algolia_agent.cli.AlgoliaAgentClient", side_effect=[ValueError("Missing credentials"), mock_client]):
        with patch("algolia_agent.cli.Path.cwd", return_value=MagicMock(
            __truediv__=lambda self, other: MagicMock(exists=lambda: False)
        )):
            with _mock_select(["hackathon-gemini", "products", "<done — no more replicas>"]):
                with patch("builtins.input", lambda _: next(inputs)):
                    with patch("algolia_agent.cli.getpass.getpass", return_value="myapikey"):
                        cmd_init(build_parser().parse_args(["init", "--output-dir", str(tmp_path)]))

    config = json.loads((tmp_path / "agent-config.json").read_text())
    assert config["provider"] == "hackathon-gemini"


def test_init_saves_credentials_to_dotenv(tmp_path, monkeypatch):
    from algolia_agent.cli import cmd_init

    providers = [{"id": "uuid", "name": "hackathon-gemini", "defaultModel": "gemini-2.5-flash"}]
    inputs = iter(["MYAPPID", "Y", "gemini-2.5-flash", "My Agent", "PROMPT.md", "Product catalog.", "N"])
    monkeypatch.setattr("sys.stdin", MagicMock(isatty=lambda: True))
    monkeypatch.delenv("ALGOLIA_APP_ID", raising=False)
    monkeypatch.delenv("ALGOLIA_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)

    mock_client = MagicMock()
    mock_client.list_providers.return_value = providers
    mock_client.list_provider_models.return_value = []
    mock_client.list_indices.return_value = []
    with patch("algolia_agent.cli.AlgoliaAgentClient", side_effect=[ValueError("Missing credentials"), mock_client]):
        with _mock_select(["hackathon-gemini", "products", "<done — no more replicas>"]):
            with patch("builtins.input", lambda _: next(inputs)):
                with patch("algolia_agent.cli.getpass.getpass", return_value="myapikey"):
                    cmd_init(build_parser().parse_args(["init", "--output-dir", str(tmp_path)]))

    env_content = (tmp_path / ".env").read_text()
    assert "ALGOLIA_APP_ID=MYAPPID" in env_content
    assert "ALGOLIA_API_KEY=myapikey" in env_content


def test_init_model_selector(tmp_path, monkeypatch):
    """When /providers/{id}/models returns a list, pick is used for model selection."""
    from algolia_agent.cli import cmd_init

    providers = [{"id": "provider-uuid", "name": "hackathon-gemini"}]
    inputs = iter(["My Agent", "PROMPT.md", "Main product catalog.", "N"])
    monkeypatch.setattr("sys.stdin", MagicMock(isatty=lambda: True))
    mock_client = MagicMock()
    mock_client.list_providers.return_value = providers
    mock_client.list_provider_models.return_value = ["gemini-2.5-flash", "gemini-2.0-flash"]
    mock_client.list_indices.return_value = []
    with patch("algolia_agent.cli.AlgoliaAgentClient", return_value=mock_client):
        with _mock_select(["hackathon-gemini", "gemini-2.0-flash", "products", "<done — no more replicas>"]):
            with patch("builtins.input", lambda _: next(inputs)):
                cmd_init(build_parser().parse_args(["init", "--output-dir", str(tmp_path)]))

    config = json.loads((tmp_path / "agent-config.json").read_text())
    assert config["model"] == "gemini-2.0-flash"
    mock_client.list_provider_models.assert_called_once_with("provider-uuid")


def test_init_index_selector_existing(tmp_path, monkeypatch):
    """When list_indices returns results, pick is used for index selection."""
    from algolia_agent.cli import cmd_init

    providers = [{"id": "provider-uuid", "name": "hackathon-gemini"}]
    inputs = iter(["My Agent", "PROMPT.md", "Product catalog.", "N"])
    monkeypatch.setattr("sys.stdin", MagicMock(isatty=lambda: True))
    mock_client = MagicMock()
    mock_client.list_providers.return_value = providers
    mock_client.list_provider_models.return_value = ["gemini-2.5-flash", "gemini-2.0-flash"]
    mock_client.list_indices.return_value = ["products_a", "products_b"]
    with patch("algolia_agent.cli.AlgoliaAgentClient", return_value=mock_client):
        with _mock_select(["hackathon-gemini", "gemini-2.5-flash", "products_b", "<done — no more replicas>"]):
            with patch("builtins.input", lambda _: next(inputs)):
                cmd_init(build_parser().parse_args(["init", "--output-dir", str(tmp_path)]))

    config = json.loads((tmp_path / "agent-config.json").read_text())
    assert config["index"] == "products_b"


def test_init_index_selector_custom(tmp_path, monkeypatch):
    """Typing a custom/template index name in the autocomplete field is accepted directly."""
    from algolia_agent.cli import cmd_init

    providers = [{"id": "provider-uuid", "name": "hackathon-gemini"}]
    inputs = iter(["My Agent", "PROMPT.md", "Product catalog.", "N"])
    monkeypatch.setattr("sys.stdin", MagicMock(isatty=lambda: True))
    mock_client = MagicMock()
    mock_client.list_providers.return_value = providers
    mock_client.list_provider_models.return_value = ["gemini-2.5-flash", "gemini-2.0-flash"]
    mock_client.list_indices.return_value = ["products_a", "products_b"]
    with patch("algolia_agent.cli.AlgoliaAgentClient", return_value=mock_client):
        with _mock_select(["hackathon-gemini", "gemini-2.5-flash", "products_{{event_id}}", "<done — no more replicas>"]):
            with patch("builtins.input", lambda _: next(inputs)):
                cmd_init(build_parser().parse_args(["init", "--output-dir", str(tmp_path)]))

    config = json.loads((tmp_path / "agent-config.json").read_text())
    assert config["index"] == "products_{{event_id}}"


def test_init_no_index_from_picker(tmp_path, monkeypatch):
    """Selecting <no index> from the index autocomplete creates a config without tools."""
    from algolia_agent.cli import cmd_init

    providers = [{"id": "provider-uuid", "name": "hackathon-gemini"}]
    inputs = iter(["My Agent", "PROMPT.md"])
    monkeypatch.setattr("sys.stdin", MagicMock(isatty=lambda: True))
    mock_client = MagicMock()
    mock_client.list_providers.return_value = providers
    mock_client.list_provider_models.return_value = ["gemini-2.5-flash"]
    mock_client.list_indices.return_value = ["products_a", "products_b"]
    with patch("algolia_agent.cli.AlgoliaAgentClient", return_value=mock_client):
        with _mock_select(["hackathon-gemini", "gemini-2.5-flash", "<no index — create without tools>"]):
            with patch("builtins.input", lambda _: next(inputs)):
                cmd_init(build_parser().parse_args(["init", "--output-dir", str(tmp_path)]))

    config = json.loads((tmp_path / "agent-config.json").read_text())
    assert "index" not in config
    assert "index_description" not in config
    assert "replicas" not in config


def test_init_no_index_with_no_existing_indices(tmp_path, monkeypatch):
    """Selecting <no index> when no indices exist creates a config without tools."""
    from algolia_agent.cli import cmd_init

    providers = [{"id": "provider-uuid", "name": "hackathon-gemini"}]
    inputs = iter(["My Agent", "PROMPT.md"])
    monkeypatch.setattr("sys.stdin", MagicMock(isatty=lambda: True))
    mock_client = MagicMock()
    mock_client.list_providers.return_value = providers
    mock_client.list_provider_models.return_value = ["gemini-2.5-flash"]
    mock_client.list_indices.return_value = []
    with patch("algolia_agent.cli.AlgoliaAgentClient", return_value=mock_client):
        with _mock_select(["hackathon-gemini", "gemini-2.5-flash", "<no index — create without tools>"]):
            with patch("builtins.input", lambda _: next(inputs)):
                cmd_init(build_parser().parse_args(["init", "--output-dir", str(tmp_path)]))

    config = json.loads((tmp_path / "agent-config.json").read_text())
    assert "index" not in config
    assert "index_description" not in config


def test_init_model_selector_fallback_on_error(tmp_path, monkeypatch):
    """When list_provider_models raises AgentAPIError, init falls back to free-text input."""
    from algolia_agent.cli import cmd_init
    from algolia_agent.client import AgentAPIError

    providers = [{"id": "provider-uuid", "name": "hackathon-gemini", "defaultModel": "gemini-2.5-flash"}]
    inputs = iter(["gemini-2.5-flash", "My Agent", "PROMPT.md", "Product catalog.", "N"])
    monkeypatch.setattr("sys.stdin", MagicMock(isatty=lambda: True))
    mock_client = MagicMock()
    mock_client.list_providers.return_value = providers
    mock_client.list_provider_models.side_effect = AgentAPIError(500, "server error")
    mock_client.list_indices.return_value = []
    with patch("algolia_agent.cli.AlgoliaAgentClient", return_value=mock_client):
        with _mock_select(["hackathon-gemini", "products", "<done — no more replicas>"]):
            with patch("builtins.input", lambda _: next(inputs)):
                cmd_init(build_parser().parse_args(["init", "--output-dir", str(tmp_path)]))

    config = json.loads((tmp_path / "agent-config.json").read_text())
    assert config["model"] == "gemini-2.5-flash"


def test_init_non_tty_errors(monkeypatch):
    from algolia_agent.cli import cmd_init
    monkeypatch.setattr("sys.stdin", MagicMock(isatty=lambda: False))
    with pytest.raises(SystemExit, match="interactive terminal"):
        cmd_init(MagicMock(output_dir="."))


def test_init_with_search_controls(tmp_path, monkeypatch):
    """Full searchControls walkthrough writes all specified controls to the config."""
    from algolia_agent.cli import cmd_init

    providers = [{"id": "uuid", "name": "hackathon-gemini", "defaultModel": "gemini-2.5-flash"}]
    inputs = iter([
        "gemini-2.5-flash", "My Agent", "PROMPT.md", "Product catalog.",
        "y",          # set up searchControls?
        "10",         # hitsPerPage max
        "5",          # page max
        "title, price",  # attributesToRetrieve
        "brand, category",  # facets
        "hits, nbHits",  # responseFields
    ])
    monkeypatch.setattr("sys.stdin", MagicMock(isatty=lambda: True))
    with _mock_init_client(providers):
        with _mock_select(["hackathon-gemini", "products", "<done — no more replicas>"]):
            with patch("builtins.input", lambda _: next(inputs)):
                cmd_init(build_parser().parse_args(["init", "--output-dir", str(tmp_path)]))

    config = json.loads((tmp_path / "agent-config.json").read_text())
    sc = config["searchControls"]
    assert sc["hitsPerPage"] == {"exposed": False, "default": 10, "constraint": {"max": 10}}
    assert sc["page"] == {"exposed": False, "default": 0, "constraint": {"max": 5}}
    assert sc["attributesToRetrieve"] == {"exposed": False, "default": ["title", "price"]}
    assert sc["facets"] == {"exposed": False, "default": ["brand", "category"]}
    assert sc["responseFields"] == {"exposed": False, "default": ["hits", "nbHits"]}


def test_init_skip_search_controls(tmp_path, monkeypatch):
    """When the user declines searchControls, no searchControls key is written."""
    from algolia_agent.cli import cmd_init

    providers = [{"id": "uuid", "name": "hackathon-gemini", "defaultModel": "gemini-2.5-flash"}]
    inputs = iter(["gemini-2.5-flash", "My Agent", "PROMPT.md", "Product catalog.", "N"])
    monkeypatch.setattr("sys.stdin", MagicMock(isatty=lambda: True))
    with _mock_init_client(providers):
        with _mock_select(["hackathon-gemini", "products", "<done — no more replicas>"]):
            with patch("builtins.input", lambda _: next(inputs)):
                cmd_init(build_parser().parse_args(["init", "--output-dir", str(tmp_path)]))

    config = json.loads((tmp_path / "agent-config.json").read_text())
    assert "searchControls" not in config


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


# ── build_tool / searchControls ───────────────────────────────────────────────

def test_build_tool_without_search_controls():
    config = {
        "index": "products",
        "index_description": "Product catalog.",
    }
    tool = build_tool(config)
    assert tool == {
        "name": "algolia_search_index",
        "type": "algolia_search_index",
        "indices": [{"index": "products", "description": "Product catalog."}],
    }
    assert "searchControls" not in tool
    assert "predefinedSearchParameters" not in tool


def test_build_tool_with_search_controls():
    search_controls = {
        "hitsPerPage": {"exposed": True, "default": 5, "constraint": {"max": 5}},
        "attributesToRetrieve": {"exposed": False, "default": ["name", "price"]},
    }
    config = {
        "index": "products",
        "index_description": "Product catalog.",
        "searchControls": search_controls,
    }
    tool = build_tool(config)
    assert "searchControls" not in tool  # not at tool level
    for idx in tool["indices"]:
        assert idx["searchControls"] == search_controls
    assert "predefinedSearchParameters" not in tool


def test_build_tool_with_empty_search_controls():
    config = {
        "index": "products",
        "index_description": "Product catalog.",
        "searchControls": {},
    }
    tool = build_tool(config)
    assert "searchControls" not in tool  # not at tool level
    for idx in tool["indices"]:
        assert idx["searchControls"] == {}


def test_build_tool_with_search_controls_applies_to_all_indices():
    sc = {"hitsPerPage": {"exposed": True, "default": 5, "constraint": {"max": 5}}}
    config = {
        "index": "products",
        "index_description": "Product catalog.",
        "replicas": [
            {"index": "products_price_asc", "description": "Ascending price."},
            {"index": "products_price_desc", "description": "Descending price."},
        ],
        "searchControls": sc,
    }
    tool = build_tool(config)
    assert len(tool["indices"]) == 3
    assert "searchControls" not in tool
    for idx in tool["indices"]:
        assert idx["searchControls"] == sc


def test_build_tool_with_predefined_search_parameters():
    params = {"filters": "status:active", "analytics": True}
    config = {
        "index": "products",
        "index_description": "Product catalog.",
        "predefinedSearchParameters": params,
    }
    tool = build_tool(config)
    assert tool["predefinedSearchParameters"] == params
    assert "searchControls" not in tool


def test_diff_detects_search_controls_change():
    sc = {"hitsPerPage": {"exposed": True, "default": 5, "constraint": {"max": 5}}}
    current = {
        "name": "My Agent",
        "model": "gemini-2.5-flash",
        "instructions": "Hello.",
        "tools": [{"type": "algolia_search_index", "indices": [{"index": "products", "description": "Catalog.", "searchControls": None}]}],
    }
    new_payload = {
        "name": "My Agent",
        "model": "gemini-2.5-flash",
        "instructions": "Hello.",
        "tools": [{"type": "algolia_search_index", "indices": [{"index": "products", "description": "Catalog.", "searchControls": sc}]}],
    }
    changes = _diff(current, new_payload)
    assert changes[0] == "  searchControls:"
    assert len(changes) == 2
    # current had no matching searchControls keys
    assert changes[1].strip().startswith("products: {} →")


def test_diff_no_change_when_search_controls_equal():
    sc = {"hitsPerPage": {"exposed": True, "default": 5, "constraint": {"max": 5}}}
    agent = {
        "name": "My Agent",
        "model": "gemini-2.5-flash",
        "instructions": "Hello.",
        "tools": [{"type": "algolia_search_index", "indices": [{"index": "products", "searchControls": sc}]}],
    }
    changes = _diff(agent, dict(agent))
    assert not changes


def test_diff_detects_search_controls_change_on_replica():
    sc = {"hitsPerPage": {"exposed": True, "default": 5, "constraint": {"max": 5}}}
    indices_without_sc = [
        {"index": "products", "description": "Catalog.", "searchControls": None},
        {"index": "products_price_asc", "description": "Asc.", "searchControls": None},
    ]
    indices_with_sc = [
        {"index": "products", "description": "Catalog.", "searchControls": sc},
        {"index": "products_price_asc", "description": "Asc.", "searchControls": sc},
    ]
    current = {
        "name": "My Agent", "model": "gemini-2.5-flash", "instructions": "Hello.",
        "tools": [{"type": "algolia_search_index", "indices": indices_without_sc}],
    }
    new_payload = {
        "name": "My Agent", "model": "gemini-2.5-flash", "instructions": "Hello.",
        "tools": [{"type": "algolia_search_index", "indices": indices_with_sc}],
    }
    changes = _diff(current, new_payload)
    assert changes[0] == "  searchControls:"
    # both indices changed, so both are named
    assert any(line.strip().startswith("products:") for line in changes[1:])
    assert any(line.strip().startswith("products_price_asc:") for line in changes[1:])


def test_diff_no_false_positive_when_api_expands_search_controls():
    """API-added fields (query, page, responseFields, etc.) do not cause a spurious diff."""
    sc_config = {"hitsPerPage": {"exposed": False, "default": 10, "constraint": {"max": 10}}}
    # The API returns this config plus extra default fields we never sent
    sc_api = {
        "hitsPerPage": {"exposed": False, "default": 10, "constraint": {"max": 10}},
        "query": {"exposed": True, "default": None},
        "page": {"exposed": True, "default": 0},
        "responseFields": {"exposed": False, "default": None},
        "facets": {"exposed": False, "default": None},
        "custom": None,
    }
    current = {
        "name": "My Agent", "model": "gemini-2.5-flash", "instructions": "Hello.",
        "tools": [{"type": "algolia_search_index", "indices": [{"index": "products", "searchControls": sc_api}]}],
    }
    new_payload = {
        "name": "My Agent", "model": "gemini-2.5-flash", "instructions": "Hello.",
        "tools": [{"type": "algolia_search_index", "indices": [{"index": "products", "searchControls": sc_config}]}],
    }
    assert not _diff(current, new_payload)


def test_diff_detects_change_despite_api_expansion():
    """A real change to a config-specified field is still reported even when the API expanded the object."""
    sc_api = {
        "hitsPerPage": {"exposed": False, "default": 10, "constraint": {"max": 10}},
        "query": {"exposed": True, "default": None},
        "page": {"exposed": True, "default": 0},
    }
    sc_new = {"hitsPerPage": {"exposed": False, "default": 5, "constraint": {"max": 5}}}
    current = {
        "name": "My Agent", "model": "gemini-2.5-flash", "instructions": "Hello.",
        "tools": [{"type": "algolia_search_index", "indices": [{"index": "products", "searchControls": sc_api}]}],
    }
    new_payload = {
        "name": "My Agent", "model": "gemini-2.5-flash", "instructions": "Hello.",
        "tools": [{"type": "algolia_search_index", "indices": [{"index": "products", "searchControls": sc_new}]}],
    }
    changes = _diff(current, new_payload)
    assert changes[0] == "  searchControls:"
    assert any("products" in line and "5" in line for line in changes[1:])


def test_diff_detects_clearing_search_controls():
    """Sending searchControls: {} when current has non-empty controls is reported as a change."""
    sc_existing = {"hitsPerPage": {"exposed": False, "default": 10, "constraint": {"max": 10}}}
    current = {
        "name": "My Agent", "model": "gemini-2.5-flash", "instructions": "Hello.",
        "tools": [{"type": "algolia_search_index", "indices": [{"index": "products", "searchControls": sc_existing}]}],
    }
    new_payload = {
        "name": "My Agent", "model": "gemini-2.5-flash", "instructions": "Hello.",
        "tools": [{"type": "algolia_search_index", "indices": [{"index": "products", "searchControls": {}}]}],
    }
    changes = _diff(current, new_payload)
    assert changes[0] == "  searchControls:"
    assert any(line.strip().endswith("→ {}") for line in changes[1:])


def _agent_with_tools(tools):
    return {"name": "My Agent", "model": "gemini-2.5-flash", "instructions": "Hello.", "tools": tools}


def test_diff_detects_added_tool():
    current = _agent_with_tools([{"type": "algolia_search_index", "indices": [{"index": "products"}]}])
    new_payload = _agent_with_tools([
        {"type": "algolia_search_index", "indices": [{"index": "products"}]},
        {"type": "algolia_display_results", "isTerminal": True, "minResultsPerGroup": 1},
    ])
    changes = _diff(current, new_payload)
    assert "  tools:" in changes
    assert any(line.strip() == "+ algolia_display_results" for line in changes)


def test_diff_detects_removed_tool():
    """A payload that drops an existing tool (the silent-clobber case) is now visible."""
    current = _agent_with_tools([
        {"type": "algolia_search_index", "indices": [{"index": "products"}]},
        {"type": "algolia_display_results", "isTerminal": True, "minResultsPerGroup": 1},
    ])
    new_payload = _agent_with_tools([{"type": "algolia_search_index", "indices": [{"index": "products"}]}])
    changes = _diff(current, new_payload)
    assert "  tools:" in changes
    assert any(line.strip() == "- algolia_display_results" for line in changes)


def test_diff_detects_tool_scalar_field_change():
    current = _agent_with_tools([{"type": "algolia_display_results", "isTerminal": False, "minResultsPerGroup": 3}])
    new_payload = _agent_with_tools([{"type": "algolia_display_results", "isTerminal": True, "minResultsPerGroup": 1}])
    changes = _diff(current, new_payload)
    assert any("algolia_display_results.isTerminal" in line and "False" in line and "True" in line for line in changes)
    assert any("algolia_display_results.minResultsPerGroup" in line and "3" in line and "1" in line for line in changes)


def test_diff_no_tool_change_when_identical():
    agent = _agent_with_tools([
        {"type": "algolia_search_index", "indices": [{"index": "products"}]},
        {"type": "algolia_display_results", "isTerminal": True, "minResultsPerGroup": 1},
    ])
    assert not _diff(agent, dict(agent))


def test_diff_no_tool_false_positive_from_api_expanded_fields():
    """Tool-level fields the API adds but we never send (mode, description) must not diff."""
    current = _agent_with_tools([{
        "type": "algolia_search_index",
        "name": "algolia_search_index",
        "mode": "static",
        "allowUnlistedIndices": False,
        "description": "API-added blurb.",
        "indices": [{"index": "products"}],
    }])
    new_payload = _agent_with_tools([{
        "type": "algolia_search_index",
        "name": "algolia_search_index",
        "indices": [{"index": "products"}],
    }])
    assert not _diff(current, new_payload)


def test_diff_detects_predefined_search_parameters_change():
    """predefinedSearchParameters lives at tool level; a change must not report as no-op."""
    current = _agent_with_tools([{
        "type": "algolia_search_index",
        "indices": [{"index": "products"}],
        "predefinedSearchParameters": {"hitsPerPage": 10},
    }])
    new_payload = _agent_with_tools([{
        "type": "algolia_search_index",
        "indices": [{"index": "products"}],
        "predefinedSearchParameters": {"hitsPerPage": 25},
    }])
    changes = _diff(current, new_payload)
    assert any("algolia_search_index.predefinedSearchParameters" in line for line in changes)
    assert any("25" in line for line in changes)


def test_diff_detects_added_predefined_search_parameters():
    current = _agent_with_tools([{
        "type": "algolia_search_index",
        "indices": [{"index": "products"}],
    }])
    new_payload = _agent_with_tools([{
        "type": "algolia_search_index",
        "indices": [{"index": "products"}],
        "predefinedSearchParameters": {"filters": "in_stock:true"},
    }])
    changes = _diff(current, new_payload)
    assert any("algolia_search_index.predefinedSearchParameters" in line for line in changes)


def test_diff_no_false_positive_when_predefined_params_unchanged():
    """Only keys we send are compared — API-expanded defaults must not diff."""
    current = _agent_with_tools([{
        "type": "algolia_search_index",
        "indices": [{"index": "products"}],
        "predefinedSearchParameters": {
            "hitsPerPage": 10,
            "page": 0,
            "responseFields": ["*"],
        },
    }])
    new_payload = _agent_with_tools([{
        "type": "algolia_search_index",
        "indices": [{"index": "products"}],
        "predefinedSearchParameters": {"hitsPerPage": 10},
    }])
    assert not _diff(current, new_payload)


def test_diff_detects_clearing_predefined_search_parameters():
    """An empty object is compared unfiltered, so clearing is still visible."""
    current = _agent_with_tools([{
        "type": "algolia_search_index",
        "indices": [{"index": "products"}],
        "predefinedSearchParameters": {"hitsPerPage": 10},
    }])
    new_payload = _agent_with_tools([{
        "type": "algolia_search_index",
        "indices": [{"index": "products"}],
        "predefinedSearchParameters": {},
    }])
    changes = _diff(current, new_payload)
    assert any("algolia_search_index.predefinedSearchParameters" in line for line in changes)


def test_diff_detects_config_block_change():
    current = _agent_with_tools([{"type": "algolia_search_index", "indices": [{"index": "products"}]}])
    current["config"] = {"temperature": 0.2}
    new_payload = _agent_with_tools([{"type": "algolia_search_index", "indices": [{"index": "products"}]}])
    new_payload["config"] = {"temperature": 0.9}
    changes = _diff(current, new_payload)
    assert any("config:" in line and "0.9" in line for line in changes)


def test_diff_no_config_change_when_identical():
    agent = _agent_with_tools([{"type": "algolia_search_index", "indices": [{"index": "products"}]}])
    agent["config"] = {"temperature": 0.2}
    assert not _diff(agent, dict(agent))


# ── issue #12: remaining false positives ──────────────────────────────────────

def _agent_with_controls(controls):
    return _agent_with_tools([{
        "type": "algolia_search_index",
        "indices": [{"index": "products", "description": "Products", "searchControls": controls}],
    }])


def test_diff_ignores_api_expanded_nulls_nested_in_search_controls():
    """issue #12 case 1: the API adds null sub-fields inside values of keys we did send."""
    current = _agent_with_controls({
        "hitsPerPage": {"exposed": True, "default": 5, "constraint": {"min": None, "max": 5}},
        "attributesToRetrieve": {"exposed": False, "constraint": None, "merge": None},
    })
    new_payload = _agent_with_controls({
        "hitsPerPage": {"exposed": True, "default": 5, "constraint": {"max": 5}},
        "attributesToRetrieve": {"exposed": False},
    })
    assert not _diff(current, new_payload)


def test_diff_ignores_api_expanded_nulls_nested_in_predefined_params():
    """Same nesting problem on the tool-level object path."""
    current = _agent_with_tools([{
        "type": "algolia_search_index",
        "indices": [{"index": "products"}],
        "predefinedSearchParameters": {"facetFilters": {"value": "a", "extra": None}},
    }])
    new_payload = _agent_with_tools([{
        "type": "algolia_search_index",
        "indices": [{"index": "products"}],
        "predefinedSearchParameters": {"facetFilters": {"value": "a"}},
    }])
    assert not _diff(current, new_payload)


def test_diff_still_detects_real_nested_search_controls_change():
    """Pruning must not mask a genuine change to a nested value we do send."""
    current = _agent_with_controls({"hitsPerPage": {"exposed": True, "default": 5}})
    new_payload = _agent_with_controls({"hitsPerPage": {"exposed": True, "default": 20}})
    changes = _diff(current, new_payload)
    assert changes[0] == "  searchControls:"
    assert any("products" in line and "20" in line for line in changes[1:])


def test_diff_ignores_instructions_trailing_whitespace():
    """issue #12 case 2: API-stored copy vs file on disk differing only by a newline."""
    base = {"name": "My Agent", "model": "gemini-2.5-flash", "tools": []}
    current = dict(base, instructions="line one\nline two\n")
    new_payload = dict(base, instructions="line one\nline two")
    assert not _diff(current, new_payload)


def test_diff_still_detects_real_instructions_change():
    base = {"name": "My Agent", "model": "gemini-2.5-flash", "tools": []}
    current = dict(base, instructions="line one\n")
    new_payload = dict(base, instructions="something else\n")
    assert any("instructions" in line for line in _diff(current, new_payload))


# ── PR #13 review findings ────────────────────────────────────────────────────

def test_diff_names_the_index_whose_search_controls_changed():
    """A change on one index must not print another index's (unchanged) values.

    Sampling the first index produced output with identical-looking sides, announcing
    a change the reader could not see.
    """
    def agent(sc_products, sc_replica):
        return _agent_with_tools([{
            "type": "algolia_search_index",
            "indices": [
                {"index": "products", "description": "d", "searchControls": sc_products},
                {"index": "products_price_asc", "description": "d", "searchControls": sc_replica},
            ],
        }])

    same = {"hitsPerPage": {"default": 5}}
    changes = _diff(agent(same, same), agent(same, {"hitsPerPage": {"default": 99}}))
    assert changes[0] == "  searchControls:"
    changed = [line for line in changes[1:]]
    assert len(changed) == 1, changed
    assert changed[0].strip().startswith("products_price_asc:")
    assert "99" in changed[0]
    # No line may show identical values on both sides.
    for line in changed:
        before, _, after = line.partition(" → ")
        assert before.split(": ", 1)[1] != after


def test_diff_detects_leading_whitespace_change_in_instructions():
    """Only trailing whitespace is noise; leading indentation is content."""
    base = {"name": "My Agent", "model": "gemini-2.5-flash", "tools": []}
    current = dict(base, instructions="line one\nline two")
    new_payload = dict(base, instructions="\n\nline one\nline two")
    assert any("instructions" in line for line in _diff(current, new_payload))
