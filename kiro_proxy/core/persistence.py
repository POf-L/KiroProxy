"""配置持久化 - 文件系统存储"""
import asyncio
from typing import List, Dict, Any
from .database import get_database

# 说明：
# - 该模块同时提供同步与异步接口，便于 CLI / 初始化阶段使用。
# - 在 ASGI 运行时（事件循环已启动）不允许使用 asyncio.run()。
#   对于“写入”类同步接口：在事件循环中改为 create_task() 异步落盘。
#   对于“读取”类同步接口：请使用对应异步接口（否则会阻塞/崩溃）。


def _in_running_loop() -> bool:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


# 向后兼容的同步接口
def save_accounts(accounts: List[Dict[str, Any]]) -> bool:
    """保存账号配置（同步接口）"""
    if _in_running_loop():
        asyncio.create_task(_save_accounts_async(accounts))
        return True
    return asyncio.run(_save_accounts_async(accounts))

def load_accounts() -> List[Dict[str, Any]]:
    """加载账号配置（同步接口）"""
    if _in_running_loop():
        raise RuntimeError("load_accounts() 不能在运行中的事件循环里调用，请使用异步接口")
    return asyncio.run(_load_accounts_async())

def save_config(config: Dict[str, Any]) -> bool:
    """保存完整配置（同步接口）"""
    if _in_running_loop():
        asyncio.create_task(_save_config_async(config))
        return True
    return asyncio.run(_save_config_async(config))

def load_config() -> Dict[str, Any]:
    """加载完整配置（同步接口）"""
    if _in_running_loop():
        raise RuntimeError("load_config() 不能在运行中的事件循环里调用，请使用异步接口")
    return asyncio.run(_load_config_async())

def export_config() -> Dict[str, Any]:
    """导出配置（用于备份）"""
    return load_config()

def import_config(config: Dict[str, Any]) -> bool:
    """导入配置（用于恢复）"""
    return save_config(config)

# 异步接口
async def _save_accounts_async(accounts: List[Dict[str, Any]]) -> bool:
    """保存账号配置（异步）"""
    try:
        db = await get_database()
        success = await db.save_accounts(accounts)

        if success:
            # 自动清理缓存中已删除的账号
            from .quota_cache import cleanup_cache_for_accounts
            account_ids = {acc.get("id", "") for acc in accounts if acc.get("id")}
            cleanup_stats = cleanup_cache_for_accounts(account_ids)

            if cleanup_stats["total_cleaned"] > 0:
                print(f"[Persistence] 缓存清理完成: {cleanup_stats}")

        return success
    except Exception as e:
        print(f"[Persistence] 保存账号配置失败: {e}")
        return False

async def _load_accounts_async() -> List[Dict[str, Any]]:
    """加载账号配置（异步）"""
    try:
        db = await get_database()
        return await db.load_accounts()
    except Exception as e:
        print(f"[Persistence] 加载账号配置失败: {e}")
        return []

async def _save_config_async(config: Dict[str, Any]) -> bool:
    """保存完整配置（异步）"""
    try:
        db = await get_database()
        return await db.save_config(config)
    except Exception as e:
        print(f"[Persistence] 保存配置失败: {e}")
        return False

async def _load_config_async() -> Dict[str, Any]:
    """加载完整配置（异步）"""
    try:
        db = await get_database()
        return await db.load_config()
    except Exception as e:
        print(f"[Persistence] 加载配置失败: {e}")
        return {}

# 新增：管理员配置接口
async def save_admin_config_async(admin_config: Dict[str, Any]) -> bool:
    """保存管理员配置（异步）"""
    try:
        db = await get_database()
        return await db.save_admin_config(admin_config)
    except Exception as e:
        print(f"[Persistence] 保存管理员配置失败: {e}")
        return False

async def load_admin_config_async() -> Dict[str, Any]:
    """加载管理员配置（异步）"""
    try:
        db = await get_database()
        return await db.load_admin_config()
    except Exception as e:
        print(f"[Persistence] 加载管理员配置失败: {e}")
        return {}

def save_admin_config(admin_config: Dict[str, Any]) -> bool:
    """保存管理员配置（同步接口）"""
    if _in_running_loop():
        asyncio.create_task(save_admin_config_async(admin_config))
        return True
    return asyncio.run(save_admin_config_async(admin_config))

def load_admin_config() -> Dict[str, Any]:
    """加载管理员配置（同步接口）"""
    if _in_running_loop():
        raise RuntimeError("load_admin_config() 不能在运行中的事件循环里调用，请使用 load_admin_config_async()")
    return asyncio.run(load_admin_config_async())

# 向后兼容：保留原有函数名
def ensure_config_dir():
    """确保配置目录存在（向后兼容，现在由数据库层处理）"""
    pass
