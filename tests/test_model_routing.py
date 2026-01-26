def test_apply_model_routing_passthrough_by_default(monkeypatch):
    from kiro_proxy.core.model_routing import apply_model_routing, get_model_routing_config, update_model_routing_config

    monkeypatch.delenv("KIROPROXY_FORCE_AUTO_MODEL", raising=False)
    prev = get_model_routing_config().force_auto_model
    try:
        update_model_routing_config({"force_auto_model": False})
        assert apply_model_routing("claude-sonnet-4") == "claude-sonnet-4"
    finally:
        update_model_routing_config({"force_auto_model": prev})


def test_apply_model_routing_can_force_auto(monkeypatch):
    from kiro_proxy.core.model_routing import apply_model_routing, get_model_routing_config, update_model_routing_config

    monkeypatch.delenv("KIROPROXY_FORCE_AUTO_MODEL", raising=False)
    prev = get_model_routing_config().force_auto_model
    try:
        update_model_routing_config({"force_auto_model": True})
        assert apply_model_routing("claude-sonnet-4") == "auto"
        assert apply_model_routing(None) == "auto"
    finally:
        update_model_routing_config({"force_auto_model": prev})
