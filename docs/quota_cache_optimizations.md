# 额度缓存优化功能文档

## 概述

本文档描述了 KiroProxy 额度缓存系统的优化功能，包括智能清理机制、增量更新优化和性能监控等新特性。

## 新增功能

### 1. 智能清理机制

#### 1.1 自动清理已删除账号的缓存

```python
from kiro_proxy.core.quota_cache import cleanup_cache_for_accounts

# 清理已删除账号的缓存
valid_account_ids = {"account_1", "account_2", "account_3"}
result = cleanup_cache_for_accounts(valid_account_ids)
print(f"清理了 {result['deleted_accounts']} 个已删除账号的缓存")
```

#### 1.2 基于配置文件的缓存同步

```python
from kiro_proxy.core.quota_cache import sync_cache_with_config

# 基于配置文件同步缓存
result = sync_cache_with_config("path/to/accounts.json")
if result["success"]:
    print(f"清理: {result['cleaned_accounts']}, 新增: {result['new_accounts']}")
```

#### 1.3 过期缓存自动清理

```python
cache = get_quota_cache()

# 清理过期缓存（默认5分钟）
expired_count = cache.cleanup_expired()

# 自定义过期时间（10分钟）
expired_count = cache.cleanup_expired(max_age_seconds=600)

# 自动清理（包括过期和LRU淘汰）
cleanup_stats = cache.auto_cleanup()
```

### 2. 增量更新优化

#### 2.1 批量更新机制

```python
cache = get_quota_cache()

# 批量设置缓存
updates = {
    "account_1": quota1,
    "account_2": quota2,
    "account_3": quota3
}
cache.set_batch(updates)

# 批量删除
to_remove = {"old_account_1", "old_account_2"}
removed_count = cache.remove_batch(to_remove)
```

#### 2.2 批量更新队列

```python
# 添加到批量队列（达到批量大小时自动刷新）
cache.add_to_batch("account_1", quota1)
cache.add_to_batch("account_2", quota2)

# 手动刷新队列
cache.flush_pending_updates()
```

#### 2.3 智能保存策略

```python
# 智能保存（自动选择增量或完整保存）
cache.save_to_file()

# 强制完整保存
cache.save_to_file(force_full=True)

# 异步保存
await cache.save_to_file_async()
```

### 3. 缓存大小限制和LRU淘汰

```python
# 创建带大小限制的缓存
cache = QuotaCache(max_size=1000)

# 当缓存超过限制时，自动淘汰最久未使用的条目
for i in range(1500):
    cache.set(f"account_{i}", quota)
# 只保留最新的1000个条目
```

### 4. 性能监控

#### 4.1 缓存统计

```python
cache = get_quota_cache()
stats = cache.get_stats()

print(f"命中率: {stats.hit_rate:.2f}%")
print(f"总请求: {stats.total_requests}")
print(f"命中: {stats.hit_count}, 未命中: {stats.miss_count}")
```

#### 4.2 缓存健康检查

```python
health = cache.get_cache_health()

print(f"总条目: {health['total_entries']}")
print(f"过期条目: {health['expired_count']}")
print(f"错误条目: {health['error_count']}")
print(f"文件大小: {health['file_size_mb']:.2f} MB")
print(f"需要清理: {health['needs_cleanup']}")
```

#### 4.3 性能报告

```python
from kiro_proxy.core.quota_cache import get_cache_performance_report

report = get_cache_performance_report()

# 性能指标
perf = report["performance"]
print(f"效率评级: {perf['efficiency_grade']}")
print(f"命中率: {perf['hit_rate']:.2f}%")

# 存储信息
storage = report["storage"]
print(f"增量保存比例: {storage['save_ratio']:.2f}")

# 优化建议
for recommendation in report["recommendations"]:
    print(f"建议: {recommendation}")
```

## 配置选项

### 缓存配置常量

```python
# 默认配置
DEFAULT_CACHE_MAX_AGE = 300        # 缓存过期时间（5分钟）
DEFAULT_MAX_CACHE_SIZE = 1000      # 最大缓存条目数
DEFAULT_BATCH_SIZE = 50            # 批量更新大小
DEFAULT_CLEANUP_INTERVAL = 3600    # 自动清理间隔（1小时）
MIN_FILE_SIZE_FOR_INCREMENTAL = 10240  # 启用增量保存的最小文件大小（10KB）
```

