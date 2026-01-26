"""Kiro Web Portal API 调用模块

调用 Kiro 的 Web Portal API 获取用户信息，使用 CBOR 编码。
参考: chaogei/Kiro-account-manager
"""
import uuid
import httpx
from typing import Optional, Tuple, Any, Dict

try:
    import cbor2
    HAS_CBOR = True
except ImportError:
    HAS_CBOR = False
    print("[KiroAPI] 警告: cbor2 未安装，部分功能不可用。请运行: pip install cbor2")


# Kiro Web Portal API 基础 URL
KIRO_API_BASE = "https://app.kiro.dev/service/KiroWebPortalService/operation"


async def kiro_api_request(
    operation: str,
    body: Dict[str, Any],
    access_token: str,
    idp: str = "Google",
) -> Tuple[bool, Any]:
    """
    调用 Kiro Web Portal API
    
    Args:
        operation: API 操作名称，如 "GetUserUsageAndLimits"
        body: 请求体（会被 CBOR 编码）
        access_token: Bearer token
        idp: 身份提供商 ("Google" 或 "Github")
    
    Returns:
        (success, response_data or error_dict)
    """
    if not HAS_CBOR:
        return False, {"error": "cbor2 未安装"}
    
    if not access_token:
        return False, {"error": "缺少 access token"}
    
    url = f"{KIRO_API_BASE}/{operation}"
    
    # CBOR 编码请求体
    try:
        encoded_body = cbor2.dumps(body)
    except Exception as e:
        return False, {"error": f"CBOR 编码失败: {e}"}
    
    headers = {
        "accept": "application/cbor",
        "content-type": "application/cbor",
        "smithy-protocol": "rpc-v2-cbor",
        "amz-sdk-invocation-id": str(uuid.uuid4()),
        "amz-sdk-request": "attempt=1; max=1",
        "x-amz-user-agent": "aws-sdk-js/1.0.0 kiro-proxy/1.0.0",
        "authorization": f"Bearer {access_token}",
        "cookie": f"Idp={idp}; AccessToken={access_token}",
    }
    
    try:
        async with httpx.AsyncClient(timeout=15, verify=False) as client:
            response = await client.post(url, content=encoded_body, headers=headers)
            
            if response.status_code != 200:
                return False, {"error": f"API 请求失败: {response.status_code}"}
            
            # CBOR 解码响应
            try:
                data = cbor2.loads(response.content)
                return True, data
            except Exception as e:
                return False, {"error": f"CBOR 解码失败: {e}"}
                
    except httpx.TimeoutException:
        return False, {"error": "请求超时"}
    except Exception as e:
        return False, {"error": f"请求失败: {str(e)}"}


async def get_user_info(
    access_token: str,
    idp: str = "Google",
) -> Tuple[bool, Dict[str, Any]]:
    """
    获取用户信息（包括邮箱）
    
    Args:
        access_token: Bearer token
        idp: 身份提供商 ("Google" 或 "Github")
    
    Returns:
        (success, user_info or error_dict)
        user_info 包含: email, userId 等
    """
    success, result = await kiro_api_request(
        operation="GetUserUsageAndLimits",
        body={"isEmailRequired": True, "origin": "KIRO_IDE"},
        access_token=access_token,
        idp=idp,
    )
    
    if not success:
        return False, result
    
    # 提取用户信息
    user_info = result.get("userInfo", {})
    return True, {
        "email": user_info.get("email"),
        "userId": user_info.get("userId"),
        "raw": result,
    }


async def get_user_email(
    access_token: str,
    provider: str = "Google",
) -> Optional[str]:
    """
    获取用户邮箱地址
    
    Args:
        access_token: Bearer token
        provider: 登录提供商 ("Google" 或 "Github")
    
    Returns:
        邮箱地址，失败返回 None
    """
    # 标准化 provider 名称
    idp = provider
    if provider and provider.lower() == "google":
        idp = "Google"
    elif provider and provider.lower() == "github":
        idp = "Github"
    
    success, result = await get_user_info(access_token, idp)
    
    if success:
        return result.get("email")
    
    print(f"[KiroAPI] 获取邮箱失败: {result.get('error', '未知错误')}")
    return None
