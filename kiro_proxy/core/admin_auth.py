"""管理员认证系统"""
import os
import secrets
import hashlib
import hmac
import time
import logging
from typing import Optional, Dict, Any
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

class AdminAuth:
    """管理员认证管理器"""

    def __init__(self):
        self.secret_key = self._get_or_create_secret_key()
        self.session_timeout = 24 * 60 * 60  # 24小时
        self._sessions: Dict[str, Dict[str, Any]] = {}

    def _get_or_create_secret_key(self) -> str:
        """获取或创建密钥

        注意：此密钥目前仅用于未来扩展（当前会话存储在内存中，不依赖密钥持久化）。
        为避免在 ASGI 事件循环中调用 asyncio.run（会导致崩溃），这里不再从持久化层读取/写入。
        如需固定密钥，请设置环境变量 ADMIN_SECRET_KEY。
        """
        env_key = os.getenv("ADMIN_SECRET_KEY")
        if env_key:
            return env_key

        logger.info("生成新的管理员密钥")
        return secrets.token_urlsafe(32)

    def is_auth_required(self) -> bool:
        """检查是否需要认证（仅当设置了 ADMIN_PASSWORD 环境变量时才需要）"""
        return bool(os.getenv("ADMIN_PASSWORD"))

    def get_admin_password(self) -> Optional[str]:
        """获取管理员密码，未设置环境变量时返回 None"""
        env_password = os.getenv("ADMIN_PASSWORD")
        if env_password:
            return env_password
        return None

    def hash_password(self, password: str) -> str:
        """哈希密码"""
        salt = secrets.token_bytes(32)
        pwdhash = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, 100000)
        return salt.hex() + pwdhash.hex()

    def verify_password(self, password: str, hashed: str) -> bool:
        """验证密码"""
        try:
            salt = bytes.fromhex(hashed[:64])
            stored_hash = bytes.fromhex(hashed[64:])
            pwdhash = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, 100000)
            return hmac.compare_digest(stored_hash, pwdhash)
        except Exception:
            return False

    def authenticate(self, password: str) -> bool:
        """验证管理员密码"""
        admin_password = self.get_admin_password()
        if admin_password is None:
            return True
        return hmac.compare_digest(password, admin_password)

    def create_session(self, user_id: str = "admin") -> str:
        """创建会话"""
        session_id = secrets.token_urlsafe(32)
        expires_at = time.time() + self.session_timeout

        self._sessions[session_id] = {
            "user_id": user_id,
            "created_at": time.time(),
            "expires_at": expires_at,
            "last_activity": time.time()
        }

        # 清理过期会话
        self._cleanup_expired_sessions()

        logger.info(f"创建管理员会话: {session_id[:8]}...")
        return session_id

    def validate_session(self, session_id: str) -> bool:
        """验证会话"""
        if not session_id or session_id not in self._sessions:
            return False

        session = self._sessions[session_id]
        current_time = time.time()

        # 检查是否过期
        if current_time > session["expires_at"]:
            del self._sessions[session_id]
            return False

        # 更新最后活动时间
        session["last_activity"] = current_time
        return True

    def revoke_session(self, session_id: str) -> bool:
        """撤销会话"""
        if session_id in self._sessions:
            del self._sessions[session_id]
            logger.info(f"撤销管理员会话: {session_id[:8]}...")
            return True
        return False

    def get_session_info(self, session_id: str) -> Optional[Dict[str, Any]]:
        """获取会话信息"""
        if session_id in self._sessions:
            session = self._sessions[session_id].copy()
            session["created_at"] = datetime.fromtimestamp(session["created_at"]).isoformat()
            session["expires_at"] = datetime.fromtimestamp(session["expires_at"]).isoformat()
            session["last_activity"] = datetime.fromtimestamp(session["last_activity"]).isoformat()
            return session
        return None

    def _cleanup_expired_sessions(self):
        """清理过期会话"""
        current_time = time.time()
        expired_sessions = [
            sid for sid, session in self._sessions.items()
            if current_time > session["expires_at"]
        ]

        for sid in expired_sessions:
            del self._sessions[sid]

        if expired_sessions:
            logger.info(f"清理了 {len(expired_sessions)} 个过期会话")

    def get_active_sessions(self) -> Dict[str, Dict[str, Any]]:
        """获取所有活跃会话"""
        self._cleanup_expired_sessions()
        return {
            sid: self.get_session_info(sid)
            for sid in self._sessions.keys()
        }


# 全局认证实例
_auth_instance: Optional[AdminAuth] = None

def get_admin_auth() -> AdminAuth:
    """获取管理员认证实例（单例模式）"""
    global _auth_instance
    if _auth_instance is None:
        _auth_instance = AdminAuth()
    return _auth_instance


# 便捷函数
def authenticate_admin(password: str) -> bool:
    """验证管理员密码"""
    return get_admin_auth().authenticate(password)

def create_admin_session() -> str:
    """创建管理员会话"""
    return get_admin_auth().create_session()

def validate_admin_session(session_id: str) -> bool:
    """验证管理员会话"""
    return get_admin_auth().validate_session(session_id)

def revoke_admin_session(session_id: str) -> bool:
    """撤销管理员会话"""
    return get_admin_auth().revoke_session(session_id)

def get_admin_password() -> Optional[str]:
    """获取管理员密码"""
    return get_admin_auth().get_admin_password()


def is_auth_required() -> bool:
    """检查是否需要认证"""
    return get_admin_auth().is_auth_required()