### 自定义配置

```python
# 创建自定义配置的缓存
cache = QuotaCache(
    cache_file="/custom/path/quota_cache.json",
    max_size=2000  # 自定义最大大小
)
```

## 命令行工具

提供了 `quota_cache_manager.py` 工具来管理缓存：

```bash
# 查看缓存状态
python tools/quota_cache_manager.py status

# 清理过期缓存
python tools/quota_cache_manager.py cleanup --expired

# 自动清理
python tools/quota_cache_manager.py cleanup --auto

# 基于配置文件清理
python tools/quota_cache_manager.py cleanup --accounts-file accounts.json

# 同步配置
python tools/quota_cache_manager.py sync accounts.json

# 性能报告
python tools/quota_cache_manager.py performance

# 性能摘要
python tools/quota_cache_manager.py performance --summary

# 统计信息
python tools/quota_cache_manager.py stats

# 保存缓存
python tools/quota_cache_manager.py save

# 强制完整保存
python tools/quota_cache_manager.py save --force-full

# 测试功能
python tools/quota_cache_manager.py test
```

## 向后兼容性

所有原有的API都保持不变，新功能作为额外的方法和选项添加：

```python
# 原有API继续工作
cache = get_quota_cache()
cache.set("account_id", quota)
result = cache.get("account_id")
cache.save_to_file()

# 新增的汇总信息包含统计数据
summary = cache.get_summary()
print(summary["stats"])  # 新增的统计字段
```

## 最佳实践

### 1. 定期清理

```python
# 在应用启动时执行清理
cache = get_quota_cache()
cache.auto_cleanup()

# 定期同步配置（如果有配置文件）
if config_file_exists:
    sync_cache_with_config(config_file_path)
```

### 2. 监控性能

```python
# 定期检查性能
report = get_cache_performance_report()
if report["performance"]["hit_rate"] < 60:
    print("警告: 缓存命中率较低")

if report["maintenance"]["needs_cleanup"]:
    cache.auto_cleanup()
```

### 3. 批量操作

```python
# 对于大量更新，使用批量操作
if len(updates) > 10:
    cache.set_batch(updates)
else:
    for account_id, quota in updates.items():
        cache.set(account_id, quota)
```

### 4. 智能保存

```python
# 让系统自动选择保存策略
cache.save_to_file()  # 自动选择增量或完整保存

# 只在必要时强制完整保存
if major_changes_made:
    cache.save_to_file(force_full=True)
```

## 故障排除

### 1. 缓存文件过大

```python
# 检查文件大小
health = cache.get_cache_health()
if health["file_size_mb"] > 10:
    # 执行清理
    cache.auto_cleanup()
    # 强制完整保存以压缩文件
    cache.save_to_file(force_full=True)
```

### 2. 命中率低

```python
# 检查缓存配置
report = get_cache_performance_report()
if "缓存命中率较低" in report["recommendations"]:
    # 考虑增加缓存时间或检查查询模式
    pass
```

### 3. 内存使用过高

```python
# 检查缓存大小
health = cache.get_cache_health()
if health["usage_percent"] > 90:
    # 减少缓存大小或增加清理频率
    cache.auto_cleanup()
```

## 性能优化建议

1. **合理设置缓存大小**: 根据账号数量设置合适的 `max_size`
2. **定期清理**: 设置定时任务执行 `auto_cleanup()`
3. **使用批量操作**: 对于大量更新使用 `set_batch()` 和 `remove_batch()`
4. **监控性能**: 定期检查性能报告并根据建议优化
5. **智能保存**: 让系统自动选择最优的保存策略

## 升级说明

从旧版本升级时：

1. 现有的缓存文件会自动兼容
2. 新的统计信息会在首次保存时添加
3. 所有原有API保持不变
4. 新功能可以逐步采用

升级后建议执行一次完整清理：

```python
cache = get_quota_cache()
cache.auto_cleanup()
cache.save_to_file(force_full=True)
```