"""账号选择器模块

实现基于剩余额度的智能账号选择策略，支持优先账号配置。
"""
import json
import random
import time
from enum import Enum
from pathlib import Path
from typing import Optional, List, Set, TYPE_CHECKING
from threading import Lock

if TYPE_CHECKING:
    from .account import Account
    from .quota_cache import QuotaCache


class SelectionStrategy(Enum):
    """选择策略"""
    LOWEST_BALANCE = "lowest_balance"    # 剩余额度最少优先
    ROUND_ROBIN = "round_robin"          # 轮询
    LEAST_REQUESTS = "least_requests"    # 请求最少优先
    RANDOM = "random"                    # 随机选择（分散压力）


class AccountSelector:
    """账号选择器
    
    根据配置的策略选择最合适的账号，支持优先账号配置。
    """
    
    def __init__(self, quota_cache: 'QuotaCache', priority_file: Optional[str] = None):
        """
        初始化账号选择器
        
        Args:
            quota_cache: 额度缓存实例
            priority_file: 优先账号配置文件路径
        """
        self.quota_cache = quota_cache
        self._priority_accounts: List[str] = []
        # 默认使用随机策略，避免单账号 RPM 过高导致封禁风险
        self._strategy = SelectionStrategy.RANDOM
        self._lock = Lock()
        self._round_robin_index = 0
        self._last_random_account_id: Optional[str] = None
        
        # 设置优先账号配置文件路径
        if priority_file:
            self._priority_file = Path(priority_file)
        else:
            from ..config import DATA_DIR
            self._priority_file = DATA_DIR / "priority.json"
        
        # 加载优先账号配置
        self._load_priority_config()
    
    @property
    def strategy(self) -> SelectionStrategy:
        """获取当前选择策略"""
        return self._strategy
    
    @strategy.setter
    def strategy(self, value: SelectionStrategy):
        """设置选择策略"""
        self._strategy = value
        self._save_priority_config()
    
    def select(self, 
               available_accounts: List['Account'],
               session_id: Optional[str] = None) -> Optional['Account']:
        """选择最合适的账号
        
        Args:
            available_accounts: 可用账号列表
            session_id: 会话ID（用于会话粘性，暂未实现）
            
        Returns:
            选中的账号，如果没有可用账号则返回 None
        """
        if not available_accounts:
            return None
        
        with self._lock:
            # 1. 首先检查优先账号
            if self._priority_accounts:
                for priority_id in self._priority_accounts:
                    for account in available_accounts:
                        if account.id == priority_id and account.is_available():
                            return account
            
            # 2. 根据策略选择
            if self._strategy == SelectionStrategy.LOWEST_BALANCE:
                return self._select_lowest_balance(available_accounts)
            elif self._strategy == SelectionStrategy.ROUND_ROBIN:
                return self._select_round_robin(available_accounts)
            elif self._strategy == SelectionStrategy.LEAST_REQUESTS:
                return self._select_least_requests(available_accounts)
            elif self._strategy == SelectionStrategy.RANDOM:
                return self._select_random(available_accounts)
            
            # 默认返回第一个可用账号
            return available_accounts[0] if available_accounts else None
    
    def _select_lowest_balance(self, accounts: List['Account']) -> Optional['Account']:
        """选择剩余额度最少的账号"""
        available = [a for a in accounts if a.is_available()]
        if not available:
            return None
        
        def get_balance_and_requests(account: 'Account') -> tuple:
            """获取账号的余额和请求数，用于排序"""
            quota = self.quota_cache.get(account.id)
            balance = quota.balance if quota and not quota.has_error() else float('inf')
            return (balance, account.request_count)
        
        # 按余额升序，余额相同时按请求数升序
        return min(available, key=get_balance_and_requests)
    
    def _select_round_robin(self, accounts: List['Account']) -> Optional['Account']:
        """轮询选择账号"""
        available = [a for a in accounts if a.is_available()]
        if not available:
            return None
        
        self._round_robin_index = self._round_robin_index % len(available)
        account = available[self._round_robin_index]
        self._round_robin_index += 1
        return account
    
    def _select_least_requests(self, accounts: List['Account']) -> Optional['Account']:
        """选择请求数最少的账号"""
        available = [a for a in accounts if a.is_available()]
        if not available:
            return None
        return min(available, key=lambda a: a.request_count)

    def _select_random(self, accounts: List['Account']) -> Optional['Account']:
        """随机选择账号（分散请求压力）"""
        available = [a for a in accounts if a.is_available()]
        if not available:
            return None

        # 尽量避免连续两次命中同一账号（在有多个可用账号时）
        if self._last_random_account_id and len(available) > 1:
            candidates = [a for a in available if a.id != self._last_random_account_id]
            if candidates:
                selected = random.choice(candidates)
            else:
                selected = random.choice(available)
        else:
            selected = random.choice(available)

        self._last_random_account_id = selected.id
        return selected
    
    def set_priority_accounts(self, account_ids: List[str], 
                              valid_account_ids: Optional[Set[str]] = None) -> tuple:
        """设置优先账号列表（按顺序）
        
        Args:
            account_ids: 优先账号ID列表（按顺序）
            valid_account_ids: 有效账号ID集合（用于验证）
            
        Returns:
            (success, message)
        """
        with self._lock:
            if not account_ids:
                self._priority_accounts = []
                self._strategy = SelectionStrategy.RANDOM
                self._save_priority_config()
                return True, "已清除优先账号"
            
            # 去重（保持顺序）
            unique_ids: List[str] = []
            seen: Set[str] = set()
            for aid in account_ids:
                if aid in seen:
                    continue
                seen.add(aid)
                unique_ids.append(aid)
            
            # 验证账号是否存在
            if valid_account_ids:
                for aid in unique_ids:
                    if aid not in valid_account_ids:
                        return False, f"账号不存在: {aid}"
            
            self._priority_accounts = unique_ids
            self._save_priority_config()
            if len(unique_ids) == 1:
                return True, f"已设置优先账号: {unique_ids[0]}"
            return True, f"已设置优先账号: {', '.join(unique_ids)}"
    
    def set_priority_account(self, account_id: Optional[str],
                             valid_account_ids: Optional[Set[str]] = None) -> tuple:
        """设置优先账号（单个）
        
        Args:
            account_id: 账号ID，None 表示清除
            valid_account_ids: 有效账号ID集合（用于验证）
            
        Returns:
            (success, message)
        """
        if account_id is None:
            return self.set_priority_accounts([], valid_account_ids)
        return self.set_priority_accounts([account_id], valid_account_ids)
    
    def add_priority_account(self, account_id: str, 
                             position: int = -1,
                             valid_account_ids: Optional[Set[str]] = None) -> tuple:
        """添加优先账号（可指定插入位置）
        
        Args:
            account_id: 账号ID
            position: 插入位置（0-based），-1 表示追加到末尾
            valid_account_ids: 有效账号ID集合（用于验证）
            
        Returns:
            (success, message)
        """
        with self._lock:
            if valid_account_ids and account_id not in valid_account_ids:
                return False, f"账号不存在: {account_id}"

            if account_id in self._priority_accounts:
                self._priority_accounts.remove(account_id)

            if position is None or position < 0 or position >= len(self._priority_accounts):
                self._priority_accounts.append(account_id)
            else:
                self._priority_accounts.insert(position, account_id)

            self._save_priority_config()
            return True, f"已添加优先账号: {account_id}"
    
    def remove_priority_account(self, account_id: str = None) -> tuple:
        """移除优先账号
        
        Args:
            account_id: 账号ID（可选，不传则清除所有）
            
        Returns:
            (success, message)
        """
        with self._lock:
            if not self._priority_accounts:
                return False, "没有设置优先账号"
            
            if account_id:
                if account_id not in self._priority_accounts:
                    return False, f"账号 {account_id} 不是优先账号"

                self._priority_accounts.remove(account_id)
                if not self._priority_accounts:
                    self._strategy = SelectionStrategy.RANDOM
                self._save_priority_config()
                return True, f"已移除优先账号: {account_id}"

            self._priority_accounts = []
            self._strategy = SelectionStrategy.RANDOM
            self._save_priority_config()
            return True, "已清除优先账号"

    def reorder_priority(self, account_ids: List[str]) -> tuple:
        """重新排序优先账号列表

        Args:
            account_ids: 新的优先账号顺序（必须与当前优先账号集合一致）

        Returns:
            (success, message)
        """
        with self._lock:
            if not self._priority_accounts:
                return False, "没有设置优先账号"

            if not account_ids:
                return False, "账号列表不能为空"

            if len(account_ids) != len(self._priority_accounts):
                return False, "账号数量不匹配"

            if len(set(account_ids)) != len(account_ids):
                return False, "账号列表包含重复项"

            if set(account_ids) != set(self._priority_accounts):
                return False, "账号列表与当前优先账号不匹配"

            self._priority_accounts = list(account_ids)
            self._save_priority_config()
            return True, "已更新优先账号顺序"
    
    def get_priority_account(self) -> Optional[str]:
        """获取优先账号（单个）"""
        with self._lock:
            return self._priority_accounts[0] if self._priority_accounts else None
    
    def get_priority_accounts(self) -> List[str]:
        """获取优先账号列表"""
        with self._lock:
            return list(self._priority_accounts)
    
    def is_priority_account(self, account_id: str) -> bool:
        """检查账号是否为优先账号"""
        with self._lock:
            return account_id in self._priority_accounts
    
    def get_priority_order(self, account_id: str) -> Optional[int]:
        """获取账号的优先级顺序（从1开始）"""
        with self._lock:
            if account_id in self._priority_accounts:
                return self._priority_accounts.index(account_id) + 1
            return None
    
    def _load_priority_config(self) -> bool:
        """从文件加载优先账号配置"""
        if not self._priority_file.exists():
            return False
        
        try:
            with open(self._priority_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            self._priority_accounts = data.get("priority_accounts", [])
            strategy_str = data.get("strategy", SelectionStrategy.RANDOM.value)
            try:
                self._strategy = SelectionStrategy(strategy_str)
            except ValueError:
                self._strategy = SelectionStrategy.RANDOM

            # 兼容旧版本：历史默认策略为 lowest_balance，但无优先账号时更需要分散压力
            if not self._priority_accounts and self._strategy == SelectionStrategy.LOWEST_BALANCE:
                self._strategy = SelectionStrategy.RANDOM
                self._save_priority_config()
            
            print(f"[AccountSelector] 加载优先账号配置: {len(self._priority_accounts)} 个优先账号")
            return True
            
        except Exception as e:
            print(f"[AccountSelector] 加载优先账号配置失败: {e}")
            return False
    
    def _save_priority_config(self) -> bool:
        """保存优先账号配置到文件"""
        try:
            self._priority_file.parent.mkdir(parents=True, exist_ok=True)
            
            data = {
                "version": "1.0",
                "priority_accounts": self._priority_accounts,
                "strategy": self._strategy.value
            }
            
            temp_file = self._priority_file.with_suffix('.tmp')
            with open(temp_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            temp_file.replace(self._priority_file)
            
            return True
            
        except Exception as e:
            print(f"[AccountSelector] 保存优先账号配置失败: {e}")
            return False
    
    def get_status(self) -> dict:
        """获取选择器状态"""
        with self._lock:
            return {
                "strategy": self._strategy.value,
                "priority_accounts": list(self._priority_accounts),
                "priority_count": len(self._priority_accounts)
            }


# 全局选择器实例
_account_selector: Optional[AccountSelector] = None


def get_account_selector(quota_cache: Optional['QuotaCache'] = None) -> AccountSelector:
    """获取全局选择器实例"""
    global _account_selector
    if _account_selector is None:
        if quota_cache is None:
            from .quota_cache import get_quota_cache
            quota_cache = get_quota_cache()
        _account_selector = AccountSelector(quota_cache)
    return _account_selector
