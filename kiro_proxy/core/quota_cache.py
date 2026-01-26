"""额度缓存管理模块

提供账号额度信息的内存缓存和文件持久化功能。
支持智能清理、增量更新和性能监控。
"""
import json
import time
import asyncio
import os
from collections import OrderedDict
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Optional, Dict, Any, Set, List, Tuple
from threading import Lock


# 默认缓存过期时间（秒）
DEFAULT_CACHE_MAX_AGE = 300  # 5分钟

# 低余额阈值
LOW_BALANCE_THRESHOLD = 0.2

# 缓存配置
DEFAULT_MAX_CACHE_SIZE = 1000  # 最大缓存条目数
DEFAULT_BATCH_SIZE = 50        # 批量更新大小
DEFAULT_CLEANUP_INTERVAL = 3600  # 自动清理间隔（秒）
MIN_FILE_SIZE_FOR_INCREMENTAL = 1024 * 10  # 10KB以上使用增量更新


@dataclass
class CacheStats:
    """缓存统计信息"""
    hit_count: int = 0
    miss_count: int = 0
    total_requests: int = 0
    cleanup_count: int = 0
    incremental_saves: int = 0
    full_saves: int = 0
    file_size: int = 0
    last_cleanup: float = 0.0

    @property
    def hit_rate(self) -> float:
        """缓存命中率"""
        if self.total_requests == 0:
            return 0.0
        return round((self.hit_count / self.total_requests) * 100, 2)

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "hit_count": self.hit_count,
            "miss_count": self.miss_count,
            "total_requests": self.total_requests,
            "hit_rate": self.hit_rate,
            "cleanup_count": self.cleanup_count,
            "incremental_saves": self.incremental_saves,
            "full_saves": self.full_saves,
            "file_size": self.file_size,
            "last_cleanup": self.last_cleanup
        }


@dataclass
class CacheStats:
    """缓存统计信息"""
    hit_count: int = 0
    miss_count: int = 0
    total_requests: int = 0
    file_size: int = 0
    last_cleanup: float = 0.0
    cleanup_count: int = 0
    incremental_saves: int = 0
    full_saves: int = 0

    @property
    def hit_rate(self) -> float:
        """缓存命中率"""
        if self.total_requests == 0:
            return 0.0
        return (self.hit_count / self.total_requests) * 100

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "hit_count": self.hit_count,
            "miss_count": self.miss_count,
            "total_requests": self.total_requests,
            "hit_rate": round(self.hit_rate, 2),
            "file_size": self.file_size,
            "last_cleanup": self.last_cleanup,
            "cleanup_count": self.cleanup_count,
            "incremental_saves": self.incremental_saves,
            "full_saves": self.full_saves
        }


class BalanceStatus(Enum):
    """额度状态枚举
    
    用于区分账号的额度状态：
    - NORMAL: 正常（剩余额度 > 20%）
    - LOW: 低额度（0 < 剩余额度 <= 20%）
    - EXHAUSTED: 无额度（剩余额度 <= 0）
    """
    NORMAL = "normal"       # 正常（>20%）
    LOW = "low"             # 低额度（0-20%）
    EXHAUSTED = "exhausted" # 无额度（<=0）


