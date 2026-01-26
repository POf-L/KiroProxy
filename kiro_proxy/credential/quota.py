"""配额管理"""
import time
from dataclasses import dataclass
from typing import Dict, Optional


@dataclass
class QuotaRecord:
    """配额超限记录"""
    credential_id: str
    exceeded_at: float
    cooldown_until: float
    reason: str


class QuotaManager:
    """配额管理器

    管理凭证的配额超限状态：
    - 仅在收到 429 错误时触发冷却
    - 自动管理冷却时间：固定 5 分钟（300秒）
    - 自动清理过期的冷却状态
    """

    # 固定冷却时间（秒）- 429 错误自动冷却 5 分钟
    COOLDOWN_SECONDS = 300

    def __init__(self):
        self.exceeded_records: Dict[str, QuotaRecord] = {}

    def is_429_error(self, status_code: Optional[int]) -> bool:
        """检查是否为 429 错误（仅 429 触发冷却）"""
        return status_code == 429

    def is_quota_exceeded_error(self, status_code: Optional[int], error_message: str) -> bool:
        """检查是否为配额超限错误（仅用于判断是否切换账号，不触发冷却）"""
        # 仅 429 才算配额超限
        return status_code == 429

    def mark_exceeded(self, credential_id: str, reason: str) -> QuotaRecord:
        """标记凭证为配额超限（仅 429 时调用）

        自动管理冷却时间：固定 5 分钟（300秒）
        """
        now = time.time()

        record = QuotaRecord(
            credential_id=credential_id,
            exceeded_at=now,
            cooldown_until=now + self.COOLDOWN_SECONDS,
            reason=reason
        )
        self.exceeded_records[credential_id] = record

        print(f"[QuotaManager] 账号 {credential_id} 遇到 429 错误，自动冷却 {self.COOLDOWN_SECONDS} 秒（5分钟）")
        return record

    def is_available(self, credential_id: str) -> bool:
        """检查凭证是否可用"""
        record = self.exceeded_records.get(credential_id)
        if not record:
            return True

        if time.time() >= record.cooldown_until:
            del self.exceeded_records[credential_id]
            return True

        return False

    def get_cooldown_remaining(self, credential_id: str) -> Optional[int]:
        """获取剩余冷却时间（秒）"""
        record = self.exceeded_records.get(credential_id)
        if not record:
            return None

        remaining = record.cooldown_until - time.time()
        if remaining <= 0:
            del self.exceeded_records[credential_id]
            return None

        return int(remaining)

    def cleanup_expired(self) -> int:
        """清理过期的冷却记录"""
        now = time.time()
        expired = [k for k, v in self.exceeded_records.items() if now >= v.cooldown_until]
        for k in expired:
            del self.exceeded_records[k]
        return len(expired)

    def restore(self, credential_id: str) -> bool:
        """手动恢复凭证"""
        if credential_id in self.exceeded_records:
            del self.exceeded_records[credential_id]
            return True
        return False


# 全局实例 - 429 自动冷却 5 分钟
quota_manager = QuotaManager()
