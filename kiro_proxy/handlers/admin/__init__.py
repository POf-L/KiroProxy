"""管理 API 处理"""
import json
import uuid
import time
import httpx
from pathlib import Path
from datetime import datetime
from dataclasses import asdict
from typing import Optional
from fastapi import Request, HTTPException, Query

from ...config import TOKEN_PATH, MODELS_URL
from ...core import state, Account, stats_manager, get_browsers_info, open_url, flow_monitor, get_account_usage
from ...credential import quota_manager, generate_machine_id, get_kiro_version, CredentialStatus
from ...auth import start_device_flow, poll_device_flow, cancel_device_flow, get_login_state, save_credentials_to_file
from ...auth import start_social_auth, exchange_social_auth_token, cancel_social_auth, get_social_auth_state


async def _auto_refresh_quota_for_new_accounts(accounts):
    if not accounts:
        return
    try:
        import asyncio
        from ...core import get_quota_scheduler
        scheduler = get_quota_scheduler()
        await asyncio.gather(
            *[scheduler.refresh_account(acc.id) for acc in accounts],
            return_exceptions=True,
        )
    except Exception as e:
        try:
            ids = ",".join([a.id for a in accounts])
        except Exception:
            ids = ""
        print(f"[AddAccount] 自动获取额度失败 {ids}: {e}")


async def get_status():
    """服务状态"""
    try:
        # 检查是否有可用账号
        available_count = len([a for a in state.accounts if a.enabled and a.is_available()])
        return {
            "ok": available_count > 0,
            "available_accounts": available_count,
            "total_accounts": len(state.accounts),
            "stats": state.get_stats()
        }
    except Exception as e:
        return {"ok": False, "error": str(e), "stats": state.get_stats()}


async def get_stats():
    """获取统计信息"""
    return state.get_stats()


async def event_logging_batch(request: Request):
    """接收事件日志批量上报（兼容客户端）"""
    try:
        await request.json()
    except Exception:
        pass
    return {"ok": True}


async def get_logs(limit: int = Query(100, le=1000)):
    """获取请求日志"""
    logs = list(state.request_logs)[-limit:]
    return {
        "logs": [asdict(log) for log in reversed(logs)],
        "total": len(state.request_logs)
    }


async def get_accounts():
    """获取账号列表（增强版）"""
    return {
        "accounts": state.get_accounts_status()
    }


async def get_account_detail(account_id: str):
    """获取账号详细信息"""
    for acc in state.accounts:
        if acc.id == account_id:
            creds = acc.get_credentials()
            return {
                "id": acc.id,
                "name": acc.name,
                "enabled": acc.enabled,
                "status": acc.status.value,
                "available": acc.is_available(),
                "request_count": acc.request_count,
                "error_count": acc.error_count,
                "last_used": acc.last_used,
                "token_path": acc.token_path,
                "machine_id": acc.get_machine_id()[:16] + "...",
                "credentials": {
                    "access_token": creds.access_token if creds else None,
                    "refresh_token": creds.refresh_token if creds else None,
                    "profile_arn": creds.profile_arn if creds else None,
                    "client_id": creds.client_id if creds else None,
                    "auth_method": creds.auth_method if creds else None,
                    "provider": creds.provider if creds else None,
                    "region": creds.region if creds else None,
                    "expires_at": creds.expires_at if creds else None,
                    "is_expired": acc.is_token_expired(),
                    "is_expiring_soon": acc.is_token_expiring_soon(),
                } if creds else None,
                "cooldown": {
                    "is_cooldown": not quota_manager.is_available(acc.id),
                    "remaining_seconds": quota_manager.get_cooldown_remaining(acc.id),
                }
            }
    raise HTTPException(404, "Account not found")


async def add_account(request: Request):
    """添加账号"""
    body = await request.json()
    name = body.get("name", f"账号{len(state.accounts)+1}")
    token_path = body.get("token_path")
    
    if not token_path or not Path(token_path).exists():
        raise HTTPException(400, "Invalid token path")
    
    account = Account(
        id=uuid.uuid4().hex[:8],
        name=name,
        token_path=token_path
    )
    state.accounts.append(account)
    
    # 预加载凭证
    account.load_credentials()
    
    # 保存配置
    state._save_accounts()

    await _auto_refresh_quota_for_new_accounts([account])
    
    return {"ok": True, "account_id": account.id}


async def delete_account(account_id: str):
    """删除账号"""
    # 1. 从账号列表中移除
    state.accounts = [a for a in state.accounts if a.id != account_id]

    # 2. 清理配额管理器记录
    quota_manager.restore(account_id)

    # 3. 清理额度缓存
    try:
        from ...core.quota_cache import get_quota_cache
        quota_cache = get_quota_cache()
        quota_cache.remove(account_id)
        # 异步保存缓存文件
        await quota_cache.save_to_file_async()
        print(f"[DeleteAccount] 已清理账号 {account_id} 的额度缓存")
    except Exception as e:
        print(f"[DeleteAccount] 清理额度缓存失败: {e}")

    # 4. 清理优先账号配置
    try:
        from ...core.account_selector import get_account_selector
        selector = get_account_selector()
        if selector.is_priority_account(account_id):
            success, message = selector.remove_priority_account(account_id)
            if success:
                print(f"[DeleteAccount] 已从优先账号列表移除: {account_id}")
            else:
                print(f"[DeleteAccount] 移除优先账号失败: {message}")
    except Exception as e:
        print(f"[DeleteAccount] 清理优先账号配置失败: {e}")

    # 5. 清理会话粘性缓存
    try:
        sessions_to_remove = [sid for sid, aid in state.session_locks.items() if aid == account_id]
        for sid in sessions_to_remove:
            state.session_locks.pop(sid, None)
            state.session_timestamps.pop(sid, None)
        if sessions_to_remove:
            print(f"[DeleteAccount] 已清理 {len(sessions_to_remove)} 个会话绑定")
    except Exception as e:
        print(f"[DeleteAccount] 清理会话粘性缓存失败: {e}")

    # 6. 保存账号配置
    state._save_accounts()

    print(f"[DeleteAccount] 账号 {account_id} 删除完成，已清理所有相关缓存")
    return {"ok": True}


