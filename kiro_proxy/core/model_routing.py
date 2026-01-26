"""模型路由配置

用于在运行时控制模型选择策略，例如强制将所有请求的 model 路由为 auto。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass
class ModelRoutingConfig:
    """模型路由配置"""

    # 强制路由模式：None(禁用), "auto"(强制auto), 或具体模型名
    force_model: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "force_model": self.force_model,
            # 向后兼容
            "force_auto_model": self.force_model == "auto"
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ModelRoutingConfig":
        if not isinstance(data, dict):
            data = {}

        # 优先使用新的force_model字段
        if "force_model" in data:
            return cls(force_model=data.get("force_model"))

        # 向后兼容：从force_auto_model转换
        if data.get("force_auto_model", False):
            return cls(force_model="auto")

        return cls(force_model=None)

    # 向后兼容的属性
    @property
    def force_auto_model(self) -> bool:
        """向后兼容：是否强制使用auto模型"""
        return self.force_model == "auto"

    @force_auto_model.setter
    def force_auto_model(self, value: bool):
        """向后兼容：设置是否强制使用auto模型"""
        if value:
            self.force_model = "auto"
        else:
            self.force_model = None


_model_routing_config = ModelRoutingConfig()


def _apply_env_overrides() -> None:
    """应用环境变量覆盖"""
    # 新的环境变量：KIROPROXY_FORCE_MODEL
    env_force_model = os.getenv("KIROPROXY_FORCE_MODEL")
    if env_force_model is not None:
        v = env_force_model.strip()
        if v.lower() in ("0", "false", "no", "off", "none", ""):
            _model_routing_config.force_model = None
        else:
            _model_routing_config.force_model = v
        return

    # 向后兼容：KIROPROXY_FORCE_AUTO_MODEL
    env_force_auto = os.getenv("KIROPROXY_FORCE_AUTO_MODEL")
    if env_force_auto is None:
        return
    v = env_force_auto.strip().lower()
    if v in ("0", "false", "no", "off"):
        _model_routing_config.force_model = None
    elif v in ("1", "true", "yes", "on"):
        _model_routing_config.force_model = "auto"


# 环境变量覆盖（便于 Docker/部署场景快速切换）
# 新版本：
# - KIROPROXY_FORCE_MODEL=auto: 强制使用 auto
# - KIROPROXY_FORCE_MODEL=claude-sonnet-4: 强制使用指定模型
# - KIROPROXY_FORCE_MODEL=0/false/off/none: 禁用强制路由
#
# 向后兼容：
# - KIROPROXY_FORCE_AUTO_MODEL=0/false/off: 关闭强制路由
# - KIROPROXY_FORCE_AUTO_MODEL=1/true/on: 启用强制 auto
_env_force_model = os.getenv("KIROPROXY_FORCE_MODEL")
_env_force_auto = os.getenv("KIROPROXY_FORCE_AUTO_MODEL")

if _env_force_model is not None:
    v = _env_force_model.strip()
    if v.lower() in ("0", "false", "no", "off", "none", ""):
        _model_routing_config.force_model = None
        print(f"[ModelRouting] 已通过环境变量禁用强制路由 (KIROPROXY_FORCE_MODEL={v})")
    else:
        _model_routing_config.force_model = v
        print(f"[ModelRouting] 已通过环境变量启用强制路由到 {v} (KIROPROXY_FORCE_MODEL={v})")
elif _env_force_auto is not None:
    v = _env_force_auto.strip().lower()
    if v in ("0", "false", "no", "off"):
        _model_routing_config.force_model = None
        print("[ModelRouting] 已通过环境变量关闭强制路由 (KIROPROXY_FORCE_AUTO_MODEL=0)")
    elif v in ("1", "true", "yes", "on"):
        _model_routing_config.force_model = "auto"
        print("[ModelRouting] 已通过环境变量启用强制 auto (KIROPROXY_FORCE_AUTO_MODEL=1)")


def get_model_routing_config() -> ModelRoutingConfig:
    """获取模型路由配置"""

    return _model_routing_config


def set_model_routing_config(config: ModelRoutingConfig):
    """设置模型路由配置"""

    global _model_routing_config
    _model_routing_config = config


def update_model_routing_config(data: Dict[str, Any]) -> ModelRoutingConfig:
    """更新模型路由配置"""

    global _model_routing_config
    _model_routing_config = ModelRoutingConfig.from_dict(data)
    _apply_env_overrides()
    return _model_routing_config


def apply_model_routing(model: Optional[str]) -> Optional[str]:
    """对入参 model 应用路由覆盖（支持强制路由到指定模型）"""

    config = get_model_routing_config()
    if config.force_model is not None:
        return config.force_model
    return model
