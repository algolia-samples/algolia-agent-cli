import pytest


@pytest.fixture
def sample_instructions():
    return "Hello {{event_name}}, booth {{booth}}. Limit: {{5}} searches."


@pytest.fixture
def sample_config():
    return {
        "name": "Test Agent",
        "provider": "hackathon-gemini",
        "model": "gemini-2.5-flash",
        "instructions": "PROMPT.md",
        "index": "products",
        "replicas": ["products_price_asc", "products_price_desc"],
        "config": {"suggestions": {"enabled": True}},
    }