async def update_account(account_id: str, request: Request):
    """更新账号信息
    
    支持更新：
    - name: 账号名称
    - enabled: 是否启用
    - provider: 登录提供商 (Google/Github)
    
    凭证相关字段（需要重新加载凭证）：
    - refresh_token: 刷新令牌
    - client_id: IDC 客户端 ID
    - client_secret: IDC 客户端密钥
    - region: 区域
    """
    body = await request.json()
    
    # 查找账号
    account = None
    for acc in state.accounts:
        if acc.id == account_id:
            account = acc
            break
    
    if not account:
        raise HTTPException(404, "账号不存在")
    
    updated_fields = []
    
    # 更新基本信息
    if "name" in body:
        new_name = body["name"].strip()
        if new_name:
            account.name = new_name
            updated_fields.append("name")
    
    if "enabled" in body:
        account.enabled = bool(body["enabled"])
        updated_fields.append("enabled")
    
    # 更新凭证相关字段
    creds = account.get_credentials()
    creds_updated = False
    
    if creds:
        if "provider" in body:
            provider = body["provider"].strip() if body["provider"] else None
            if provider in (None, "", "Google", "Github"):
                creds.provider = provider if provider else None
                creds_updated = True
                updated_fields.append("provider")
        
        if "refresh_token" in body:
            new_rt = body["refresh_token"].strip()
            if new_rt and len(new_rt) > 50:
                creds.refresh_token = new_rt
                creds_updated = True
                updated_fields.append("refresh_token")
        
        if "client_id" in body:
            creds.client_id = body["client_id"].strip() or None
            creds_updated = True
            updated_fields.append("client_id")
        
        if "client_secret" in body:
            creds.client_secret = body["client_secret"].strip() or None
            creds_updated = True
            updated_fields.append("client_secret")
        
        if "region" in body:
            new_region = body["region"].strip()
            if new_region:
                creds.region = new_region
                creds_updated = True
                updated_fields.append("region")
        
        # 保存凭证到文件
        if creds_updated:
            creds.save_to_file(account.token_path)
            # 重新加载凭证
            account._credentials = None
            account.load_credentials()
    
    # 保存账号配置
    state._save_accounts()
    
    return {
        "ok": True,
        "account_id": account_id,
        "updated_fields": updated_fields,
        "message": f"已更新: {', '.join(updated_fields)}" if updated_fields else "无更新"
    }


async def toggle_account(account_id: str):
    """启用/禁用账号"""
    for acc in state.accounts:
        if acc.id == account_id:
            acc.enabled = not acc.enabled
            # 手动切换时清除自动禁用标记（避免后续被自动启用覆盖手动意图）
            if hasattr(acc, "auto_disabled"):
                acc.auto_disabled = False
            # 保存配置
            state._save_accounts()
            return {"ok": True, "enabled": acc.enabled}
    raise HTTPException(404, "Account not found")


async def refresh_account_token(account_id: str):
    """刷新指定账号的 token"""
    success, message = await state.refresh_account_token(account_id)
    return {"ok": success, "message": message}


async def refresh_all_tokens():
    """刷新所有账号的 token"""
    results = []
    for acc in state.accounts:
        if acc.enabled:
            try:
                success, msg = await acc.refresh_token()
                results.append({
                    "account_id": acc.id,
                    "name": acc.name,
                    "success": success,
                    "message": msg
                })
            except Exception as e:
                results.append({
                    "account_id": acc.id,
                    "name": acc.name,
                    "success": False,
                    "message": str(e)
                })
    
    refreshed_count = len([r for r in results if r["success"]])
    return {
        "ok": True,
        "results": results,
        "refreshed": refreshed_count,
        "total": len(results)
    }


async def restore_account(account_id: str):
    """恢复账号（从冷却状态）"""
    restored = quota_manager.restore(account_id)
    if restored:
        for acc in state.accounts:
            if acc.id == account_id:
                from ...credential import CredentialStatus
                acc.status = CredentialStatus.ACTIVE
                break
    return {"ok": restored}


async def speedtest():
    """测试 API 延迟"""
    account = state.get_available_account()
    if not account:
        return {"ok": False, "error": "No available account"}
    
    start = time.time()
    try:
        token = account.get_token()
        machine_id = account.get_machine_id()
        kiro_version = get_kiro_version()
        
        headers = {
            "content-type": "application/json",
            "x-amz-user-agent": f"aws-sdk-js/1.0.0 KiroIDE-{kiro_version}-{machine_id}",
            "Authorization": f"Bearer {token}",
        }
        async with httpx.AsyncClient(verify=False, timeout=10) as client:
            resp = await client.get(MODELS_URL, headers=headers, params={"origin": "AI_EDITOR"})
            latency = (time.time() - start) * 1000
            return {
                "ok": resp.status_code == 200,
                "latency_ms": round(latency, 2),
                "status": resp.status_code,
                "account_id": account.id
            }
    except Exception as e:
        return {"ok": False, "error": str(e), "latency_ms": (time.time() - start) * 1000}


async def test_account_token(account_id: str):
    """测试指定账号的 Token 是否有效
    
    测试内容：
    1. Token 是否存在
    2. Token 是否过期
    3. 调用 Kiro API 验证 Token 有效性
    4. 获取用户邮箱（验证 Token 权限）
    
    Returns:
        测试结果，包含各项检查状态
    """
    # 查找账号
    account = None
    for acc in state.accounts:
        if acc.id == account_id:
            account = acc
            break
    
    if not account:
        return {"ok": False, "error": "账号不存在"}
    
    result = {
        "ok": True,
        "account_id": account_id,
        "account_name": account.name,
        "tests": {}
    }
    
    # 1. 检查 Token 是否存在
    token = account.get_token()
    result["tests"]["token_exists"] = {
        "passed": bool(token),
        "message": "Token 存在" if token else "Token 不存在"
    }
    
    if not token:
        result["ok"] = False
        return result
    
    # 2. 检查 Token 是否过期
    creds = account.get_credentials()
    is_expired = account.is_token_expired()
    is_expiring_soon = account.is_token_expiring_soon(10)
    
    result["tests"]["token_expiry"] = {
        "passed": not is_expired,
        "message": "Token 已过期" if is_expired else ("Token 即将过期" if is_expiring_soon else "Token 有效期正常"),
        "expires_at": creds.expires_at if creds else None,
        "is_expiring_soon": is_expiring_soon
    }
    
    if is_expired:
        result["ok"] = False
        result["tests"]["token_expiry"]["suggestion"] = "请刷新 Token"
    
    # 3. 调用 Kiro API 验证 Token
    start = time.time()
    try:
        machine_id = account.get_machine_id()
        kiro_version = get_kiro_version()
        
        headers = {
            "content-type": "application/json",
            "x-amz-user-agent": f"aws-sdk-js/1.0.0 KiroIDE-{kiro_version}-{machine_id}",
            "Authorization": f"Bearer {token}",
        }
        async with httpx.AsyncClient(verify=False, timeout=15) as client:
            resp = await client.get(MODELS_URL, headers=headers, params={"origin": "AI_EDITOR"})
            latency = (time.time() - start) * 1000
            
            api_ok = resp.status_code == 200
            result["tests"]["api_call"] = {
                "passed": api_ok,
                "message": "API 调用成功" if api_ok else f"API 调用失败 (HTTP {resp.status_code})",
                "status_code": resp.status_code,
                "latency_ms": round(latency, 2)
            }
            
            if resp.status_code == 401:
                result["tests"]["api_call"]["suggestion"] = "Token 无效或已过期，请刷新 Token"
                result["ok"] = False
            elif resp.status_code == 429:
                result["tests"]["api_call"]["suggestion"] = "请求过于频繁，请稍后再试"
            elif resp.status_code == 403:
                result["tests"]["api_call"]["suggestion"] = "账号可能已被封禁"
                result["ok"] = False
            elif not api_ok:
                result["ok"] = False
                
    except httpx.TimeoutException:
        result["tests"]["api_call"] = {
            "passed": False,
            "message": "API 调用超时",
            "suggestion": "网络连接问题，请检查网络"
        }
        result["ok"] = False
    except Exception as e:
        result["tests"]["api_call"] = {
            "passed": False,
            "message": f"API 调用异常: {str(e)}",
        }
        result["ok"] = False
    
    # 4. 尝试获取用户邮箱（验证 Token 权限）
    try:
        email = await _get_user_email(creds)
        result["tests"]["get_email"] = {
            "passed": bool(email),
            "message": f"获取邮箱成功: {email}" if email else "无法获取邮箱",
            "email": email
        }
    except Exception as e:
        result["tests"]["get_email"] = {
            "passed": False,
            "message": f"获取邮箱失败: {str(e)}"
        }
    
    # 汇总结果
    passed_count = sum(1 for t in result["tests"].values() if t.get("passed"))
    total_count = len(result["tests"])
    result["summary"] = f"{passed_count}/{total_count} 项测试通过"
    
    return result


