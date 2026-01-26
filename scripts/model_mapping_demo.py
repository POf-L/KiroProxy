#!/usr/bin/env python3
"""Manual demo for the smart model-mapping helpers."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kiro_proxy.config import map_model_name, detect_model_tier, get_best_model_by_tier

def test_tier_detection():
    """测试等级检测功能"""
    print("测试模型等级检测:")

    test_cases = [
        # Opus 等级 (最强)
        ("claude-4-opus", "opus"),
        ("gpt-o1-preview", "opus"),
        ("gemini-1.5-pro", "opus"),
        ("claude-3-opus-20240229", "opus"),
        ("some-premium-model", "opus"),

        # Sonnet 等级 (平衡)
        ("claude-3.5-sonnet", "sonnet"),
        ("gpt-4o", "sonnet"),
        ("gemini-2.0-flash", "sonnet"),
        ("claude-4-standard", "sonnet"),

        # Haiku 等级 (快速)
        ("claude-3-haiku", "haiku"),
        ("gpt-4o-mini", "haiku"),
        ("gpt-3.5-turbo", "haiku"),
        ("claude-haiku-fast", "haiku"),

        # 未知模型
        ("unknown-model-xyz", "sonnet"),  # 默认中等
        ("", "sonnet"),  # 空值默认
    ]

    for model, expected in test_cases:
        result = detect_model_tier(model)
        status = "OK" if result == expected else "FAIL"
        print(f"  {status} {model:<25} -> {result:<6} (期望: {expected})")

def test_dynamic_mapping():
    """测试动态模型映射（等级对等 + 智能降级）"""
    print("\n测试动态模型映射（等级对等策略）:")

    # 模拟不同的可用模型场景
    scenarios = [
        {
            "name": "全部可用",
            "available": {"claude-sonnet-4.5", "claude-sonnet-4", "claude-haiku-4.5", "auto"}
        },
        {
            "name": "缺少4.5版本",
            "available": {"claude-sonnet-4", "claude-haiku-4.5", "auto"}
        },
        {
            "name": "仅有Haiku",
            "available": {"claude-haiku-4.5", "auto"}
        },
        {
            "name": "仅有Sonnet-4",
            "available": {"claude-sonnet-4", "auto"}
        }
    ]

    test_models = [
        ("claude-4-opus", "opus"),      # 应该优先选择 sonnet-4.5
        ("gpt-4o", "sonnet"),          # 应该优先选择 sonnet-4.5
        ("gpt-4o-mini", "haiku"),      # 应该优先选择 haiku-4.5
        ("unknown-future-model", "sonnet")  # 未知模型，默认 sonnet-4.5
    ]

    for scenario in scenarios:
        print(f"\n  场景: {scenario['name']}")
        print(f"     可用模型: {scenario['available']}")

        for model, expected_tier in test_models:
            result = map_model_name(model, scenario['available'])
            tier = detect_model_tier(model)
            print(f"     {model:<20} ({tier:<6}) -> {result}")

def test_tier_mapping_logic():
    """测试等级对等映射逻辑"""
    print("\n测试等级对等映射逻辑:")

    # 全部可用时的期望映射
    full_available = {"claude-sonnet-4.5", "claude-sonnet-4", "claude-haiku-4.5", "auto"}

    test_cases = [
        # 格式: (输入模型, 期望等级, 期望输出模型)
        ("claude-4-opus", "opus", "claude-sonnet-4.5"),      # Opus -> 最强
        ("gpt-4o", "sonnet", "claude-sonnet-4.5"),           # Sonnet -> 高性能
        ("gpt-4o-mini", "haiku", "claude-haiku-4.5"),        # Haiku -> 快速
        ("o1-preview", "opus", "claude-sonnet-4.5"),         # O1 -> 最强
        ("claude-3.5-sonnet", "sonnet", "claude-sonnet-4.5"), # Sonnet -> 高性能
        ("gpt-3.5-turbo", "haiku", "claude-haiku-4.5"),      # 3.5 -> 快速
    ]

    for model, expected_tier, expected_output in test_cases:
        tier = detect_model_tier(model)
        result = map_model_name(model, full_available)
        tier_ok = "OK" if tier == expected_tier else "FAIL"
        output_ok = "OK" if result == expected_output else "FAIL"
        print(f"  {tier_ok}/{output_ok} {model:<20} -> {tier:<6} -> {result}")
        if tier != expected_tier:
            print(f"       等级检测错误: 期望 {expected_tier}, 实际 {tier}")
        if result != expected_output:
            print(f"       映射错误: 期望 {expected_output}, 实际 {result}")

def test_degradation_paths():
    """测试降级路径"""
    print("\n测试降级路径:")

    degradation_scenarios = [
        {
            "name": "Opus降级测试",
            "model": "claude-4-opus",
            "scenarios": [
                ({"claude-sonnet-4.5", "auto"}, "claude-sonnet-4.5"),  # 首选可用
                ({"claude-sonnet-4", "auto"}, "claude-sonnet-4"),      # 降级到次强
                ({"claude-haiku-4.5", "auto"}, "claude-haiku-4.5"),    # 降级到快速
                ({"auto"}, "auto"),                                     # 最终回退
            ]
        },
        {
            "name": "Haiku降级测试",
            "model": "gpt-4o-mini",
            "scenarios": [
                ({"claude-haiku-4.5", "auto"}, "claude-haiku-4.5"),    # 首选可用
                ({"claude-sonnet-4", "auto"}, "claude-sonnet-4"),      # 降级到标准
                ({"claude-sonnet-4.5", "auto"}, "claude-sonnet-4.5"),  # 降级到高性能
                ({"auto"}, "auto"),                                     # 最终回退
            ]
        }
    ]

    for test_group in degradation_scenarios:
        print(f"\n  {test_group['name']}:")
        model = test_group['model']
        tier = detect_model_tier(model)

        for available, expected in test_group['scenarios']:
            result = map_model_name(model, available)
            status = "OK" if result == expected else "FAIL"
            print(f"    {status} 可用:{available} -> {result} (期望:{expected})")

def test_backward_compatibility():
    """测试向后兼容性"""
    print("\n测试向后兼容性:")

    # 原有的精确映射应该仍然工作
    legacy_tests = [
        ("gpt-4o", "claude-sonnet-4"),
        ("claude-3-5-sonnet-20241022", "claude-sonnet-4"),
        ("o1-preview", "claude-sonnet-4.5"),
        ("gemini-1.5-pro", "claude-sonnet-4.5"),
    ]

    for model, expected in legacy_tests:
        result = map_model_name(model)
        status = "OK" if result == expected else "FAIL"
        print(f"  {status} {model:<25} -> {result:<20} (期望: {expected})")

def test_edge_cases():
    """测试边界情况"""
    print("\n测试边界情况:")

    edge_cases = [
        ("", "auto"),  # 空字符串
        (None, "auto"),  # None值 (需要修改函数处理)
        ("CLAUDE-4-OPUS", "claude-sonnet-4.5"),  # 大写
        ("gpt-4o-MINI-turbo", "claude-haiku-4.5"),  # 混合大小写
        ("claude_sonnet_4", "claude-sonnet-4"),  # 下划线
    ]

    for model, expected in edge_cases:
        try:
            result = map_model_name(model or "")
            tier = detect_model_tier(model or "")
            status = "OK" if result == expected else "FAIL"
            print(f"  {status} {str(model):<25} ({tier}) -> {result}")
        except Exception as e:
            print(f"  ERROR {str(model):<25} -> 错误: {e}")

if __name__ == "__main__":
    print("KiroProxy 智能模型映射测试（等级对等策略）\n")

    test_tier_detection()
    test_tier_mapping_logic()
    test_degradation_paths()
    test_dynamic_mapping()
    test_backward_compatibility()
    test_edge_cases()

    print("\n测试完成!")
