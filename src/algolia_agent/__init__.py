"""CLI for the Algolia Agent Studio REST API."""

# Single source of truth for the version. pyproject.toml reads it via
# [tool.setuptools.dynamic], the CLI reports it through --version, and the client
# sends it in the User-Agent — so a release cannot disagree with itself. CI checks
# this value against the git tag whenever a v* tag is pushed.
__version__ = "1.3.0"
