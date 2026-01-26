"""测试额度重置时间功能"""
import asyncio
import json
from datetime import datetime, timezone, timedelta
from kiro_proxy.core.usage import calculate_balance, UsageInfo


def test_quota_reset_time():
    """测试额度重置时间解析"""
    
    # 模拟 API 响应数据
    mock_response = {
        "subscriptionInfo": {
            "subscriptionTitle": "Kiro Pro"
        },
        "usageBreakdownList": [
            {
                "resourceType": "CREDIT",
                "displayName": "Credits",
                "usageLimitWithPrecision": 50.0,
                "currentUsageWithPrecision": 25.0,
                "freeTrialInfo": {
                    "freeTrialStatus": "ACTIVE",
                    "usageLimitWithPrecision": 500.0,
                    "currentUsageWithPrecision": 100.0,
                    "freeTrialExpiry": "2026-02-13T23:59:59Z"
                },
                "bonuses": [
                    {
                        "status": "ACTIVE",
                        "usageLimitWithPrecision": 100.0,
                        "currentUsageWithPrecision": 0.0,
                        "expiresAt": "2026-03-01T23:59:59Z"
                    },
                    {
                        "status": "ACTIVE",
                        "usageLimitWithPrecision": 50.0,
                        "currentUsageWithPrecision": 25.0,
                        "expiresAt": "2026-02-28T23:59:59Z"
                    }
                ]
            }
        ],
        "nextDateReset": "2026-02-01T00:00:00Z"
    }
    
    # 解析额度信息
    usage_info = calculate_balance(mock_response)
    
    # 验证结果
    print("=== 额度信息解析结果 ===")
    print(f"订阅类型: {usage_info.subscription_title}")
    print(f"总额度: {usage_info.usage_limit}")
    print(f"已用额度: {usage_info.current_usage}")
    print(f"剩余额度: {usage_info.balance}")
    print(f"使用率: {(usage_info.current_usage / usage_info.usage_limit * 100):.1f}%")
    print(f"下次重置时间: {usage_info.next_reset_date}")
    print(f"免费试用过期时间: {usage_info.free_trial_expiry}")
    print(f"奖励过期时间列表: {usage_info.bonus_expiries}")
    
    # 验证具体数值
    assert usage_info.usage_limit == 700.0  # 50 + 500 + 100 + 50
    assert usage_info.current_usage == 150.0  # 25 + 100 + 0 + 25
    assert usage_info.balance == 550.0
    assert usage_info.free_trial_limit == 500.0
    assert usage_info.free_trial_usage == 100.0
    assert usage_info.bonus_limit == 150.0  # 100 + 50
    assert usage_info.bonus_usage == 25.0  # 0 + 25
    assert usage_info.next_reset_date == "2026-02-01T00:00:00Z"
    assert usage_info.free_trial_expiry == "2026-02-13T23:59:59Z"
    assert len(usage_info.bonus_expiries) == 2
    
    print("\n✅ 测试通过！")
    

def test_quota_cache_with_reset_time():
    """测试额度缓存的重置时间功能"""
    from kiro_proxy.core.quota_cache import CachedQuota
    
    # 创建 UsageInfo
    usage_info = UsageInfo(
        subscription_title="Kiro Pro",
        usage_limit=100.0,
        current_usage=50.0,
        balance=50.0,
        next_reset_date="2026-02-01T00:00:00Z",
        free_trial_expiry="2026-02-13T23:59:59Z",
        bonus_expiries=["2026-03-01T23:59:59Z", "2026-02-28T23:59:59Z"]
    )
    
    # 从 UsageInfo 创建 CachedQuota
    cached_quota = CachedQuota.from_usage_info("test_account", usage_info)
    
    # 验证转换结果
    print("\n=== 缓存额度信息 ===")
    print(f"账号ID: {cached_quota.account_id}")
    print(f"下次重置时间: {cached_quota.next_reset_date}")
    print(f"免费试用过期时间: {cached_quota.free_trial_expiry}")
    print(f"奖励过期时间: {cached_quota.bonus_expiries}")
    
    # 转换为字典并验证
    quota_dict = cached_quota.to_dict()
    assert quota_dict["next_reset_date"] == "2026-02-01T00:00:00Z"
    assert quota_dict["free_trial_expiry"] == "2026-02-13T23:59:59Z"
    assert len(quota_dict["bonus_expiries"]) == 2
    
    # 从字典重建并验证
    rebuilt_quota = CachedQuota.from_dict(quota_dict)
    assert rebuilt_quota.next_reset_date == cached_quota.next_reset_date
    assert rebuilt_quota.free_trial_expiry == cached_quota.free_trial_expiry
    assert rebuilt_quota.bonus_expiries == cached_quota.bonus_expiries
    
    print("\n✅ 缓存测试通过！")


def test_account_status_info():
    """测试账号状态信息中的重置时间"""
    from kiro_proxy.core.account import Account
    from kiro_proxy.core.quota_cache import CachedQuota, get_quota_cache
    
    # 创建模拟账号
    account = Account(
        id="test_account",
        name="测试账号",
        token_path="/tmp/test_token.json"
    )
    
    # 创建缓存额度
    cached_quota = CachedQuota(
        account_id="test_account",
        usage_limit=100.0,
        current_usage=50.0,
        balance=50.0,
        next_reset_date="2026-02-01T00:00:00Z",
        free_trial_expiry="2026-02-13T23:59:59Z",
        bonus_expiries=["2026-03-01T23:59:59Z"]
    )
    
    # 设置缓存
    quota_cache = get_quota_cache()
    quota_cache.set("test_account", cached_quota)
    
    # 获取状态信息
    status_info = account.get_status_info()
    
    # 验证重置时间信息
    print("\n=== 账号状态信息 ===")
    quota_info = status_info.get("quota")
    if quota_info:
        print(f"下次重置时间: {quota_info.get('next_reset_date')}")
        print(f"格式化重置日期: {quota_info.get('reset_date_text')}")
        print(f"免费试用过期时间: {quota_info.get('free_trial_expiry')}")
        print(f"格式化过期日期: {quota_info.get('trial_expiry_text')}")
        print(f"生效奖励数: {quota_info.get('active_bonuses')}")
        
        assert quota_info["reset_date_text"] == "2026-02-01"
        assert quota_info["trial_expiry_text"] == "2026-02-13"
        assert quota_info["active_bonuses"] == 1
    
    print("\n✅ 账号状态测试通过！")


if __name__ == "__main__":
    test_quota_reset_time()
    test_quota_cache_with_reset_time()
    test_account_status_info()
    print("\n🎉 所有测试通过！额度重置时间功能已成功实现。")