async def scan_tokens():
    """扫描系统中的 Kiro token 文件"""
    from ...config import TOKEN_DIR
    
    found = []
    
    # 扫描新目录
    if TOKEN_DIR.exists():
        for f in TOKEN_DIR.glob("*.json"):
            try:
                with open(f) as fp:
                    data = json.load(fp)
                    if "accessToken" in data:
                        # 检查是否已添加
                        already_added = any(a.token_path == str(f) for a in state.accounts)
                        
                        auth_method = data.get("authMethod", "social")
                        client_id_hash = data.get("clientIdHash")
                        
                        # 检查 IdC 配置完整性
                        idc_complete = None
                        if auth_method == "idc" and client_id_hash:
                            hash_file = TOKEN_DIR / f"{client_id_hash}.json"
                            if hash_file.exists():
                                try:
                                    with open(hash_file) as hf:
                                        hash_data = json.load(hf)
                                        idc_complete = bool(hash_data.get("clientId") and hash_data.get("clientSecret"))
                                except:
                                    idc_complete = False
                            else:
                                idc_complete = False
                        
                        found.append({
                            "path": str(f),
                            "name": f.stem,
                            "expires": data.get("expiresAt"),
                            "auth_method": auth_method,
                            "region": data.get("region", "us-east-1"),
                            "has_refresh_token": "refreshToken" in data,
                            "already_added": already_added,
                            "idc_config_complete": idc_complete,
                        })
            except:
                pass
    
    # 兼容：也扫描旧的 AWS SSO 目录
    sso_cache = Path.home() / ".aws/sso/cache"
    if sso_cache.exists():
        for f in sso_cache.glob("*.json"):
            try:
                with open(f) as fp:
                    data = json.load(fp)
                    if "accessToken" in data:
                        already_added = any(a.token_path == str(f) for a in state.accounts)
                        auth_method = data.get("authMethod", "social")
                        
                        found.append({
                            "path": str(f),
                            "name": f.stem + " (旧目录)",
                            "expires": data.get("expiresAt"),
                            "auth_method": auth_method,
                            "region": data.get("region", "us-east-1"),
                            "has_refresh_token": "refreshToken" in data,
                            "already_added": already_added,
                            "idc_config_complete": None,
                        })
            except:
                pass
    
    return {"tokens": found}


async def add_from_scan(request: Request):
    """从扫描结果添加账号"""
    body = await request.json()
    token_path = body.get("path")
    name = body.get("name", "扫描账号")
    
    if not token_path or not Path(token_path).exists():
        raise HTTPException(400, "Token 文件不存在")
    
    if any(a.token_path == token_path for a in state.accounts):
        raise HTTPException(400, "该账号已添加")
    
    try:
        with open(token_path) as f:
            data = json.load(f)
            if "accessToken" not in data:
                raise HTTPException(400, "无效的 token 文件")
    except json.JSONDecodeError:
        raise HTTPException(400, "无效的 JSON 文件")
    
    account = Account(
        id=uuid.uuid4().hex[:8],
        name=name,
        token_path=token_path
    )
    state.accounts.append(account)
    
    # 预加载凭证
    account.load_credentials()
    
    # 保存配置
    state._save_accounts()
    
    await _auto_refresh_quota_for_new_accounts([account])
    
    return {"ok": True, "account_id": account.id}


async def export_config():
    """导出配置"""
    return {
        "accounts": [
            {"name": a.name, "token_path": a.token_path, "enabled": a.enabled}
            for a in state.accounts
        ],
        "exported_at": datetime.now().isoformat()
    }


async def import_config(request: Request):
    """导入配置"""
    body = await request.json()
    accounts = body.get("accounts", [])
    imported = 0
    new_accounts = []
     
    for acc_data in accounts:
        token_path = acc_data.get("token_path", "")
        if Path(token_path).exists():
            if not any(a.token_path == token_path for a in state.accounts):
                account = Account(
                    id=uuid.uuid4().hex[:8],
                    name=acc_data.get("name", "导入账号"),
                    token_path=token_path,
                    enabled=acc_data.get("enabled", True)
                 )
                state.accounts.append(account)
                account.load_credentials()
                new_accounts.append(account)
                imported += 1
     
    # 保存配置
    state._save_accounts()
     
    await _auto_refresh_quota_for_new_accounts(new_accounts)
     
    return {"ok": True, "imported": imported}


async def refresh_token_check():
    """检查所有账号的 token 状态"""
    results = []
    for acc in state.accounts:
        creds = acc.get_credentials()
        if creds:
            results.append({
                "id": acc.id,
                "name": acc.name,
                "valid": not acc.is_token_expired(),
                "expiring_soon": acc.is_token_expiring_soon(),
                "expires": creds.expires_at,
                "auth_method": creds.auth_method,
                "has_refresh_token": bool(creds.refresh_token),
            })
        else:
            results.append({
                "id": acc.id,
                "name": acc.name,
                "valid": False,
                "error": "无法加载凭证"
            })
    
    return {"accounts": results}


async def get_quota_status():
    """获取配额状态"""
    return {
        "cooldown_seconds": quota_manager.COOLDOWN_SECONDS,
        "exceeded_count": len(quota_manager.exceeded_records),
        "exceeded_credentials": [
            {
                "credential_id": r.credential_id,
                "exceeded_at": r.exceeded_at,
                "cooldown_until": r.cooldown_until,
                "remaining_seconds": max(0, int(r.cooldown_until - time.time())),
                "reason": r.reason
            }
            for r in quota_manager.exceeded_records.values()
        ]
    }