@dataclass
class CachedQuota:
    """缓存的额度信息"""
    account_id: str
    usage_limit: float = 0.0          # 总额度
    current_usage: float = 0.0        # 已用额度
    balance: float = 0.0              # 剩余额度
    usage_percent: float = 0.0        # 使用百分比
    balance_status: str = "normal"    # 额度状态: normal, low, exhausted
    is_low_balance: bool = False      # 是否低额度（兼容旧字段）
    is_exhausted: bool = False        # 是否无额度
    is_suspended: bool = False        # 是否被封禁
    subscription_title: str = ""      # 订阅类型
    free_trial_limit: float = 0.0     # 免费试用额度
    free_trial_usage: float = 0.0     # 免费试用已用
    bonus_limit: float = 0.0          # 奖励额度
    bonus_usage: float = 0.0          # 奖励已用
    updated_at: float = 0.0           # 更新时间戳
    error: Optional[str] = None       # 错误信息(如果获取失败)
    
    # 重置和过期时间
    next_reset_date: Optional[str] = None    # 下次重置时间
    free_trial_expiry: Optional[str] = None  # 免费试用过期时间
    bonus_expiries: list = None              # 奖励过期时间列表
    
    def __post_init__(self):
        """初始化后计算额度状态"""
        self._update_balance_status()
    
    def _update_balance_status(self) -> None:
        """更新额度状态"""
        if self.error is not None:
            # 有错误时不更新状态
            return
        
        if self.balance <= 0:
            self.balance_status = BalanceStatus.EXHAUSTED.value
            self.is_exhausted = True
            self.is_low_balance = False
        elif self.usage_limit > 0:
            remaining_percent = (self.balance / self.usage_limit) * 100
            if remaining_percent <= LOW_BALANCE_THRESHOLD * 100:
                self.balance_status = BalanceStatus.LOW.value
                self.is_low_balance = True
                self.is_exhausted = False
            else:
                self.balance_status = BalanceStatus.NORMAL.value
                self.is_low_balance = False
                self.is_exhausted = False
        else:
            self.balance_status = BalanceStatus.NORMAL.value
            self.is_low_balance = False
            self.is_exhausted = False
    
    @classmethod
    def from_usage_info(cls, account_id: str, usage_info: 'UsageInfo') -> 'CachedQuota':
        """从 UsageInfo 创建 CachedQuota"""
        usage_percent = (usage_info.current_usage / usage_info.usage_limit * 100) if usage_info.usage_limit > 0 else 0.0
        quota = cls(
            account_id=account_id,
            usage_limit=usage_info.usage_limit,
            current_usage=usage_info.current_usage,
            balance=usage_info.balance,
            usage_percent=round(usage_percent, 2),
            is_low_balance=usage_info.is_low_balance,
            subscription_title=usage_info.subscription_title,
            free_trial_limit=usage_info.free_trial_limit,
            free_trial_usage=usage_info.free_trial_usage,
            bonus_limit=usage_info.bonus_limit,
            bonus_usage=usage_info.bonus_usage,
            updated_at=time.time(),
            error=None,
            next_reset_date=usage_info.next_reset_date,
            free_trial_expiry=usage_info.free_trial_expiry,
            bonus_expiries=usage_info.bonus_expiries or [],
        )
        # 重新计算状态以确保一致性
        quota._update_balance_status()
        return quota
    
    @classmethod
    def from_error(cls, account_id: str, error: str) -> 'CachedQuota':
        """创建错误状态的缓存"""
        # 检查是否为账号封禁错误
        is_suspended = (
            "temporarily_suspended" in error.lower() or
            "suspended" in error.lower() or
            "accountsuspendedexception" in error.lower()
        )
        
        quota = cls(
            account_id=account_id,
            updated_at=time.time(),
            error=error
        )
        
        # 如果是封禁错误，标记为特殊状态
        if is_suspended:
            quota.is_suspended = True
        
        return quota
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'CachedQuota':
        """从字典创建"""
        quota = cls(
            account_id=data.get("account_id", ""),
            usage_limit=data.get("usage_limit", 0.0),
            current_usage=data.get("current_usage", 0.0),
            balance=data.get("balance", 0.0),
            usage_percent=data.get("usage_percent", 0.0),
            balance_status=data.get("balance_status", "normal"),
            is_low_balance=data.get("is_low_balance", False),
            is_exhausted=data.get("is_exhausted", False),
            is_suspended=data.get("is_suspended", False),
            subscription_title=data.get("subscription_title", ""),
            free_trial_limit=data.get("free_trial_limit", 0.0),
            free_trial_usage=data.get("free_trial_usage", 0.0),
            bonus_limit=data.get("bonus_limit", 0.0),
            bonus_usage=data.get("bonus_usage", 0.0),
            updated_at=data.get("updated_at", 0.0),
            error=data.get("error"),
            next_reset_date=data.get("next_reset_date"),
            free_trial_expiry=data.get("free_trial_expiry"),
            bonus_expiries=data.get("bonus_expiries", []),
        )
        # 重新计算状态以确保一致性
        quota._update_balance_status()
        return quota
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return asdict(self)
    
    def has_error(self) -> bool:
        """是否有错误"""
        return self.error is not None
    
    def is_available(self) -> bool:
        """额度是否可用（未耗尽且无错误）"""
        return not self.is_exhausted and not self.has_error()
    
    def get_balance_status_enum(self) -> BalanceStatus:
        """获取额度状态枚举"""
        try:
            return BalanceStatus(self.balance_status)
        except ValueError:
            return BalanceStatus.NORMAL


