#!/usr/bin/env python3
"""额度缓存管理工具

提供命令行接口来管理和优化额度缓存。
"""
import argparse
import json
import sys
from pathlib import Path
from typing import Dict, Any

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from kiro_proxy.core.quota_cache import (
    get_quota_cache, cleanup_cache_for_accounts,
    sync_cache_with_config, get_cache_performance_report
)


def print_json(data: Dict[str, Any], indent: int = 2):
    """格式化打印JSON数据"""
    print(json.dumps(data, indent=indent, ensure_ascii=False))


def cmd_status(args):
    """显示缓存状态"""
    cache = get_quota_cache()

    print("=== 缓存状态 ===")
    summary = cache.get_summary()
    print_json(summary)

    print("\n=== 健康检查 ===")
    health = cache.get_cache_health()
    print_json(health)


def cmd_cleanup(args):
    """清理缓存"""
    cache = get_quota_cache()

    if args.expired:
        print("清理过期缓存...")
        count = cache.cleanup_expired(args.max_age)
        print(f"清理了 {count} 个过期条目")

    if args.auto:
        print("执行自动清理...")
        stats = cache.auto_cleanup()
        print_json(stats)

    if args.accounts_file:
        print(f"基于账号文件清理: {args.accounts_file}")
        try:
            with open(args.accounts_file, 'r', encoding='utf-8') as f:
                config_data = json.load(f)

            accounts = config_data.get("accounts", {})
            if not accounts:
                print("错误: 配置文件中未找到账号信息")
                return

            valid_ids = set(accounts.keys())
            result = cleanup_cache_for_accounts(valid_ids)
            print_json(result)

        except Exception as e:
            print(f"错误: {e}")
            return

    # 保存更改
    if cache.save_to_file():
        print("缓存已保存")
    else:
        print("保存缓存失败")


def cmd_sync(args):
    """同步配置"""
    if not args.config_file:
        print("错误: 需要指定配置文件路径")
        return

    print(f"同步配置文件: {args.config_file}")
    result = sync_cache_with_config(args.config_file)
    print_json(result)


def cmd_performance(args):
    """性能报告"""
    print("=== 缓存性能报告 ===")
    report = get_cache_performance_report()

    if args.summary:
        # 只显示摘要
        perf = report["performance"]
        print(f"命中率: {perf['hit_rate']:.1f}%")
        print(f"效率评级: {perf['efficiency_grade']}")
        print(f"总请求数: {perf['total_requests']}")

        storage = report["storage"]
        print(f"文件大小: {storage['file_size_mb']:.2f} MB")

        if report["recommendations"]:
            print("\n建议:")
            for rec in report["recommendations"]:
                print(f"  - {rec}")
    else:
        # 显示完整报告
        print_json(report)


def cmd_stats(args):
    """统计信息"""
    cache = get_quota_cache()
    stats = cache.get_stats()

    print("=== 缓存统计 ===")
    print_json(stats.to_dict())


def cmd_save(args):
    """保存缓存"""
    cache = get_quota_cache()

    if args.force_full:
        print("强制完整保存...")
        success = cache.save_to_file(force_full=True)
    else:
        print("智能保存...")
        success = cache.save_to_file()

    if success:
        print("保存成功")
        stats = cache.get_stats()
        print(f"增量保存: {stats.incremental_saves}, 完整保存: {stats.full_saves}")
    else:
        print("保存失败")


def cmd_test(args):
    """测试缓存功能"""
    from kiro_proxy.core.quota_cache import CachedQuota
    import time

    cache = get_quota_cache()

    print("测试缓存功能...")

    # 添加测试数据
    test_quota = CachedQuota(
        account_id="test_account",
        usage_limit=100.0,
        current_usage=30.0,
        balance=70.0,
        usage_percent=30.0,
        updated_at=time.time()
    )

    cache.set("test_account", test_quota)

    # 测试获取
    result = cache.get("test_account")
    if result:
        print("✓ 设置和获取测试通过")
    else:
        print("✗ 设置和获取测试失败")

    # 测试批量操作
    batch_data = {}
    for i in range(3):
        batch_data[f"batch_test_{i}"] = CachedQuota(
            account_id=f"batch_test_{i}",
            usage_limit=100.0,
            current_usage=i * 10,
            balance=100.0 - i * 10,
            usage_percent=i * 10,
            updated_at=time.time()
        )

    cache.set_batch(batch_data)

    # 验证批量设置
    all_found = all(cache.get(f"batch_test_{i}") for i in range(3))
    if all_found:
        print("✓ 批量操作测试通过")
    else:
        print("✗ 批量操作测试失败")

    # 清理测试数据
    cache.remove("test_account")
    cache.remove_batch({f"batch_test_{i}" for i in range(3)})

    print("测试完成")


def main():
    """主函数"""
    parser = argparse.ArgumentParser(description="额度缓存管理工具")
    subparsers = parser.add_subparsers(dest="command", help="可用命令")

    # status 命令
    status_parser = subparsers.add_parser("status", help="显示缓存状态")
    status_parser.set_defaults(func=cmd_status)

    # cleanup 命令
    cleanup_parser = subparsers.add_parser("cleanup", help="清理缓存")
    cleanup_parser.add_argument("--expired", action="store_true", help="清理过期条目")
    cleanup_parser.add_argument("--max-age", type=int, default=300, help="最大缓存时间（秒）")
    cleanup_parser.add_argument("--auto", action="store_true", help="自动清理")
    cleanup_parser.add_argument("--accounts-file", help="账号配置文件路径")
    cleanup_parser.set_defaults(func=cmd_cleanup)

    # sync 命令
    sync_parser = subparsers.add_parser("sync", help="同步配置")
    sync_parser.add_argument("config_file", help="配置文件路径")
    sync_parser.set_defaults(func=cmd_sync)

    # performance 命令
    perf_parser = subparsers.add_parser("performance", help="性能报告")
    perf_parser.add_argument("--summary", action="store_true", help="只显示摘要")
    perf_parser.set_defaults(func=cmd_performance)

    # stats 命令
    stats_parser = subparsers.add_parser("stats", help="统计信息")
    stats_parser.set_defaults(func=cmd_stats)

    # save 命令
    save_parser = subparsers.add_parser("save", help="保存缓存")
    save_parser.add_argument("--force-full", action="store_true", help="强制完整保存")
    save_parser.set_defaults(func=cmd_save)

    # test 命令
    test_parser = subparsers.add_parser("test", help="测试缓存功能")
    test_parser.set_defaults(func=cmd_test)

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    try:
        args.func(args)
    except Exception as e:
        print(f"错误: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()