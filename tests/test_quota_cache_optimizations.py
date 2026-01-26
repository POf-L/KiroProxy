"""测试额度缓存优化功能"""
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from kiro_proxy.core.quota_cache import (
    QuotaCache, CachedQuota, CacheStats, BalanceStatus,
    cleanup_cache_for_accounts, sync_cache_with_config,
    get_cache_performance_report, DEFAULT_MAX_CACHE_SIZE
)


class TestQuotaCacheOptimizations(unittest.TestCase):
    """测试额度缓存优化功能"""

    def setUp(self):
        """设置测试环境"""
        self.temp_dir = tempfile.mkdtemp()
        self.cache_file = Path(self.temp_dir) / "test_quota_cache.json"
        self.cache = QuotaCache(str(self.cache_file), max_size=10)

    def tearDown(self):
        """清理测试环境"""
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _create_test_quota(self, account_id: str, balance: float = 100.0,
                          usage: float = 50.0, limit: float = 150.0) -> CachedQuota:
        """创建测试用的额度信息"""
        return CachedQuota(
            account_id=account_id,
            usage_limit=limit,
            current_usage=usage,
            balance=balance,
            usage_percent=round((usage / limit) * 100, 2),
            updated_at=time.time()
        )

    def test_lru_eviction(self):
        """测试LRU淘汰机制"""
        # 添加超过最大容量的条目
        for i in range(15):
            quota = self._create_test_quota(f"account_{i}")
            self.cache.set(f"account_{i}", quota)

        # 验证缓存大小不超过限制
        all_cache = self.cache.get_all()
        self.assertEqual(len(all_cache), 10)

        # 验证最新的条目被保留
        self.assertIsNotNone(self.cache.get("account_14"))
        self.assertIsNotNone(self.cache.get("account_13"))

        # 验证最旧的条目被淘汰
        self.assertIsNone(self.cache.get("account_0"))
        self.assertIsNone(self.cache.get("account_1"))

    def test_batch_operations(self):
        """测试批量操作"""
        # 批量设置
        updates = {}
        for i in range(5):
            updates[f"account_{i}"] = self._create_test_quota(f"account_{i}")

        self.cache.set_batch(updates)

        # 验证所有条目都被设置
        for i in range(5):
            self.assertIsNotNone(self.cache.get(f"account_{i}"))

        # 批量删除
        to_remove = {"account_0", "account_1", "account_2"}
        removed_count = self.cache.remove_batch(to_remove)

        self.assertEqual(removed_count, 3)
        self.assertIsNone(self.cache.get("account_0"))
        self.assertIsNotNone(self.cache.get("account_3"))

    def test_batch_queue(self):
        """测试批量更新队列"""
        # 添加到批量队列
        for i in range(3):
            quota = self._create_test_quota(f"batch_account_{i}")
            self.cache.add_to_batch(f"batch_account_{i}", quota)

        # 手动刷新队列
        self.cache.flush_pending_updates()

        # 验证条目已被添加
        for i in range(3):
            self.assertIsNotNone(self.cache.get(f"batch_account_{i}"))

    def test_cache_stats(self):
        """测试缓存统计"""
        # 初始统计
        stats = self.cache.get_stats()
        self.assertEqual(stats.hit_count, 0)
        self.assertEqual(stats.miss_count, 0)

        # 添加条目
        quota = self._create_test_quota("test_account")
        self.cache.set("test_account", quota)

        # 命中测试
        result = self.cache.get("test_account")
        self.assertIsNotNone(result)

        # 未命中测试
        result = self.cache.get("nonexistent_account")
        self.assertIsNone(result)

        # 验证统计
        stats = self.cache.get_stats()
        self.assertEqual(stats.hit_count, 1)
        self.assertEqual(stats.miss_count, 1)
        self.assertEqual(stats.total_requests, 2)
        self.assertEqual(stats.hit_rate, 50.0)

    def test_cleanup_expired(self):
        """测试过期缓存清理"""
        # 添加过期条目
        old_quota = self._create_test_quota("old_account")
        old_quota.updated_at = time.time() - 400  # 400秒前，超过默认300秒

        new_quota = self._create_test_quota("new_account")

        self.cache.set("old_account", old_quota)
        self.cache.set("new_account", new_quota)

        # 清理过期条目
        cleaned_count = self.cache.cleanup_expired(max_age_seconds=300)

        self.assertEqual(cleaned_count, 1)
        self.assertIsNone(self.cache.get("old_account"))
        self.assertIsNotNone(self.cache.get("new_account"))

    def test_cleanup_deleted_accounts(self):
        """测试删除账号清理"""
        # 添加多个账号
        for i in range(5):
            quota = self._create_test_quota(f"account_{i}")
            self.cache.set(f"account_{i}", quota)

        # 模拟只有部分账号仍然有效
        valid_accounts = {"account_0", "account_2", "account_4"}
        cleaned_count = self.cache.cleanup_deleted_accounts(valid_accounts)

        self.assertEqual(cleaned_count, 2)  # account_1 和 account_3 被清理
        self.assertIsNotNone(self.cache.get("account_0"))
        self.assertIsNone(self.cache.get("account_1"))
        self.assertIsNotNone(self.cache.get("account_2"))
        self.assertIsNone(self.cache.get("account_3"))
        self.assertIsNotNone(self.cache.get("account_4"))

    def test_sync_with_config(self):
        """测试配置同步"""
        # 添加一些现有缓存
        for i in range(3):
            quota = self._create_test_quota(f"old_account_{i}")
            self.cache.set(f"old_account_{i}", quota)

        # 模拟新的配置
        config_accounts = {
            "old_account_0": {"name": "Account 0"},  # 保留
            "new_account_1": {"name": "Account 1"},  # 新增
            "new_account_2": {"name": "Account 2"},  # 新增
        }

        cleaned_count, new_count = self.cache.sync_with_config(config_accounts)

        self.assertEqual(cleaned_count, 2)  # old_account_1, old_account_2 被清理
        self.assertEqual(new_count, 2)      # new_account_1, new_account_2 是新的

        # 验证清理结果
        self.assertIsNotNone(self.cache.get("old_account_0"))
        self.assertIsNone(self.cache.get("old_account_1"))
        self.assertIsNone(self.cache.get("old_account_2"))

    def test_auto_cleanup(self):
        """测试自动清理"""
        # 添加过期和正常条目
        old_quota = self._create_test_quota("old_account")
        old_quota.updated_at = time.time() - 400

        new_quota = self._create_test_quota("new_account")

        self.cache.set("old_account", old_quota)
        self.cache.set("new_account", new_quota)

        # 模拟需要清理的情况
        with patch.object(self.cache._stats, 'last_cleanup', time.time() - 4000):
            cleanup_stats = self.cache.auto_cleanup()

        self.assertEqual(cleanup_stats["expired"], 1)
        self.assertIsNone(self.cache.get("old_account"))
        self.assertIsNotNone(self.cache.get("new_account"))

    def test_cache_health(self):
        """测试缓存健康检查"""
        # 添加各种状态的条目
        normal_quota = self._create_test_quota("normal_account")

        expired_quota = self._create_test_quota("expired_account")
        expired_quota.updated_at = time.time() - 400

        error_quota = self._create_test_quota("error_account")
        error_quota.error = "Test error"

        self.cache.set("normal_account", normal_quota)
        self.cache.set("expired_account", expired_quota)
        self.cache.set("error_account", error_quota)

        health = self.cache.get_cache_health()

        self.assertEqual(health["total_entries"], 3)
        self.assertEqual(health["expired_count"], 1)
        self.assertEqual(health["error_count"], 1)
        self.assertTrue(health["needs_cleanup"])

    def test_incremental_save(self):
        """测试增量保存"""
        # 先进行一次完整保存
        quota1 = self._create_test_quota("account_1")
        self.cache.set("account_1", quota1)
        self.cache.save_to_file(force_full=True)

        # 添加新条目
        quota2 = self._create_test_quota("account_2")
        self.cache.set("account_2", quota2)

        # 模拟文件足够大以触发增量保存
        with patch.object(self.cache, '_should_use_incremental_save', return_value=True):
            success = self.cache.save_to_file()

        self.assertTrue(success)

        # 验证统计
        stats = self.cache.get_stats()
        self.assertGreater(stats.incremental_saves, 0)

    def test_performance_report(self):
        """测试性能报告"""
        # 添加一些数据和统计
        for i in range(5):
            quota = self._create_test_quota(f"account_{i}")
            self.cache.set(f"account_{i}", quota)

        # 模拟一些缓存访问
        for i in range(3):
            self.cache.get(f"account_{i}")  # 命中
        self.cache.get("nonexistent")  # 未命中

        report = get_cache_performance_report()

        self.assertIn("performance", report)
        self.assertIn("storage", report)
        self.assertIn("maintenance", report)
        self.assertIn("recommendations", report)

        # 验证性能指标
        perf = report["performance"]
        self.assertGreater(perf["hit_rate"], 0)
        self.assertIn(perf["efficiency_grade"], ["优秀", "良好", "一般", "需要优化"])

    def test_global_cleanup_function(self):
        """测试全局清理函数"""
        # 添加一些缓存数据
        for i in range(5):
            quota = self._create_test_quota(f"account_{i}")
            self.cache.set(f"account_{i}", quota)

        # 模拟只有部分账号有效
        valid_accounts = {"account_0", "account_2"}

        with patch('kiro_proxy.core.quota_cache.get_quota_cache', return_value=self.cache):
            result = cleanup_cache_for_accounts(valid_accounts)

        self.assertEqual(result["deleted_accounts"], 3)
        self.assertGreaterEqual(result["total_cleaned"], 3)

    def test_config_sync_function(self):
        """测试配置同步函数"""
        # 创建临时配置文件
        config_file = Path(self.temp_dir) / "test_config.json"
        config_data = {
            "accounts": {
                "account_1": {"name": "Account 1"},
                "account_2": {"name": "Account 2"}
            }
        }

        with open(config_file, 'w', encoding='utf-8') as f:
            json.dump(config_data, f)

        with patch('kiro_proxy.core.quota_cache.get_quota_cache', return_value=self.cache):
            result = sync_cache_with_config(str(config_file))

        self.assertTrue(result["success"])
        self.assertEqual(result["total_config_accounts"], 2)

    def test_backward_compatibility(self):
        """测试向后兼容性"""
        # 验证原有API仍然工作
        quota = self._create_test_quota("test_account")

        # 原有方法
        self.cache.set("test_account", quota)
        result = self.cache.get("test_account")
        self.assertIsNotNone(result)

        self.assertFalse(self.cache.is_stale("test_account"))

        all_cache = self.cache.get_all()
        self.assertIn("test_account", all_cache)

        self.cache.remove("test_account")
        self.assertIsNone(self.cache.get("test_account"))

        # 文件操作
        self.cache.set("test_account", quota)
        self.assertTrue(self.cache.save_to_file())

        self.cache.clear()
        self.assertTrue(self.cache.load_from_file())
        self.assertIsNotNone(self.cache.get("test_account"))

        # 汇总信息
        summary = self.cache.get_summary()
        self.assertIn("total_accounts", summary)
        self.assertIn("stats", summary)  # 新增字段


if __name__ == '__main__':
    unittest.main()