async def get_kiro_login_url():
    """获取 Kiro 登录说明"""
    from ...config import TOKEN_DIR
    return {
        "message": "请使用本代理的登录功能，或从 Kiro IDE 导入 token",
        "instructions": [
            "1. 点击「添加」按钮，选择登录方式",
            "2. 或者从 Kiro IDE 的 ~/.aws/sso/cache/ 复制 token 文件",
            "3. 将 token 文件放到 ~/.kiro-proxy/tokens/ 目录",
            "4. 点击「扫描」按钮自动识别"
        ],
        "token_dir": str(TOKEN_DIR),
        "token_dir_exists": TOKEN_DIR.exists()
    }


async def get_detailed_stats():
    """获取详细统计信息"""
    basic_stats = state.get_stats()
    detailed = stats_manager.get_all_stats()
    
    return {
        **basic_stats,
        "detailed": detailed
    }


async def run_health_check():
    """手动触发健康检查"""
    results = []
    
    for acc in state.accounts:
        if not acc.enabled:
            results.append({
                "id": acc.id,
                "name": acc.name,
                "status": "disabled",
                "healthy": False
            })
            continue
        
        try:
            token = acc.get_token()
            if not token:
                acc.status = CredentialStatus.UNHEALTHY
                results.append({
                    "id": acc.id,
                    "name": acc.name,
                    "status": "no_token",
                    "healthy": False
                })
                continue
            
            headers = {
                "Authorization": f"Bearer {token}",
                "content-type": "application/json"
            }
            
            async with httpx.AsyncClient(verify=False, timeout=10) as client:
                resp = await client.get(
                    MODELS_URL,
                    headers=headers,
                    params={"origin": "AI_EDITOR"}
                )
                
                if resp.status_code == 200:
                    if acc.status == CredentialStatus.UNHEALTHY:
                        acc.status = CredentialStatus.ACTIVE
                    results.append({
                        "id": acc.id,
                        "name": acc.name,
                        "status": "healthy",
                        "healthy": True,
                        "latency_ms": resp.elapsed.total_seconds() * 1000
                    })
                elif resp.status_code == 401:
                    acc.status = CredentialStatus.UNHEALTHY
                    results.append({
                        "id": acc.id,
                        "name": acc.name,
                        "status": "auth_failed",
                        "healthy": False
                    })
                elif resp.status_code == 429:
                    results.append({
                        "id": acc.id,
                        "name": acc.name,
                        "status": "rate_limited",
                        "healthy": True  # 限流不代表不健康
                    })
                else:
                    results.append({
                        "id": acc.id,
                        "name": acc.name,
                        "status": f"error_{resp.status_code}",
                        "healthy": False
                    })
                    
        except Exception as e:
            results.append({
                "id": acc.id,
                "name": acc.name,
                "status": "error",
                "healthy": False,
                "error": str(e)
            })
    
    healthy_count = len([r for r in results if r["healthy"]])
    return {
        "ok": True,
        "total": len(results),
        "healthy": healthy_count,
        "unhealthy": len(results) - healthy_count,
        "results": results
    }


# ==================== Kiro 登录 API ====================

async def get_browsers():
    """获取可用浏览器列表"""
    return {"browsers": get_browsers_info()}


async def start_kiro_login(request: Request):
    """启动 Kiro 设备授权登录"""
    body = await request.json() if request.headers.get("content-type") == "application/json" else {}
    region = body.get("region", "us-east-1")
    
    success, result = await start_device_flow(region)
    
    if success:
        return {
            "ok": True,
            "user_code": result["user_code"],
            "verification_uri": result["verification_uri"],
            "expires_in": result["expires_in"],
            "interval": result["interval"],
        }
    else:
        return {"ok": False, "error": result.get("error", "未知错误")}


async def poll_kiro_login():
    """轮询 Kiro 登录状态"""
    success, result = await poll_device_flow()
    
    if not success:
        return {"ok": False, "error": result.get("error", "未知错误")}
    
    if result.get("completed"):
        # 授权完成，保存凭证并添加账号
        credentials = result["credentials"]
        
        # 保存到文件
        from ...auth.device_flow import save_credentials_to_file
        file_path = await save_credentials_to_file(credentials)
        
        # 尝试获取邮箱作为账号名称
        account_name = "在线登录账号"
        try:
            from ...credential import KiroCredentials
            creds = KiroCredentials(
                access_token=credentials.get("accessToken"),
                refresh_token=credentials.get("refreshToken"),
                auth_method=credentials.get("authMethod", "idc"),
            )
            email = await _get_user_email(creds)
            if email:
                account_name = email
        except Exception as e:
            print(f"[DeviceFlow] 获取邮箱失败: {e}")
        
        # 添加账号
        account = Account(
            id=uuid.uuid4().hex[:8],
            name=account_name,
            token_path=file_path
        )
        state.accounts.append(account)
        account.load_credentials()
        state._save_accounts()
        
        await _auto_refresh_quota_for_new_accounts([account])
        
        return {
            "ok": True,
            "completed": True,
            "account_id": account.id,
            "message": "登录成功，账号已添加"
        }
    else:
        return {
            "ok": True,
            "completed": False,
            "status": result.get("status", "pending")
        }


async def cancel_kiro_login():
    """取消 Kiro 登录"""
    cancelled = cancel_device_flow()
    return {"ok": cancelled}


async def get_kiro_login_status():
    """获取当前登录状态"""
    login_state = get_login_state()
    if login_state:
        return {
            "ok": True,
            "in_progress": True,
            **login_state
        }
    else:
        return {"ok": True, "in_progress": False}


# ==================== Social Auth API (Google/GitHub) ====================

async def start_social_login(request: Request):
    """启动 Social Auth 登录 (Google/GitHub)"""
    body = await request.json() if request.headers.get("content-type") == "application/json" else {}
    provider = body.get("provider", "google")
    
    success, result = await start_social_auth(provider)
    
    if success:
        return {
            "ok": True,
            "provider": result["provider"],
            "login_url": result["login_url"],
            "state": result["state"],
        }
    else:
        return {"ok": False, "error": result.get("error", "未知错误")}


async def exchange_social_token(request: Request):
    """交换 Social Auth Token"""
    body = await request.json()
    code = body.get("code")
    oauth_state = body.get("state")
    
    if not code or not oauth_state:
        return {"ok": False, "error": "缺少 code 或 state"}
    
    success, result = await exchange_social_auth_token(code, oauth_state)
    
    if not success:
        return {"ok": False, "error": result.get("error", "未知错误")}
    
    if result.get("completed"):
        # 保存凭证并添加账号
        credentials = result["credentials"]
        provider = result.get("provider", "Social")
        
        # 保存到文件
        from ...auth.device_flow import save_credentials_to_file
        file_path = await save_credentials_to_file(credentials, f"kiro-{provider.lower()}-auth")
        
        # 尝试获取邮箱作为账号名称
        account_name = f"{provider} 登录账号"
        try:
            from ...credential import KiroCredentials
            creds = KiroCredentials(
                access_token=credentials.get("accessToken"),
                refresh_token=credentials.get("refreshToken"),
                provider=provider,
            )
            email = await _get_user_email(creds)
            if email:
                account_name = email
        except Exception as e:
            print(f"[SocialAuth] 获取邮箱失败: {e}")
        
        # 添加账号
        account = Account(
            id=uuid.uuid4().hex[:8],
            name=account_name,
            token_path=file_path
        )
        state.accounts.append(account)
        account.load_credentials()
        state._save_accounts()
        
        await _auto_refresh_quota_for_new_accounts([account])
        
        return {
            "ok": True,
            "completed": True,
            "account_id": account.id,
            "provider": provider,
            "message": f"{provider} 登录成功，账号已添加"
        }
    
    return {"ok": False, "error": "Token 交换失败"}


