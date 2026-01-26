"""展示额度重置时间功能的示例"""
import json
from datetime import datetime


def generate_quota_display_example():
    """生成额度显示示例"""
    
    # 模拟账号的额度信息（从 API 获取）
    quota_data = {
        "subscription_title": "Kiro Pro",
        "usage_limit": 700.0,
        "current_usage": 150.0,
        "balance": 550.0,
        "usage_percent": 21.4,
        "is_low_balance": False,
        "is_exhausted": False,
        "balance_status": "normal",
        
        # 免费试用信息
        "free_trial_limit": 500.0,
        "free_trial_usage": 100.0,
        "free_trial_expiry": "2026-02-13T23:59:59Z",
        "trial_expiry_text": "2026-02-13",
        
        # 奖励信息
        "bonus_limit": 150.0,
        "bonus_usage": 25.0,
        "bonus_expiries": ["2026-03-01T23:59:59Z", "2026-02-28T23:59:59Z"],
        "active_bonuses": 2,
        
        # 重置时间
        "next_reset_date": "2026-02-01T00:00:00Z",
        "reset_date_text": "2026-02-01",
        
        # 更新时间
        "updated_at": "2分钟前",
        "error": None
    }
    
    # 生成 HTML 显示片段（类似在 Web 界面中的显示）
    html_template = """
<div class="account-quota-section">
  <div class="quota-header">
    <span>已用/总额</span>
    <span>{current_usage:.1f} / {usage_limit:.1f}</span>
  </div>
  <div class="progress-bar">
    <div class="progress-fill" style="width: {usage_percent:.1f}%"></div>
  </div>
  <div class="quota-detail">
    <span>试用: {free_trial_usage:.0f}/{free_trial_limit:.0f}</span>
    <span>奖励: {bonus_usage:.0f}/{bonus_limit:.0f} ({active_bonuses}个)</span>
    <span>更新: {updated_at}</span>
  </div>
  <div class="quota-reset-info">
    <span>🔄 重置: {reset_date_text}</span>
    <span>🎁 试用过期: {trial_expiry_text}</span>
  </div>
</div>
    """.format(**quota_data)
    
    print("=== 额度信息展示示例 ===")
    print(html_template)
    
    # 生成卡片式展示
    card_template = """
<div class="quota-card">
  <h3>主配额</h3>
  <div class="quota-amount">{current_usage:.0f} / {usage_limit:.0f}</div>
  <div class="quota-reset">2026-02-01 重置</div>
</div>
<div class="quota-card">
  <h3>免费试用</h3>
  <div class="quota-amount">{free_trial_usage:.0f} / {free_trial_limit:.0f}</div>
  <div class="quota-expiry">ACTIVE</div>
  <div class="quota-reset">2026-02-13 过期</div>
</div>
<div class="quota-card">
  <h3>奖励总计</h3>
  <div class="quota-amount">{bonus_usage:.0f} / {bonus_limit:.0f}</div>
  <div class="quota-expiry">{active_bonuses}个生效奖励</div>
</div>
    """.format(**quota_data)
    
    print("\n=== 卡片式展示（如图所示）===")
    print(card_template)
    
    # 生成 JSON 数据
    print("\n=== JSON 数据格式 ===")
    print(json.dumps(quota_data, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    generate_quota_display_example()
