import pytest


def test_map_model_name_downgrades_opus():
    from kiro_proxy.config import map_model_name

    assert map_model_name("claude-opus-4.5") == "claude-sonnet-4.5"
    assert map_model_name("claude-3-opus-20240229") == "claude-sonnet-4.5"
    assert map_model_name("claude-3-opus-latest") == "claude-sonnet-4.5"
    assert map_model_name("claude-4-opus") == "claude-sonnet-4.5"
    assert map_model_name("o1") == "claude-sonnet-4.5"
    assert map_model_name("o1-preview") == "claude-sonnet-4.5"
    assert map_model_name("opus") == "claude-sonnet-4.5"


@pytest.mark.asyncio
async def test_models_fallback_does_not_advertise_opus(monkeypatch):
    from kiro_proxy.routers import protocols

    monkeypatch.setattr(protocols.state, "get_available_account", lambda *args, **kwargs: None)

    resp = await protocols.models()
    model_ids = {m["id"] for m in resp.get("data", [])}
    assert "claude-opus-4.5" not in model_ids

