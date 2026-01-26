"""管理员认证中间件和装饰器"""
import os
import hmac
from functools import wraps
from typing import Optional
from fastapi import HTTPException, Request, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from .admin_auth import get_admin_auth, validate_admin_session, is_auth_required

security = HTTPBearer(auto_error=False)


def get_api_key() -> Optional[str]:
    """获取 API 密钥环境变量"""
    return os.getenv("API_KEY")


def is_api_auth_required() -> bool:
    """检查是否需要 API 认证"""
    return bool(get_api_key())


def verify_api_key(provided_key: str) -> bool:
    """验证 API 密钥"""
    expected_key = get_api_key()
    if not expected_key:
        return True
    return hmac.compare_digest(provided_key, expected_key)


def extract_api_key_from_request(request: Request) -> Optional[str]:
    """从请求中提取 API 密钥"""
    auth_header = request.headers.get("Authorization")
    if auth_header:
        if auth_header.startswith("Bearer "):
            return auth_header[7:]
        return auth_header
    
    api_key = request.headers.get("X-API-Key")
    if api_key:
        return api_key
    
    api_key = request.query_params.get("api_key")
    if api_key:
        return api_key
    
    return None


async def require_api_auth(request: Request) -> bool:
    """要求 API 认证的依赖项"""
    if not is_api_auth_required():
        return True
    
    api_key = extract_api_key_from_request(request)
    if not api_key:
        raise HTTPException(
            status_code=401,
            detail="Missing API key. Please provide via Authorization header (Bearer <key>), X-API-Key header, or api_key query parameter.",
            headers={"WWW-Authenticate": "Bearer"}
        )
    
    if not verify_api_key(api_key):
        raise HTTPException(
            status_code=401,
            detail="Invalid API key",
            headers={"WWW-Authenticate": "Bearer"}
        )
    
    return True

class AdminAuthMiddleware:
    """管理员认证中间件"""

    def __init__(self):
        self.auth = get_admin_auth()

    def get_session_from_request(self, request: Request) -> Optional[str]:
        """从请求中提取会话ID"""
        # 1. 从Authorization头获取
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            return auth_header[7:]

        # 2. 从Cookie获取
        session_id = request.cookies.get("admin_session")
        if session_id:
            return session_id

        # 3. 从查询参数获取（用于WebSocket等场景）
        session_id = request.query_params.get("session")
        if session_id:
            return session_id

        return None

    async def authenticate_request(self, request: Request) -> bool:
        """验证请求是否已认证"""
        if not is_auth_required():
            return True
        
        session_id = self.get_session_from_request(request)
        if not session_id:
            return False

        return validate_admin_session(session_id)


# 全局中间件实例
_middleware_instance: Optional[AdminAuthMiddleware] = None

def get_auth_middleware() -> AdminAuthMiddleware:
    """获取认证中间件实例"""
    global _middleware_instance
    if _middleware_instance is None:
        _middleware_instance = AdminAuthMiddleware()
    return _middleware_instance


# FastAPI依赖项
async def require_admin_auth(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security)
) -> Optional[str]:
    """要求管理员认证的依赖项"""
    if not is_auth_required():
        return "no_auth_required"
    
    middleware = get_auth_middleware()

    # 检查认证
    if not await middleware.authenticate_request(request):
        raise HTTPException(
            status_code=401,
            detail="需要管理员认证",
            headers={"WWW-Authenticate": "Bearer"}
        )

    # 返回会话ID
    session_id = middleware.get_session_from_request(request)
    return session_id


async def optional_admin_auth(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security)
) -> Optional[str]:
    """可选的管理员认证依赖项"""
    middleware = get_auth_middleware()

    if await middleware.authenticate_request(request):
        return middleware.get_session_from_request(request)

    return None


# 装饰器版本（用于非FastAPI函数）
def require_admin_session(func):
    """要求管理员会话的装饰器"""
    @wraps(func)
    async def wrapper(*args, **kwargs):
        # 查找Request参数
        request = None
        for arg in args:
            if isinstance(arg, Request):
                request = arg
                break

        if not request:
            # 从kwargs查找
            request = kwargs.get('request')

        if not request:
            raise HTTPException(status_code=500, detail="无法获取请求对象")

        middleware = get_auth_middleware()
        if not await middleware.authenticate_request(request):
            raise HTTPException(
                status_code=401,
                detail="需要管理员认证",
                headers={"WWW-Authenticate": "Bearer"}
            )

        return await func(*args, **kwargs)

    return wrapper