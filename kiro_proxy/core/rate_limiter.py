"""请求限速器 - 降低账号封禁风险

通过限制请求频率来降低被检测为异常活动的风险：
- 每账号请求间隔
- 全局请求限制
- 突发请求检测

注意：429 冷却时间已改为自动管理（固定5分钟），不再需要手动配置
"""
import time
from dataclasses import dataclass, field
from typing import Dict, Optional
from collections import deque


@dataclass
class RateLimitConfig:
    """限速配置"""
    # 每账号最小请求间隔（秒）
    min_request_interval: float = 0.5

    # 每账号每分钟最大请求数
    max_requests_per_minute: int = 60

    # 全局每分钟最大请求数
    global_max_requests_per_minute: int = 120

    # 是否启用限速
    enabled: bool = False


@dataclass
class AccountRateState:
    """账号限速状态"""
    last_request_time: float = 0
    request_times: deque = field(default_factory=lambda: deque(maxlen=100))

    def get_requests_in_window(self, window_seconds: int = 60) -> int:
        """获取时间窗口内的请求数"""
        now = time.time()
        cutoff = now - window_seconds
        return sum(1 for t in self.request_times if t > cutoff)


class RateLimiter:
    """请求限速器"""

    def __init__(self, config: RateLimitConfig = None):
        self.config = config or RateLimitConfig()
        self._account_states: Dict[str, AccountRateState] = {}
        self._global_requests: deque = deque(maxlen=1000)

    def _get_account_state(self, account_id: str) -> AccountRateState:
        """获取账号状态"""
        if account_id not in self._account_states:
            self._account_states[account_id] = AccountRateState()
        return self._account_states[account_id]

    def can_request(self, account_id: str) -> tuple:
        """检查是否可以发送请求

        Returns:
            (can_request, wait_seconds, reason)
        """
        if not self.config.enabled:
            return True, 0, None

        now = time.time()
        state = self._get_account_state(account_id)

        # 检查最小请求间隔
        time_since_last = now - state.last_request_time
        if time_since_last < self.config.min_request_interval:
            wait = self.config.min_request_interval - time_since_last
            return False, wait, f"请求过快，请等待 {wait:.1f} 秒"

        # 检查每账号每分钟限制
        account_rpm = state.get_requests_in_window(60)
        if account_rpm >= self.config.max_requests_per_minute:
            return False, 2, f"账号请求过于频繁 ({account_rpm}/分钟)"

        # 检查全局每分钟限制
        global_rpm = sum(1 for t in self._global_requests if t > now - 60)
        if global_rpm >= self.config.global_max_requests_per_minute:
            return False, 1, f"全局请求过于频繁 ({global_rpm}/分钟)"

        return True, 0, None

    def record_request(self, account_id: str):
        """记录请求"""
        now = time.time()
        state = self._get_account_state(account_id)
        state.last_request_time = now
        state.request_times.append(now)
        self._global_requests.append(now)

    def get_stats(self) -> dict:
        """获取统计信息"""
        now = time.time()
        return {
            "enabled": self.config.enabled,
            "global_rpm": sum(1 for t in self._global_requests if t > now - 60),
            "accounts": {
                aid: {
                    "rpm": state.get_requests_in_window(60),
                    "last_request": now - state.last_request_time if state.last_request_time else None
                }
                for aid, state in self._account_states.items()
            }
        }

    def update_config(self, **kwargs):
        """更新配置"""
        for key, value in kwargs.items():
            if hasattr(self.config, key):
                setattr(self.config, key, value)


# 全局实例
rate_limiter = RateLimiter()


def get_rate_limiter() -> RateLimiter:
    """获取限速器实例"""
    return rate_limiter