async def cancel_social_login():
    """取消 Social Auth 登录"""
    cancelled = cancel_social_auth()
    return {"ok": cancelled}


async def get_social_login_status():
    """获取 Social Auth 状态"""
    auth_state = get_social_auth_state()
    if auth_state:
        return {
            "ok": True,
            "in_progress": True,
            **auth_state
        }
    else:
        return {"ok": True, "in_progress": False}


# ==================== Flow Monitor API ====================

async def get_flows(
    protocol: str = None,
    model: str = None,
    account_id: str = None,
    state_filter: str = None,
    has_error: bool = None,
    bookmarked: bool = None,
    search: str = None,
    limit: int = 50,
    offset: int = 0,
):
    """查询 Flows"""
    from ...core.flow_monitor import FlowState
    
    state_enum = None
    if state_filter:
        try:
            state_enum = FlowState(state_filter)
        except ValueError:
            pass
    
    flows = flow_monitor.query(
        protocol=protocol,
        model=model,
        account_id=account_id,
        state=state_enum,
        has_error=has_error,
        bookmarked=bookmarked,
        search=search,
        limit=limit,
        offset=offset,
    )
    
    return {
        "flows": [f.to_dict() for f in flows],
        "total": len(flows),
    }


async def get_flow_detail(flow_id: str):
    """获取 Flow 详情"""
    flow = flow_monitor.get_flow(flow_id)
    if not flow:
        raise HTTPException(404, "Flow not found")
    return flow.to_full_dict()


async def get_flow_stats():
    """获取 Flow 统计"""
    return flow_monitor.get_stats()


async def bookmark_flow(flow_id: str, request: Request):
    """书签 Flow"""
    body = await request.json()
    bookmarked = body.get("bookmarked", True)
    flow_monitor.bookmark_flow(flow_id, bookmarked)
    return {"ok": True}


async def add_flow_note(flow_id: str, request: Request):
    """添加 Flow 备注"""
    body = await request.json()
    note = body.get("note", "")
    flow_monitor.add_note(flow_id, note)
    return {"ok": True}


async def add_flow_tag(flow_id: str, request: Request):
    """添加 Flow 标签"""
    body = await request.json()
    tag = body.get("tag", "")
    if tag:
        flow_monitor.add_tag(flow_id, tag)
    return {"ok": True}


async def export_flows(request: Request):
    """导出 Flows"""
    body = await request.json()
    flow_ids = body.get("flow_ids", [])
    format = body.get("format", "json")
    
    content = flow_monitor.export(flow_ids if flow_ids else None, format)
    return {"content": content, "format": format}


# ==================== Usage API ====================

async def get_account_usage_info(account_id: str):
    """获取账号用量信息"""
    for acc in state.accounts:
        if acc.id == account_id:
            success, result = await get_account_usage(acc)
            if success:
                return {
                    "ok": True,
                    "account_id": account_id,
                    "account_name": acc.name,
                    "usage": {
                        "subscription_title": result.subscription_title,
                        "usage_limit": result.usage_limit,
                        "current_usage": result.current_usage,
                        "balance": result.balance,
                        "is_low_balance": result.is_low_balance,
                        "free_trial_limit": result.free_trial_limit,
                        "free_trial_usage": result.free_trial_usage,
                        "bonus_limit": result.bonus_limit,
                        "bonus_usage": result.bonus_usage,
                    }
                }
            else:
                return {"ok": False, "error": result.get("error", "查询失败")}
    raise HTTPException(404, "Account not found")


# ==================== 账号导入导出 API ====================

async def export_accounts():
    """导出所有账号配置（包含 token）"""
    accounts_data = []
    for acc in state.accounts:
        creds = acc.get_credentials()
        if creds:
            accounts_data.append({
                "name": acc.name,
                "enabled": acc.enabled,
                "credentials": {
                    "accessToken": creds.access_token,
                    "refreshToken": creds.refresh_token,
                    "expiresAt": creds.expires_at,
                    "region": creds.region or "us-east-1",
                    "authMethod": creds.auth_method or "social",
                    "clientId": creds.client_id,
                    "clientSecret": creds.client_secret,
                }
            })
    return {
        "ok": True,
        "accounts": accounts_data,
        "exported_at": datetime.now().isoformat(),
        "version": "1.0"
    }


async def import_accounts(request: Request):
    """导入账号配置
    
    支持：
    - Refresh Token 必填，Access Token 可选
    - 账号名可选（可自动获取邮箱）
    - 根据 Refresh Token 去重
    """
    body = await request.json()
    accounts_data = body.get("accounts", [])
    imported = 0
    new_accounts = []
    
    for acc_data in accounts_data:
        token_path = acc_data.get("token_path", "")
        if Path(token_path).exists():
            if not any(a.token_path == token_path for a in state.accounts):
                account = Account(
                    id=uuid.uuid4().hex[:8],
                    name=acc_data.get("name", "导入账号"),
                    token_path=token_path,
                    enabled=acc_data.get("enabled", True)
                )
                state.accounts.append(account)
                account.load_credentials()
                new_accounts.append(account)
                imported += 1
    
    # 保存配置
    state._save_accounts()
    
    await _auto_refresh_quota_for_new_accounts(new_accounts)
    
    return {"ok": True, "imported": imported}


async def refresh_token_check():
    """检查所有账号的 token 状态"""
    results = []
    for acc in state.accounts:
        creds = acc.get_credentials()
        if creds:
            results.append({
                "id": acc.id,
                "name": acc.name,
                "valid": not acc.is_token_expired(),
                "expiring_soon": acc.is_token_expiring_soon(),
                "expires": creds.expires_at,
                "auth_method": creds.auth_method,
                "has_refresh_token": bool(creds.refresh_token),
            })
        else:
            results.append({
                "id": acc.id,
                "name": acc.name,
                "valid": False,
                "error": "无法加载凭证"
            })
    
    return {"accounts": results}


