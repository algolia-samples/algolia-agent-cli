import re

MUSTACHE_RE = re.compile(r'\{\{(\w+)\}\}')


def extract_variables(text: str) -> list[str]:
    """Return unique variable names found in {{...}} placeholders, in order of first appearance."""
    return list(dict.fromkeys(MUSTACHE_RE.findall(text)))


def render(text: str, variables: dict[str, str]) -> str:
    """Substitute all {{key}} with values. Raises ValueError if any variable is missing."""
    missing = [v for v in extract_variables(text) if v not in variables]
    if missing:
        raise ValueError(f"Missing template variables: {', '.join(missing)}")
    return MUSTACHE_RE.sub(lambda m: variables[m.group(1)], text)
