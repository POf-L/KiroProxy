"""Kiro 用量查询服务

通过调用 AWS Q 的 getUsageLimits API 获取用户的用量信息。
"""
import uuid
import httpx
from dataclasses import dataclass
from typing import Optional, Tuple


# API 端点
USAGE_LIMITS_URL = "https://q.us-east-1.amazonaws.com/getUsageLimits"

# 低余额阈值 (20%)
LOW_BALANCE_THRESHOLD = 0.2


@dataclass
class UsageInfo:
    """用量信息"""
    subscription_title: str = ""
    usage_limit: float = 0.0
    current_usage: float = 0.0
    balance: float = 0.0
    is_low_balance: bool = False
    
    # 详细信息
    free_trial_limit: float = 0.0
    free_trial_usage: float = 0.0
    bonus_limit: float = 0.0
    bonus_usage: float = 0.0
    
    # 重置和过期时间
    next_reset_date: Optional[str] = None  # 下次重置时间
    free_trial_expiry: Optional[str] = None  # 免费试用过期时间
    bonus_expiries: list = None  # 奖励过期时间列表
    
    def __post_init__(self):
        if self.bonus_expiries is None:
            self.bonus_expiries = []


def build_usage_api_url(auth_method: str, profile_arn: Optional[str] = None) -> str:
    """构造 API 请求 URL"""
    url = f"{USAGE_LIMITS_URL}?origin=AI_EDITOR&resourceType=AGENTIC_REQUEST"
    
    # Social 认证需要 profileArn
    if auth_method == "social" and profile_arn:
        from urllib.parse import quote
        url += f"&profileArn={quote(profile_arn)}"
    
    return url


def build_usage_headers(
    access_token: str,
    machine_id: str,
    kiro_version: str = "1.0.0"
) -> dict:
    """构造请求头"""
    import platform
    os_name = platform.system().lower()
    
    return {
        "Authorization": f"Bearer {access_token}",
        "User-Agent": f"aws-sdk-js/1.0.0 ua/2.1 os/{os_name} lang/python api/codewhispererruntime#1.0.0 m/N,E KiroIDE-{kiro_version}-{machine_id}",
        "x-amz-user-agent": f"aws-sdk-js/1.0.0 KiroIDE-{kiro_version}-{machine_id}",
        "amz-sdk-invocation-id": str(uuid.uuid4()),
        "amz-sdk-request": "attempt=1; max=1",
        "Connection": "close",
    }


def calculate_balance(response: dict) -> UsageInfo:
    """从 API 响应计算余额
    
    注意：只计算 resourceType 为 CREDIT 的额度，忽略其他类型（如 AGENTIC_REQUEST）
    """
    subscription_info = response.get("subscriptionInfo", {})
    usage_breakdown_list = response.get("usageBreakdownList", [])
    
    total_limit = 0.0
    total_usage = 0.0
    free_trial_limit = 0.0
    free_trial_usage = 0.0
    bonus_limit = 0.0
    bonus_usage = 0.0
    
    # 重置和过期时间
    next_reset_date = response.get("nextDateReset")  # 下次重置时间
    free_trial_expiry = None
    bonus_expiries = []
    
    # 只查找 CREDIT 类型的额度
    credit_breakdown = None
    for breakdown in usage_breakdown_list:
        resource_type = breakdown.get("resourceType", "")
        display_name = breakdown.get("displayName", "")
        if resource_type == "CREDIT" or display_name == "Credits":
            credit_breakdown = breakdown
            break
    
    if credit_breakdown:
        # 基本额度 (优先使用带精度的值)
        total_limit = credit_breakdown.get("usageLimitWithPrecision", 0.0) or credit_breakdown.get("usageLimit", 0.0)
        total_usage = credit_breakdown.get("currentUsageWithPrecision", 0.0) or credit_breakdown.get("currentUsage", 0.0)
        
        # 免费试用额度 (只有状态为 ACTIVE 时才计算)
        free_trial = credit_breakdown.get("freeTrialInfo")
        if free_trial and free_trial.get("freeTrialStatus") == "ACTIVE":
            ft_limit = free_trial.get("usageLimitWithPrecision", 0.0) or free_trial.get("usageLimit", 0.0)
            ft_usage = free_trial.get("currentUsageWithPrecision", 0.0) or free_trial.get("currentUsage", 0.0)
            total_limit += ft_limit
            total_usage += ft_usage
            free_trial_limit = ft_limit
            free_trial_usage = ft_usage
            # 获取免费试用过期时间
            free_trial_expiry = free_trial.get("freeTrialExpiry")
        
        # 奖励额度 (只计算状态为 ACTIVE 的奖励)
        bonuses = credit_breakdown.get("bonuses", [])
        for bonus in bonuses or []:
            if bonus.get("status") == "ACTIVE":
                b_limit = bonus.get("usageLimitWithPrecision", 0.0) or bonus.get("usageLimit", 0.0)
                b_usage = bonus.get("currentUsageWithPrecision", 0.0) or bonus.get("currentUsage", 0.0)
                total_limit += b_limit
                total_usage += b_usage
                bonus_limit += b_limit
                bonus_usage += b_usage
                # 获取奖励过期时间
                expires_at = bonus.get("expiresAt")
                if expires_at:
                    bonus_expiries.append(expires_at)
    
    balance = total_limit - total_usage
    is_low = (balance / total_limit) < LOW_BALANCE_THRESHOLD if total_limit > 0 else False
    
    return UsageInfo(
        subscription_title=subscription_info.get("subscriptionTitle", "Unknown"),
        usage_limit=total_limit,
        current_usage=total_usage,
        balance=balance,
        is_low_balance=is_low,
        free_trial_limit=free_trial_limit,
        free_trial_usage=free_trial_usage,
        bonus_limit=bonus_limit,
        bonus_usage=bonus_usage,
        next_reset_date=next_reset_date,
        free_trial_expiry=free_trial_expiry,
        bonus_expiries=bonus_expiries,
    )