async def add_manual_token(request: Request):
    """手动添加 Token
    
    支持：
    - Refresh Token 必填，Access Token 可选（可通过 Refresh Token 获取）
    - 账号名可选（可自动获取邮箱作为名称）
    - 根据 Refresh Token 去重
    - 支持 authMethod: social/idc
    - 支持 provider: Google/Github (社交登录)
    - 支持 clientId/clientSecret (IDC 认证)
    """
    body = await request.json()
    access_token = body.get("access_token", "").strip()
    refresh_token = body.get("refresh_token", "").strip()
    name = body.get("name", "").strip()
    region = body.get("region", "us-east-1")
    auth_method = body.get("auth_method", "social")
    provider = body.get("provider", "").strip()  # Google/Github
    client_id = body.get("client_id", "").strip()
    client_secret = body.get("client_secret", "").strip()
    
    # Refresh Token 必填
    if not refresh_token:
        raise HTTPException(400, "缺少 refresh_token（必填）")
    
    # IDC 认证需要 clientId 和 clientSecret
    if auth_method == "idc" and (not client_id or not client_secret):
        raise HTTPException(400, "IDC 认证需要 client_id 和 client_secret")
    
    # 检查 Refresh Token 是否已存在（去重）
    for acc in state.accounts:
        creds = acc.get_credentials()
        if creds and creds.refresh_token == refresh_token:
            raise HTTPException(400, f"该 Refresh Token 已存在，对应账号: {acc.name} ({acc.id})")
    
    # 构建凭证对象
    from ...credential import KiroCredentials, TokenRefresher
    
    creds = KiroCredentials(
        access_token=access_token if access_token else None,
        refresh_token=refresh_token,
        region=region,
        auth_method=auth_method,
        provider=provider if provider else None,
        client_id=client_id if client_id else None,
        client_secret=client_secret if client_secret else None,
    )
    
    # 如果没有 Access Token，通过 Refresh Token 获取
    if not access_token:
        refresher = TokenRefresher(creds)
        success, result = await refresher.refresh()
        if not success:
            raise HTTPException(400, f"无法通过 Refresh Token 获取 Access Token: {result}")
        # refresh 成功后 creds.access_token 已更新
    
    # 如果没有提供名称，尝试获取邮箱作为名称
    auto_name = None
    if not name:
        try:
            email = await _get_user_email(creds)
            if email:
                auto_name = email
        except Exception as e:
            print(f"[AddAccount] 获取邮箱失败: {e}")
    
    final_name = name or auto_name or "手动添加账号"
    
    # 构建保存的凭证数据
    creds_data = {
        "accessToken": creds.access_token,
        "refreshToken": creds.refresh_token,
        "expiresAt": creds.expires_at,
        "region": region,
        "authMethod": auth_method,
        "profileArn": creds.profile_arn,
    }
    
    # 添加 provider 字段（社交登录）
    if provider:
        creds_data["provider"] = provider
    
    # 添加 IDC 认证字段
    if client_id:
        creds_data["clientId"] = client_id
    if client_secret:
        creds_data["clientSecret"] = client_secret
    
    # 保存凭证到文件
    file_path = await save_credentials_to_file(creds_data, f"manual-{uuid.uuid4().hex[:8]}")
    
    # 添加账号
    account = Account(
        id=uuid.uuid4().hex[:8],
        name=final_name,
        token_path=file_path
    )
    state.accounts.append(account)
    account.load_credentials()
    state._save_accounts()
    
    await _auto_refresh_quota_for_new_accounts([account])
    
    return {
        "ok": True, 
        "account_id": account.id,
        "name": final_name,
        "auto_name": auto_name is not None
    }


async def batch_import_accounts(request: Request):
    """批量导入账号
    
    接收 JSON 数组，每个元素包含：
    - refresh_token: 必填
    - access_token: 可选
    - name: 可选（自动获取邮箱）
    - auth_method: 可选，默认 social
    - provider: 可选 (Google/Github)
    - client_id, client_secret: IDC 认证需要
    - region: 可选，默认 us-east-1
    
    返回导入结果统计
    """
    body = await request.json()
    accounts_data = body.get("accounts", [])
    
    if not accounts_data:
        raise HTTPException(400, "accounts 数组为空")
    
    results = {
        "total": len(accounts_data),
        "success": 0,
        "skipped": 0,
        "failed": 0,
        "details": []
    }
    
    new_accounts = []
    
    # 获取现有 refresh_token 集合（去重）
    existing_refresh_tokens = set()
    for acc in state.accounts:
        creds = acc.get_credentials()
        if creds and creds.refresh_token:
            existing_refresh_tokens.add(creds.refresh_token)
    
    from ...credential import KiroCredentials, TokenRefresher
    
    for i, acc_data in enumerate(accounts_data):
        try:
            refresh_token = acc_data.get("refresh_token", "").strip()
            access_token = acc_data.get("access_token", "").strip()
            name = acc_data.get("name", "").strip()
            auth_method = acc_data.get("auth_method", "social")
            provider = acc_data.get("provider", "").strip()
            client_id = acc_data.get("client_id", "").strip()
            client_secret = acc_data.get("client_secret", "").strip()
            region = acc_data.get("region", "us-east-1")
            
            # 验证必填字段
            if not refresh_token:
                results["failed"] += 1
                results["details"].append({"index": i, "status": "failed", "error": "缺少 refresh_token"})
                continue
            
            # 去重检查
            if refresh_token in existing_refresh_tokens:
                results["skipped"] += 1
                results["details"].append({"index": i, "status": "skipped", "error": "refresh_token 已存在"})
                continue
            
            # IDC 认证验证
            if auth_method == "idc" and (not client_id or not client_secret):
                results["failed"] += 1
                results["details"].append({"index": i, "status": "failed", "error": "IDC 认证需要 client_id 和 client_secret"})
                continue
            
            # 构建凭证
            creds = KiroCredentials(
                access_token=access_token if access_token else None,
                refresh_token=refresh_token,
                region=region,
                auth_method=auth_method,
                provider=provider if provider else None,
                client_id=client_id if client_id else None,
                client_secret=client_secret if client_secret else None,
            )
            
            # 如果没有 access_token，尝试刷新获取
            if not access_token:
                refresher = TokenRefresher(creds)
                success, result = await refresher.refresh()
                if not success:
                    results["failed"] += 1
                    results["details"].append({"index": i, "status": "failed", "error": f"Token 刷新失败: {result}"})
                    continue
            
            # 获取邮箱作为名称
            final_name = name
            if not final_name:
                try:
                    email = await _get_user_email(creds)
                    if email:
                        final_name = email
                except Exception:
                    pass
            final_name = final_name or f"批量导入账号 {i+1}"
            
            # 保存凭证
            creds_data = {
                "accessToken": creds.access_token,
                "refreshToken": creds.refresh_token,
                "expiresAt": creds.expires_at,
                "region": region,
                "authMethod": auth_method,
                "profileArn": creds.profile_arn,
            }
            if provider:
                creds_data["provider"] = provider
            if client_id:
                creds_data["clientId"] = client_id
            if client_secret:
                creds_data["clientSecret"] = client_secret
            
            file_path = await save_credentials_to_file(creds_data, f"batch-{uuid.uuid4().hex[:8]}")
            
            # 添加账号
            account = Account(
                id=uuid.uuid4().hex[:8],
                name=final_name,
                token_path=file_path
            )
            state.accounts.append(account)
            account.load_credentials()
            new_accounts.append(account)
            
            # 添加到已存在集合
            existing_refresh_tokens.add(refresh_token)
            
            results["success"] += 1
            results["details"].append({"index": i, "status": "success", "account_id": account.id, "name": final_name})
            
        except Exception as e:
            results["failed"] += 1
            results["details"].append({"index": i, "status": "failed", "error": str(e)})
    
    # 保存配置
    state._save_accounts()
    
    await _auto_refresh_quota_for_new_accounts(new_accounts)
    
    return {
        "ok": True,
        **results
    }


