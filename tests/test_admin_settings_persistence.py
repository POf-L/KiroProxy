import pytest


@pytest.mark.asyncio
async def test_persist_admin_setting_merges_into_existing_admin_config(monkeypatch):
    from kiro_proxy.core import admin_settings

    async def fake_load():
        return {"foo": "bar", "settings": {"history": {"max_retries": 1}}}

    saved = {}

    async def fake_save(data):
        saved.clear()
        saved.update(data)
        return True

    monkeypatch.setattr(admin_settings, "load_admin_config_async", fake_load)
    monkeypatch.setattr(admin_settings, "save_admin_config_async", fake_save)

    ok = await admin_settings.persist_admin_setting("model_routing", {"force_auto_model": True})
    assert ok is True

    assert saved["foo"] == "bar"
    assert saved["settings"]["history"] == {"max_retries": 1}
    assert saved["settings"]["model_routing"] == {"force_auto_model": True}


@pytest.mark.asyncio
async def test_load_admin_settings_applies_known_sections(monkeypatch):
    from kiro_proxy.core import admin_settings

    async def fake_load():
        return {
            "settings": {
                "history": {"max_retries": 9},
                "rate_limit": {"enabled": True, "min_request_interval": 1.0},
                "refresh": {"max_retries": 2, "concurrency": 1, "retry_base_delay": 1.0, "token_refresh_before_expiry": 60, "auto_refresh_interval": 5},
                "model_routing": {"force_auto_model": True},
            }
        }

    monkeypatch.setattr(admin_settings, "load_admin_config_async", fake_load)

    applied = {}

    def fake_update_history_config(data):
        applied["history"] = data

    def fake_update_model_routing_config(data):
        applied["model_routing"] = data

    class DummyLimiter:
        def update_config(self, **kwargs):
            applied["rate_limit"] = kwargs

    class DummyRefreshManager:
        def update_config(self, **kwargs):
            applied["refresh"] = kwargs

    monkeypatch.setattr(admin_settings, "update_history_config", fake_update_history_config)
    monkeypatch.setattr(admin_settings, "update_model_routing_config", fake_update_model_routing_config)
    monkeypatch.setattr(admin_settings, "get_rate_limiter", lambda: DummyLimiter())
    monkeypatch.setattr(admin_settings, "get_refresh_manager", lambda: DummyRefreshManager())

    settings = await admin_settings.load_admin_settings_async()
    assert isinstance(settings, dict)

    assert applied["history"]["max_retries"] == 9
    assert applied["rate_limit"]["enabled"] is True
    assert applied["refresh"]["auto_refresh_interval"] == 5
    assert applied["model_routing"]["force_auto_model"] is True

