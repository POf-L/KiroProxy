"""调试额度信息获取"""
import asyncio
import json
from kiro_proxy.core.state import ProxyState


def debug_quota_info():
    """调试额度信息获取"""
    
    # 初始化状态管理器
    state = ProxyState()
    
    print("=== 调试账号额度信息 ===\n")
    
    for account in state.accounts[:2]:  # 只查看前两个账号
        print(f"账号: {account.name} ({account.id})")
        
        # 获取状态信息
        status = account.get_status_info()
        
        if "quota" in status and status["quota"]:
            quota = status["quota"]
            print(f"  - 额度状态: {quota.get('balance_status', 'unknown')}")
            print(f"  - 已用/总额: {quota.get('current_usage', 0)} / {quota.get('usage_limit', 0)}")
            print(f"  - 剩余额度: {quota.get('balance', 0)}")
            print(f"  - 更新时间: {quota.get('updated_at', 'unknown')}")
            
            # 检查重置时间字段
            print(f"  - 下次重置时间: {quota.get('next_reset_date', '未设置')}")
            print(f"  - 格式化重置日期: {quota.get('reset_date_text', '未设置')}")
            print(f"  - 免费试用过期: {quota.get('free_trial_expiry', '未设置')}")
            print(f"  - 格式化过期日期: {quota.get('trial_expiry_text', '未设置')}")
            print(f"  - 奖励过期列表: {quota.get('bonus_expiries', [])}")
            print(f"  - 生效奖励数: {quota.get('active_bonuses', 0)}")
        else:
            print("  - 无额度信息")
            if status.get("quota", {}).get("error"):
                print(f"  - 错误: {status['quota']['error']}")
        
        print()
    
    # 模拟 API 响应
    print("=== 模拟 Web API 响应 ===\n")
    
    accounts_status = state.get_accounts_status()
    
    # 只显示第一个账号的信息
    if accounts_status:
        first_account = accounts_status[0]
        print(json.dumps(first_account, indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    debug_quota_info()