async def _get_user_email(creds: 'KiroCredentials') -> Optional[str]:
    """通过 Kiro API 获取用户邮箱"""
    from ...core.kiro_api import get_user_email
    
    if not creds.access_token:
        return None
    
    # 获取 provider
    provider = creds.provider or "Google"
    
    try:
        email = await get_user_email(creds.access_token, provider)
        if email:
            print(f"[GetUserEmail] 成功获取邮箱: {email}")
            return email
    except Exception as e:
        print(f"[GetUserEmail] 请求失败: {e}")
    
    return None


# ==================== 额度管理 API ====================

async def get_accounts_status_enhanced():
    """获取完整账号状态（增强版）"""
    return {
        "ok": True,
        "summary": state.get_accounts_summary(),
        "accounts": state.get_accounts_status()
    }


async def refresh_account_quota(account_id: str):
    """刷新单个账号额度"""
    from ...core import get_quota_scheduler
    scheduler = get_quota_scheduler()
    
    success = await scheduler.refresh_account(account_id)
    
    if success:
        return {"ok": True, "message": f"账号 {account_id} 额度刷新成功"}
    else:
        return {"ok": False, "error": f"账号 {account_id} 额度刷新失败"}


async def refresh_all_quotas():
    """刷新所有账号额度"""
    from ...core import get_quota_scheduler
    scheduler = get_quota_scheduler()
    
    results = await scheduler.refresh_all()
    
    success_count = sum(1 for v in results.values() if v)
    fail_count = len(results) - success_count
    
    return {
        "ok": True,
        "results": results,
        "success_count": success_count,
        "fail_count": fail_count
    }


# ==================== 优先账号 API ====================

async def get_priority_accounts():
    """获取优先账号列表"""
    from ...core import get_account_selector
    selector = get_account_selector()
    
    priority_ids = selector.get_priority_accounts()
    
    # 获取账号详情
    priority_accounts = []
    for pid in priority_ids:
        for acc in state.accounts:
            if acc.id == pid:
                priority_accounts.append({
                    "id": acc.id,
                    "name": acc.name,
                    "enabled": acc.enabled,
                    "available": acc.is_available(),
                    "order": selector.get_priority_order(acc.id)
                })
                break
    
    return {
        "ok": True,
        "priority_accounts": priority_accounts,
        "strategy": selector.strategy.value
    }


async def set_priority_account(account_id: str, request: Request):
    """设置优先账号"""
    from ...core import get_account_selector
    selector = get_account_selector()
    
    body = await request.json() if request.headers.get("content-type") == "application/json" else {}
    position = body.get("position", -1)
    
    valid_ids = state.get_valid_account_ids()
    success, message = selector.add_priority_account(account_id, position, valid_ids)
    
    return {"ok": success, "message": message}


async def remove_priority_account(account_id: str):
    """取消优先账号"""
    from ...core import get_account_selector
    selector = get_account_selector()
    
    success, message = selector.remove_priority_account(account_id)
    
    return {"ok": success, "message": message}


async def reorder_priority_accounts(request: Request):
    """调整优先账号顺序"""
    from ...core import get_account_selector
    selector = get_account_selector()
    
    body = await request.json()
    account_ids = body.get("account_ids", [])
    
    success, message = selector.reorder_priority(account_ids)
    
    return {"ok": success, "message": message}


# ==================== 汇总统计 API ====================

async def get_accounts_summary():
    """获取账号汇总统计"""
    return {
        "ok": True,
        "summary": state.get_accounts_summary()
    }


# ==================== 刷新进度 API ====================

async def get_refresh_progress():
    """获取刷新进度"""
    from ...core import get_refresh_manager
    manager = get_refresh_manager()
    
    progress = manager.get_progress_dict()
    is_refreshing = manager.is_refreshing()
    
    if progress:
        return {
            "ok": True,
            "is_refreshing": is_refreshing,
            "progress": progress,
            "progress_percent": progress.get("total", 0) and round(
                (progress.get("completed", 0) / progress.get("total", 1)) * 100, 2
            )
        }
    else:
        return {
            "ok": True,
            "is_refreshing": is_refreshing,
            "progress": None,
            "message": "没有进行中的刷新操作"
        }


async def refresh_all_with_progress():
    """批量刷新（带进度和锁检查）
    
    使用 RefreshManager 进行批量刷新，支持：
    - 全局锁防止重复刷新
    - 进度跟踪
    - 自动刷新 Token
    - 重试机制
    
    注意：刷新操作在后台执行，API 立即返回，前端通过轮询获取进度。
    """
    import asyncio
    from ...core import get_refresh_manager, get_account_usage
    manager = get_refresh_manager()
    
    # 检查是否已有刷新在进行
    if manager.is_refreshing():
        progress = manager.get_progress_dict()
        return {
            "ok": False,
            "error": "刷新操作正在进行中",
            "progress": progress
        }
    
    # 定义获取额度的函数
    async def get_quota_func(account):
        """获取账号额度"""
        success, result = await get_account_usage(account)
        if success:
            # 更新额度缓存
            from ...core import get_quota_cache
            from ...core.quota_cache import CachedQuota
            quota_cache = get_quota_cache()
            cached_quota = CachedQuota.from_usage_info(account.id, result)
            quota_cache.set(account.id, cached_quota)
            
            # 自动启用/禁用账号
            if cached_quota.is_exhausted:
                # 额度用尽，自动禁用
                if account.enabled:
                    account.enabled = False
                    if hasattr(account, "auto_disabled"):
                        account.auto_disabled = True
                    print(f"[RefreshManager] 账号 {account.id} ({account.name}) 额度已用尽，自动禁用")
            else:
                # 有额度，自动启用（仅对自动禁用的账号生效）
                if (not account.enabled) and getattr(account, "auto_disabled", False):
                    account.enabled = True
                    account.auto_disabled = False
                    print(f"[RefreshManager] 账号 {account.id} ({account.name}) 有可用额度，自动启用")
            
            return True, result
        else:
            return False, result
    
    # 定义后台刷新任务
    async def background_refresh():
        """后台执行刷新"""
        try:
            await manager.refresh_all_with_token(
                accounts=state.accounts,
                get_quota_func=get_quota_func,
                skip_disabled=False,  # 不跳过禁用账号，以便检查是否可以解禁
                skip_error=False      # 不跳过错误账号，以便检查是否已恢复
            )
            # 刷新完成后保存账号配置（因为可能有启用/禁用状态变化）
            state._save_accounts()
        except Exception as e:
            print(f"[RefreshManager] 后台刷新异常: {e}")
    
    # 启动后台任务，不等待完成
    asyncio.create_task(background_refresh())
    
    # 立即返回，前端通过轮询获取进度
    return {
        "ok": True,
        "message": "刷新任务已启动，请通过 /api/refresh/progress 获取进度"
    }


