"""配置存储抽象层（仅文件系统持久化）"""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class DatabaseInterface(ABC):
    """配置存储接口抽象类"""

    @abstractmethod
    async def save_accounts(self, accounts: List[Dict[str, Any]]) -> bool:
        """保存账号配置"""

    @abstractmethod
    async def load_accounts(self) -> List[Dict[str, Any]]:
        """加载账号配置"""

    @abstractmethod
    async def save_config(self, config: Dict[str, Any]) -> bool:
        """保存完整配置"""

    @abstractmethod
    async def load_config(self) -> Dict[str, Any]:
        """加载完整配置"""

    @abstractmethod
    async def save_admin_config(self, admin_config: Dict[str, Any]) -> bool:
        """保存管理员配置"""

    @abstractmethod
    async def load_admin_config(self) -> Dict[str, Any]:
        """加载管理员配置"""

    @abstractmethod
    async def initialize(self) -> bool:
        """初始化存储"""


class FileSystemDatabase(DatabaseInterface):
    """文件系统存储实现（DATA_DIR/config.json + DATA_DIR/admin.json）"""

    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.config_file = data_dir / "config.json"
        self.admin_config_file = data_dir / "admin.json"

    def _ensure_dir(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)

    async def initialize(self) -> bool:
        try:
            self._ensure_dir()
            return True
        except Exception as exc:
            logger.error(f"文件系统初始化失败: {exc}")
            return False

    async def save_accounts(self, accounts: List[Dict[str, Any]]) -> bool:
        try:
            self._ensure_dir()
            config = await self.load_config()
            config["accounts"] = accounts
            return await self.save_config(config)
        except Exception as exc:
            logger.error(f"保存账号配置失败: {exc}")
            return False

    async def load_accounts(self) -> List[Dict[str, Any]]:
        config = await self.load_config()
        return config.get("accounts", [])

    async def save_config(self, config: Dict[str, Any]) -> bool:
        try:
            self._ensure_dir()
            self.config_file.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")
            return True
        except Exception as exc:
            logger.error(f"保存配置失败: {exc}")
            return False

    async def load_config(self) -> Dict[str, Any]:
        try:
            if self.config_file.exists():
                return json.loads(self.config_file.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.error(f"加载配置失败: {exc}")
        return {}

    async def save_admin_config(self, admin_config: Dict[str, Any]) -> bool:
        try:
            self._ensure_dir()
            self.admin_config_file.write_text(
                json.dumps(admin_config, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            return True
        except Exception as exc:
            logger.error(f"保存管理员配置失败: {exc}")
            return False

    async def load_admin_config(self) -> Dict[str, Any]:
        try:
            if self.admin_config_file.exists():
                return json.loads(self.admin_config_file.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.error(f"加载管理员配置失败: {exc}")
        return {}


def create_database() -> DatabaseInterface:
    """创建存储实例（仅文件系统）"""
    from ..config import DATA_DIR

    logger.info(f"使用文件系统存储: {DATA_DIR}")
    return FileSystemDatabase(DATA_DIR)


_db_instance: Optional[DatabaseInterface] = None


async def get_database() -> DatabaseInterface:
    """获取全局存储实例（单例模式）"""
    global _db_instance

    if _db_instance is None:
        db = create_database()
        await db.initialize()
        _db_instance = db
    return _db_instance