class QuotaCache:
    """额度缓存管理器

    提供线程安全的额度缓存操作，支持内存缓存和文件持久化。
    新增功能：
    - 智能清理机制
    - 增量更新优化
    - 性能监控
    - LRU淘汰策略
    """

    def __init__(self, cache_file: Optional[str] = None, max_size: int = DEFAULT_MAX_CACHE_SIZE):
        """
        初始化缓存管理器

        Args:
            cache_file: 缓存文件路径，None 则使用默认路径
            max_size: 最大缓存条目数
        """
        # 使用 OrderedDict 实现 LRU
        self._cache: OrderedDict[str, CachedQuota] = OrderedDict()
        self._lock = Lock()
        self._save_lock = asyncio.Lock()
        self._max_size = max_size

        # 统计信息
        self._stats = CacheStats()

        # 批量更新队列
        self._pending_updates: Dict[str, CachedQuota] = {}
        self._batch_lock = Lock()

        # 设置缓存文件路径
        if cache_file:
            self._cache_file = Path(cache_file)
        else:
            from ..config import DATA_DIR
            self._cache_file = DATA_DIR / "quota_cache.json"

        # 启动时加载缓存
        self.load_from_file()

        # 记录最后清理时间
        self._stats.last_cleanup = time.time()

    def get(self, account_id: str) -> Optional[CachedQuota]:
        """获取账号的缓存额度

        Args:
            account_id: 账号ID

        Returns:
            缓存的额度信息，不存在则返回 None
        """
        with self._lock:
            self._stats.total_requests += 1

            if account_id in self._cache:
                # LRU: 移动到末尾
                quota = self._cache.pop(account_id)
                self._cache[account_id] = quota
                self._stats.hit_count += 1
                return quota
            else:
                self._stats.miss_count += 1
                return None

    def set(self, account_id: str, quota: CachedQuota) -> None:
        """设置账号的额度缓存

        Args:
            account_id: 账号ID
            quota: 额度信息
        """
        with self._lock:
            # 如果已存在，先删除（LRU更新）
            if account_id in self._cache:
                del self._cache[account_id]

            # 添加到末尾
            self._cache[account_id] = quota

            # 检查缓存大小限制
            if len(self._cache) > self._max_size:
                # 删除最旧的条目（LRU淘汰）
                oldest_key = next(iter(self._cache))
                del self._cache[oldest_key]

    def set_batch(self, updates: Dict[str, CachedQuota]) -> None:
        """批量设置缓存

        Args:
            updates: 账号ID到额度信息的映射
        """
        with self._lock:
            for account_id, quota in updates.items():
                # 如果已存在，先删除（LRU更新）
                if account_id in self._cache:
                    del self._cache[account_id]

                # 添加到末尾
                self._cache[account_id] = quota

            # 检查缓存大小限制
            while len(self._cache) > self._max_size:
                oldest_key = next(iter(self._cache))
                del self._cache[oldest_key]

    def add_to_batch(self, account_id: str, quota: CachedQuota) -> None:
        """添加到批量更新队列

        Args:
            account_id: 账号ID
            quota: 额度信息
        """
        with self._batch_lock:
            self._pending_updates[account_id] = quota

            # 如果达到批量大小，执行批量更新
            if len(self._pending_updates) >= DEFAULT_BATCH_SIZE:
                self._flush_batch()

    def _flush_batch(self) -> None:
        """刷新批量更新队列（需要在 _batch_lock 内调用）"""
        if not self._pending_updates:
            return

        updates = dict(self._pending_updates)
        self._pending_updates.clear()

        # 批量设置缓存
        self.set_batch(updates)

    def flush_pending_updates(self) -> None:
        """强制刷新所有待处理的更新"""
        with self._batch_lock:
            self._flush_batch()

    def is_stale(self, account_id: str, max_age_seconds: int = DEFAULT_CACHE_MAX_AGE) -> bool:
        """检查缓存是否过期

        Args:
            account_id: 账号ID
            max_age_seconds: 最大缓存时间（秒）

        Returns:
            True 表示缓存过期或不存在
        """
        with self._lock:
            quota = self._cache.get(account_id)
            if quota is None:
                return True
            return (time.time() - quota.updated_at) > max_age_seconds

    def get_all(self) -> Dict[str, CachedQuota]:
        """获取所有缓存

        Returns:
            所有账号的额度缓存副本
        """
        with self._lock:
            return dict(self._cache)

    def remove(self, account_id: str) -> None:
        """移除账号缓存

        Args:
            account_id: 账号ID
        """
        with self._lock:
            self._cache.pop(account_id, None)

    def remove_batch(self, account_ids: Set[str]) -> int:
        """批量移除账号缓存

        Args:
            account_ids: 要移除的账号ID集合

        Returns:
            实际移除的数量
        """
        removed_count = 0
        with self._lock:
            for account_id in account_ids:
                if self._cache.pop(account_id, None) is not None:
                    removed_count += 1
        return removed_count

    def clear(self) -> None:
        """清空所有缓存"""
        with self._lock:
            self._cache.clear()

        # 清空批量更新队列
        with self._batch_lock:
            self._pending_updates.clear()

    def cleanup_expired(self, max_age_seconds: int = DEFAULT_CACHE_MAX_AGE) -> int:
        """清理过期缓存

        Args:
            max_age_seconds: 最大缓存时间（秒）

        Returns:
            清理的条目数量
        """
        current_time = time.time()
        expired_keys = []

        with self._lock:
            for account_id, quota in self._cache.items():
                if (current_time - quota.updated_at) > max_age_seconds:
                    expired_keys.append(account_id)

            # 移除过期条目
            for key in expired_keys:
                del self._cache[key]

        if expired_keys:
            print(f"[QuotaCache] 清理了 {len(expired_keys)} 个过期缓存条目")

        self._stats.cleanup_count += 1
        self._stats.last_cleanup = current_time
        return len(expired_keys)

    def cleanup_deleted_accounts(self, valid_account_ids: Set[str]) -> int:
        """清理已删除账号的缓存

        Args:
            valid_account_ids: 有效账号ID集合

        Returns:
            清理的条目数量
        """
        deleted_keys = []

        with self._lock:
            for account_id in self._cache.keys():
                if account_id not in valid_account_ids:
                    deleted_keys.append(account_id)

            # 移除已删除账号的缓存
            for key in deleted_keys:
                del self._cache[key]

        if deleted_keys:
            print(f"[QuotaCache] 清理了 {len(deleted_keys)} 个已删除账号的缓存条目")

        return len(deleted_keys)

    def sync_with_config(self, config_accounts: Dict[str, Any]) -> Tuple[int, int]:
        """基于配置文件同步缓存

        Args:
            config_accounts: 配置文件中的账号信息

        Returns:
            (清理的数量, 新增的数量)
        """
        config_account_ids = set(config_accounts.keys())

        # 清理已删除的账号
        cleaned_count = self.cleanup_deleted_accounts(config_account_ids)

        # 统计新增账号
        with self._lock:
            cached_account_ids = set(self._cache.keys())

        new_account_ids = config_account_ids - cached_account_ids
        new_count = len(new_account_ids)

        if new_count > 0:
            print(f"[QuotaCache] 发现 {new_count} 个新账号，将在下次查询时缓存")

        return cleaned_count, new_count

    def auto_cleanup(self) -> Dict[str, int]:
        """自动清理（过期缓存 + 大小限制）

        Returns:
            清理统计信息
        """
        current_time = time.time()

        # 检查是否需要清理
        if (current_time - self._stats.last_cleanup) < DEFAULT_CLEANUP_INTERVAL:
            return {"expired": 0, "lru": 0, "total": 0}

        # 清理过期缓存
        expired_count = self.cleanup_expired()

        # 检查大小限制
        lru_count = 0
        with self._lock:
            while len(self._cache) > self._max_size:
                oldest_key = next(iter(self._cache))
                del self._cache[oldest_key]
                lru_count += 1

        total_count = expired_count + lru_count

        if total_count > 0:
            print(f"[QuotaCache] 自动清理完成: 过期={expired_count}, LRU={lru_count}, 总计={total_count}")

        return {
            "expired": expired_count,
            "lru": lru_count,
            "total": total_count
        }

    def get_cache_health(self) -> Dict[str, Any]:
        """获取缓存健康状态

        Returns:
            健康状态信息
        """
        current_time = time.time()

        with self._lock:
            total_entries = len(self._cache)
            expired_count = 0
            error_count = 0

            for quota in self._cache.values():
                if (current_time - quota.updated_at) > DEFAULT_CACHE_MAX_AGE:
                    expired_count += 1
                if quota.has_error():
                    error_count += 1

        # 计算文件大小
        file_size = 0
        if self._cache_file.exists():
            file_size = self._cache_file.stat().st_size
            self._stats.file_size = file_size

        return {
            "total_entries": total_entries,
            "max_size": self._max_size,
            "usage_percent": round((total_entries / self._max_size) * 100, 2),
            "expired_count": expired_count,
            "error_count": error_count,
            "file_size": file_size,
            "file_size_mb": round(file_size / (1024 * 1024), 2),
            "last_cleanup_ago": round(current_time - self._stats.last_cleanup),
            "needs_cleanup": expired_count > 0 or total_entries > self._max_size,
            "stats": self._stats.to_dict()
        }

    def load_from_file(self) -> bool:
        """从文件加载缓存

        Returns:
            是否加载成功
        """
        if not self._cache_file.exists():
            return False

        try:
            with open(self._cache_file, 'r', encoding='utf-8') as f:
                data = json.load(f)

            # 验证版本
            version = data.get("version", "1.0")
            accounts_data = data.get("accounts", {})

            # 加载统计信息
            stats_data = data.get("stats", {})
            if stats_data:
                self._stats.file_size = stats_data.get("file_size", 0)
                self._stats.last_cleanup = stats_data.get("last_cleanup", time.time())
                self._stats.cleanup_count = stats_data.get("cleanup_count", 0)
                self._stats.incremental_saves = stats_data.get("incremental_saves", 0)
                self._stats.full_saves = stats_data.get("full_saves", 0)

            with self._lock:
                self._cache.clear()
                for account_id, quota_data in accounts_data.items():
                    quota_data["account_id"] = account_id
                    self._cache[account_id] = CachedQuota.from_dict(quota_data)

            print(f"[QuotaCache] 从文件加载 {len(self._cache)} 个账号的额度缓存")
            return True

        except json.JSONDecodeError as e:
            print(f"[QuotaCache] 缓存文件格式错误: {e}")
            return False
        except Exception as e:
            print(f"[QuotaCache] 加载缓存失败: {e}")
            return False

    def _should_use_incremental_save(self) -> bool:
        """判断是否应该使用增量保存"""
        if not self._cache_file.exists():
            return False

        file_size = self._cache_file.stat().st_size
        return file_size > MIN_FILE_SIZE_FOR_INCREMENTAL

    def save_to_file(self, force_full: bool = False) -> bool:
        """保存缓存到文件（同步版本）

        Args:
            force_full: 强制完整保存

        Returns:
            是否保存成功
        """
        try:
            # 确保目录存在
            self._cache_file.parent.mkdir(parents=True, exist_ok=True)

            # 刷新待处理的更新
            self.flush_pending_updates()

            # 决定保存方式
            use_incremental = not force_full and self._should_use_incremental_save()

            if use_incremental:
                success = self._save_incremental()
                if success:
                    self._stats.incremental_saves += 1
                    return True
                else:
                    # 增量保存失败，回退到完整保存
                    print("[QuotaCache] 增量保存失败，回退到完整保存")

            # 完整保存
            success = self._save_full()
            if success:
                self._stats.full_saves += 1
            return success

        except Exception as e:
            print(f"[QuotaCache] 保存缓存失败: {e}")
            return False

    def _save_full(self) -> bool:
        """完整保存到文件"""
        try:
            with self._lock:
                accounts_data = {}
                for account_id, quota in self._cache.items():
                    quota_dict = quota.to_dict()
                    quota_dict.pop("account_id", None)  # 避免重复存储
                    accounts_data[account_id] = quota_dict

            # 更新统计信息
            current_time = time.time()
            self._stats.file_size = 0  # 将在保存后更新

            data = {
                "version": "1.0",
                "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(current_time)),
                "accounts": accounts_data,
                "stats": {
                    "file_size": 0,  # 将在保存后更新
                    "last_cleanup": self._stats.last_cleanup,
                    "cleanup_count": self._stats.cleanup_count,
                    "incremental_saves": self._stats.incremental_saves,
                    "full_saves": self._stats.full_saves + 1,  # 预增加
                    "saved_at": current_time
                }
            }

            # 写入临时文件后重命名，确保原子性
            temp_file = self._cache_file.with_suffix('.tmp')
            with open(temp_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)

            temp_file.replace(self._cache_file)

            # 更新文件大小统计
            self._stats.file_size = self._cache_file.stat().st_size

            return True

        except Exception as e:
            print(f"[QuotaCache] 完整保存失败: {e}")
            return False

    def _save_incremental(self) -> bool:
        """增量保存（仅更新变化的部分）"""
        try:
            # 读取现有文件
            with open(self._cache_file, 'r', encoding='utf-8') as f:
                existing_data = json.load(f)

            existing_accounts = existing_data.get("accounts", {})

            # 检查哪些账号需要更新
            updates_needed = False
            with self._lock:
                for account_id, quota in self._cache.items():
                    quota_dict = quota.to_dict()
                    quota_dict.pop("account_id", None)

                    # 比较是否有变化
                    if (account_id not in existing_accounts or
                        existing_accounts[account_id] != quota_dict):
                        existing_accounts[account_id] = quota_dict
                        updates_needed = True

                # 检查是否有账号被删除
                cached_ids = set(self._cache.keys())
                existing_ids = set(existing_accounts.keys())
                deleted_ids = existing_ids - cached_ids

                for deleted_id in deleted_ids:
                    del existing_accounts[deleted_id]
                    updates_needed = True

            if not updates_needed:
                return True  # 无需更新

            # 更新数据
            current_time = time.time()
            existing_data["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(current_time))
            existing_data["accounts"] = existing_accounts

            # 更新统计信息
            stats = existing_data.get("stats", {})
            stats.update({
                "last_cleanup": self._stats.last_cleanup,
                "cleanup_count": self._stats.cleanup_count,
                "incremental_saves": self._stats.incremental_saves + 1,
                "full_saves": self._stats.full_saves,
                "saved_at": current_time
            })
            existing_data["stats"] = stats

            # 原子性写入
            temp_file = self._cache_file.with_suffix('.tmp')
            with open(temp_file, 'w', encoding='utf-8') as f:
                json.dump(existing_data, f, indent=2, ensure_ascii=False)

            temp_file.replace(self._cache_file)

            # 更新文件大小统计
            self._stats.file_size = self._cache_file.stat().st_size

            return True

        except Exception as e:
            print(f"[QuotaCache] 增量保存失败: {e}")
            return False

    async def save_to_file_async(self, force_full: bool = False) -> bool:
        """异步保存缓存到文件

        Args:
            force_full: 强制完整保存

        Returns:
            是否保存成功
        """
        async with self._save_lock:
            # 在线程池中执行同步保存
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, self.save_to_file, force_full)

    def get_summary(self) -> Dict[str, Any]:
        """获取缓存汇总信息

        Returns:
            汇总统计信息
        """
        with self._lock:
            total_balance = 0.0
            total_usage = 0.0
            total_limit = 0.0
            error_count = 0
            stale_count = 0

            current_time = time.time()

            for quota in self._cache.values():
                if quota.has_error():
                    error_count += 1
                else:
                    total_balance += quota.balance
                    total_usage += quota.current_usage
                    total_limit += quota.usage_limit

                if (current_time - quota.updated_at) > DEFAULT_CACHE_MAX_AGE:
                    stale_count += 1

            return {
                "total_accounts": len(self._cache),
                "total_balance": round(total_balance, 2),
                "total_usage": round(total_usage, 2),
                "total_limit": round(total_limit, 2),
                "error_count": error_count,
                "stale_count": stale_count,
                "max_size": self._max_size,
                "usage_percent": round((len(self._cache) / self._max_size) * 100, 2),
                "stats": self._stats.to_dict()
            }

    def get_stats(self) -> CacheStats:
        """获取缓存统计信息"""
        return self._stats