async def get_refresh_config():
    """获取刷新配置"""
    from ...core import get_refresh_manager
    manager = get_refresh_manager()
    
    config = manager.config
    return {
        "ok": True,
        "config": config.to_dict()
    }


async def update_refresh_config(request: Request):
    """更新刷新配置"""
    from ...core import get_refresh_manager
    from ...core.admin_settings import persist_admin_setting
    manager = get_refresh_manager()
    
    body = await request.json()
    
    try:
        # 更新配置
        manager.update_config(**body)
        await persist_admin_setting("refresh", manager.config.to_dict())
        
        return {
            "ok": True,
            "config": manager.config.to_dict(),
            "message": "配置更新成功"
        }
    except ValueError as e:
        return {
            "ok": False,
            "error": str(e)
        }


async def get_refresh_manager_status():
    """获取刷新管理器状态"""
    from ...core import get_refresh_manager
    manager = get_refresh_manager()
    
    status = manager.get_status()
    auto_refresh_status = manager.get_auto_refresh_status()
    
    return {
        "ok": True,
        "status": status,
        "auto_refresh": auto_refresh_status,
        "last_refresh_time": manager.get_last_refresh_time()
    }


# ==================== 集成 RefreshManager 到现有刷新接口 ====================

async def refresh_account_token_with_manager(account_id: str):
    """刷新指定账号的 token（集成 RefreshManager）
    
    刷新前自动检查 Token 状态，使用 RefreshManager 的重试机制。
    """
    from ...core import get_refresh_manager
    manager = get_refresh_manager()
    
    # 查找账号
    account = None
    for acc in state.accounts:
        if acc.id == account_id:
            account = acc
            break
    
    if not account:
        return {"ok": False, "error": "账号不存在"}
    
    # 使用 RefreshManager 的重试机制刷新 Token
    success, result = await manager.retry_with_backoff(
        account.refresh_token
    )
    
    if success:
        return {"ok": True, "message": "Token 刷新成功"}
    else:
        return {"ok": False, "error": f"Token 刷新失败: {result}"}


async def refresh_account_quota_with_token(account_id: str):
    """刷新单个账号额度（先刷新 Token）
    
    在获取额度前自动检查并刷新 Token（如果需要）。
    """
    from ...core import get_refresh_manager, get_account_usage, get_quota_cache
    manager = get_refresh_manager()
    
    # 查找账号
    account = None
    for acc in state.accounts:
        if acc.id == account_id:
            account = acc
            break
    
    if not account:
        return {"ok": False, "error": "账号不存在"}
    
    # 先刷新 Token（如果需要）
    token_success, token_msg = await manager.refresh_token_if_needed(account)
    
    if not token_success:
        return {"ok": False, "error": f"Token 刷新失败: {token_msg}"}
    
    # 获取额度
    success, result = await get_account_usage(account)
    
    if success:
        # 更新额度缓存
        from ...core.quota_cache import CachedQuota
        quota_cache = get_quota_cache()
        cached_quota = CachedQuota.from_usage_info(account.id, result)
        quota_cache.set(account.id, cached_quota)
        
        # 自动启用/禁用账号
        auto_status_changed = False
        if cached_quota.is_exhausted:
            # 额度用尽，自动禁用
            if account.enabled:
                account.enabled = False
                if hasattr(account, "auto_disabled"):
                    account.auto_disabled = True
                auto_status_changed = True
                print(f"[RefreshManager] 账号 {account.id} ({account.name}) 额度已用尽，自动禁用")
        else:
            # 有额度，自动启用（仅对自动禁用的账号生效）
            if (not account.enabled) and getattr(account, "auto_disabled", False):
                account.enabled = True
                account.auto_disabled = False
                auto_status_changed = True
                print(f"[RefreshManager] 账号 {account.id} ({account.name}) 有可用额度，自动启用")
        
        # 如果状态变化，保存配置
        if auto_status_changed:
            state._save_accounts()
        
        return {
            "ok": True,
            "message": f"账号 {account_id} 额度刷新成功",
            "token_refreshed": token_msg != "Token 有效，无需刷新",
            "auto_enabled": auto_status_changed and account.enabled,
            "auto_disabled": auto_status_changed and not account.enabled,
            "usage": {
                "balance": result.balance,
                "current_usage": result.current_usage,
                "usage_limit": result.usage_limit
            }
        }
    else:
        error_msg = result.get("error", "Unknown error") if isinstance(result, dict) else str(result)
        
        # 更新额度缓存，包含错误信息（用于检测封禁）
        from ...core.quota_cache import CachedQuota
        quota_cache = get_quota_cache()
        cached_quota = CachedQuota.from_error(account.id, error_msg)
        quota_cache.set(account.id, cached_quota)
        
        return {"ok": False, "error": f"获取额度失败: {error_msg}"}


# ==================== 协议注册 API ====================

async def register_kiro_protocol():
    """注册 kiro:// 协议"""
    from ...core.protocol_handler import (
        register_protocol_windows, 
        start_callback_server,
        is_protocol_registered
    )
    
    # 启动回调服务器
    server_success, server_result = start_callback_server()
    if not server_success:
        return {"ok": False, "error": f"启动回调服务器失败: {server_result}"}
    
    # 注册协议
    reg_success, reg_msg = register_protocol_windows()
    
    return {
        "ok": reg_success,
        "message": reg_msg,
        "callback_port": server_result if server_success else None,
        "is_registered": is_protocol_registered()
    }


async def unregister_kiro_protocol():
    """取消注册 kiro:// 协议"""
    from ...core.protocol_handler import (
        unregister_protocol_windows,
        stop_callback_server
    )
    
    # 停止回调服务器
    stop_callback_server()
    
    # 取消注册协议
    success, msg = unregister_protocol_windows()
    
    return {"ok": success, "message": msg}


async def get_protocol_status():
    """获取协议注册状态"""
    from ...core.protocol_handler import is_protocol_registered, CALLBACK_PORT
    
    return {
        "is_registered": is_protocol_registered(),
        "callback_port": CALLBACK_PORT
    }


async def get_callback_result():
    """获取回调结果"""
    from ...core.protocol_handler import get_callback_result as _get_result, clear_callback_result
    
    result = _get_result()
    if result:
        # 清除结果，避免重复获取
        clear_callback_result()
        return {"ok": True, "result": result}
    else:
        return {"ok": False, "result": None}
