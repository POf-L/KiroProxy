"""管理员设置持久化与加载

该模块用于将「设置面板」相关配置持久化到 admin 配置（DATA_DIR/admin.json）。

当前持久化的设置分区：
- history: 历史消息配置
- rate_limit: 限速配置
- refresh: 刷新配置
- model_routing: 模型路由配置
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict

from .history_manager import update_history_config
from .model_routing import update_model_routing_config
from .persistence import load_admin_config_async, save_admin_config_async
from .rate_limiter import get_rate_limiter
from .refresh_manager import get_refresh_manager


_SETTINGS_KEY = "settings"
_lock = asyncio.Lock()


def _as_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


async def load_admin_settings_async() -> Dict[str, Any]:
    """加载并应用已持久化的管理员设置"""
    admin_config = _as_dict(await load_admin_config_async())
    settings = _as_dict(admin_config.get(_SETTINGS_KEY))

    history_cfg = settings.get("history")
    if isinstance(history_cfg, dict) and history_cfg:
        try:
            update_history_config(history_cfg)
        except Exception:
            pass

    rate_limit_cfg = settings.get("rate_limit")
    if isinstance(rate_limit_cfg, dict) and rate_limit_cfg:
        try:
            get_rate_limiter().update_config(**rate_limit_cfg)
        except Exception:
            pass

    refresh_cfg = settings.get("refresh")
    if isinstance(refresh_cfg, dict) and refresh_cfg:
        try:
            get_refresh_manager().update_config(**refresh_cfg)
        except Exception:
            pass

    model_routing_cfg = settings.get("model_routing")
    if isinstance(model_routing_cfg, dict) and model_routing_cfg:
        try:
            update_model_routing_config(model_routing_cfg)
        except Exception:
            pass

    return settings


async def persist_admin_setting(section: str, value: Dict[str, Any]) -> bool:
    """更新某个设置分区并持久化（会与已有 admin_config 合并）"""
    if not isinstance(section, str) or not section:
        return False
    if not isinstance(value, dict):
        return False

    async with _lock:
        admin_config = _as_dict(await load_admin_config_async())
        settings = _as_dict(admin_config.get(_SETTINGS_KEY))
        settings[section] = value
        admin_config[_SETTINGS_KEY] = settings
        return bool(await save_admin_config_async(admin_config))