# 全局缓存实例
_quota_cache: Optional[QuotaCache] = None


def get_quota_cache() -> QuotaCache:
    """获取全局缓存实例"""
    global _quota_cache
    if _quota_cache is None:
        _quota_cache = QuotaCache()
    return _quota_cache


def cleanup_cache_for_accounts(valid_account_ids: Set[str]) -> Dict[str, int]:
    """清理已删除账号的缓存（全局函数）

    Args:
        valid_account_ids: 有效账号ID集合

    Returns:
        清理统计信息
    """
    cache = get_quota_cache()

    # 清理已删除账号
    deleted_count = cache.cleanup_deleted_accounts(valid_account_ids)

    # 执行自动清理
    auto_cleanup_stats = cache.auto_cleanup()

    # 保存更改
    cache.save_to_file()

    return {
        "deleted_accounts": deleted_count,
        "expired_entries": auto_cleanup_stats["expired"],
        "lru_evicted": auto_cleanup_stats["lru"],
        "total_cleaned": deleted_count + auto_cleanup_stats["total"]
    }


def sync_cache_with_config(config_file_path: str) -> Dict[str, Any]:
    """基于配置文件同步缓存

    Args:
        config_file_path: 配置文件路径

    Returns:
        同步结果统计
    """
    try:
        # 读取配置文件
        config_path = Path(config_file_path)
        if not config_path.exists():
            return {"error": "配置文件不存在", "path": config_file_path}

        with open(config_path, 'r', encoding='utf-8') as f:
            config_data = json.load(f)

        # 提取账号信息
        accounts = config_data.get("accounts", {})
        if not accounts:
            return {"error": "配置文件中未找到账号信息"}

        # 同步缓存
        cache = get_quota_cache()
        cleaned_count, new_count = cache.sync_with_config(accounts)

        # 保存更改
        if cleaned_count > 0:
            cache.save_to_file()

        return {
            "success": True,
            "total_config_accounts": len(accounts),
            "cleaned_accounts": cleaned_count,
            "new_accounts": new_count,
            "cache_health": cache.get_cache_health()
        }

    except Exception as e:
        return {"error": f"同步失败: {e}"}


