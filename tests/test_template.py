import pytest
from algolia_agent.template import extract_variables, render


def test_extract_simple():
    assert extract_variables("Hello {{name}}!") == ["name"]


def test_extract_multiple():
    assert extract_variables("{{a}} and {{b}} and {{a}}") == ["a", "b"]


def test_extract_preserves_order():
    assert extract_variables("{{z}} {{a}} {{m}}") == ["z", "a", "m"]


def test_extract_none():
    assert extract_variables("No placeholders here.") == []


def test_extract_numeric_placeholder():
    # {{5}} has a digit — \w matches digits too
    assert extract_variables("max {{5}} results") == ["5"]


def test_render_simple():
    assert render("Hello {{name}}!", {"name": "World"}) == "Hello World!"


def test_render_multiple():
    result = render("{{greeting}}, {{name}}!", {"greeting": "Hi", "name": "Alice"})
    assert result == "Hi, Alice!"


def test_render_repeated_placeholder():
    result = render("{{x}} + {{x}} = double {{x}}", {"x": "y"})
    assert result == "y + y = double y"


def test_render_extra_vars_ok():
    # Extra keys in variables dict are silently ignored
    result = render("Hello {{name}}!", {"name": "World", "unused": "ignored"})
    assert result == "Hello World!"


def test_render_missing_raises():
    with pytest.raises(ValueError, match="Missing template variables"):
        render("Hello {{name}} from {{place}}!", {"name": "Alice"})


def test_render_missing_lists_all():
    with pytest.raises(ValueError) as exc_info:
        render("{{a}} {{b}} {{c}}", {})
    assert "a" in str(exc_info.value)
    assert "b" in str(exc_info.value)
    assert "c" in str(exc_info.value)