async def get_usage_limits(
    access_token: str,
    auth_method: str = "social",
    profile_arn: Optional[str] = None,
    machine_id: str = "",
    kiro_version: str = "1.0.0",
) -> Tuple[bool, UsageInfo | dict]:
    """
    获取 Kiro 用量信息
    
    Args:
        access_token: Bearer token
        auth_method: 认证方式 ("social" 或 "idc")
        profile_arn: Social 认证需要的 profileArn
        machine_id: 设备 ID
        kiro_version: Kiro 版本号
    
    Returns:
        (success, UsageInfo or error_dict)
    """
    if not access_token:
        return False, {"error": "缺少 access token"}
    
    if not machine_id:
        return False, {"error": "缺少 machine ID"}
    
    # 构造 URL 和请求头
    url = build_usage_api_url(auth_method, profile_arn)
    headers = build_usage_headers(access_token, machine_id, kiro_version)
    
    try:
        async with httpx.AsyncClient(timeout=10, verify=False) as client:
            response = await client.get(url, headers=headers)
            
            if response.status_code != 200:
                return False, {"error": f"API 请求失败: {response.status_code} - {response.text[:200]}"}
            
            data = response.json()
            usage_info = calculate_balance(data)
            return True, usage_info
            
    except httpx.TimeoutException:
        return False, {"error": "请求超时"}
    except Exception as e:
        return False, {"error": f"请求失败: {str(e)}"}


async def get_account_usage(account) -> Tuple[bool, UsageInfo | dict]:
    """
    获取指定账号的用量信息
    
    Args:
        account: Account 对象
    
    Returns:
        (success, UsageInfo or error_dict)
    """
    from ..credential import get_kiro_version
    from .refresh_manager import get_refresh_manager
    
    creds = account.get_credentials()
    if not creds:
        return False, {"error": "无法获取凭证"}

    # 先刷新 Token（如即将过期/已过期），避免额度获取失败
    refresh_manager = get_refresh_manager()
    if refresh_manager.should_refresh_token(account):
        token_success, token_msg = await refresh_manager.refresh_token_if_needed(account)
        if not token_success:
            return False, {"error": f"Token 刷新失败: {token_msg}"}

    token = account.get_token()
    if not token:
        return False, {"error": "无法获取 token"}
    
    return await get_usage_limits(
        access_token=token,
        auth_method=creds.auth_method or "social",
        profile_arn=creds.profile_arn,
        machine_id=account.get_machine_id(),
        kiro_version=get_kiro_version(),
    )