def get_cache_performance_report() -> Dict[str, Any]:
    """获取缓存性能报告

    Returns:
        性能报告
    """
    cache = get_quota_cache()
    health = cache.get_cache_health()
    stats = cache.get_stats()
    summary = cache.get_summary()

    # 计算性能指标
    avg_file_size_per_account = 0
    if summary["total_accounts"] > 0:
        avg_file_size_per_account = health["file_size"] / summary["total_accounts"]

    # 评估缓存效率
    efficiency_score = 0
    if stats.hit_rate >= 80:
        efficiency_score += 40
    elif stats.hit_rate >= 60:
        efficiency_score += 30
    elif stats.hit_rate >= 40:
        efficiency_score += 20

    if health["expired_count"] == 0:
        efficiency_score += 20
    elif health["expired_count"] <= 5:
        efficiency_score += 15

    if health["usage_percent"] <= 80:
        efficiency_score += 20
    elif health["usage_percent"] <= 90:
        efficiency_score += 15

    if health["file_size_mb"] <= 1:
        efficiency_score += 20
    elif health["file_size_mb"] <= 5:
        efficiency_score += 15
    elif health["file_size_mb"] <= 10:
        efficiency_score += 10

    # 生成建议
    recommendations = []
    if stats.hit_rate < 60:
        recommendations.append("缓存命中率较低，考虑增加缓存时间或检查查询模式")
    if health["expired_count"] > 10:
        recommendations.append("过期条目较多，建议执行清理操作")
    if health["usage_percent"] > 90:
        recommendations.append("缓存使用率过高，考虑增加最大缓存大小")
    if health["file_size_mb"] > 10:
        recommendations.append("缓存文件较大，建议启用增量保存")
    if stats.incremental_saves == 0 and stats.full_saves > 10:
        recommendations.append("建议启用增量保存以提高性能")

    return {
        "performance": {
            "hit_rate": stats.hit_rate,
            "total_requests": stats.total_requests,
            "avg_file_size_per_account": round(avg_file_size_per_account, 2),
            "efficiency_score": efficiency_score,
            "efficiency_grade": (
                "优秀" if efficiency_score >= 80 else
                "良好" if efficiency_score >= 60 else
                "一般" if efficiency_score >= 40 else
                "需要优化"
            )
        },
        "storage": {
            "file_size_mb": health["file_size_mb"],
            "incremental_saves": stats.incremental_saves,
            "full_saves": stats.full_saves,
            "save_ratio": round(stats.incremental_saves / max(1, stats.full_saves), 2)
        },
        "maintenance": {
            "cleanup_count": stats.cleanup_count,
            "last_cleanup_ago": health["last_cleanup_ago"],
            "needs_cleanup": health["needs_cleanup"]
        },
        "recommendations": recommendations,
        "raw_data": {
            "health": health,
            "stats": stats.to_dict(),
            "summary": summary
        }
    }


# 向后兼容性：保持原有函数签名
def create_quota_cache(cache_file: Optional[str] = None) -> QuotaCache:
    """创建新的缓存实例（向后兼容）"""
    return QuotaCache(cache_file)
