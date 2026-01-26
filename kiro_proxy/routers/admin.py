from fastapi import APIRouter, Request, HTTPException, Depends, Response
from fastapi.responses import JSONResponse

from ..core import (
    get_history_config,
    get_rate_limiter,
    update_history_config,
    get_model_routing_config,
    update_model_routing_config,
)
from ..core.admin_settings import persist_admin_setting
from ..core.auth_middleware import require_admin_auth, optional_admin_auth
from ..core.admin_auth import get_admin_auth, authenticate_admin, create_admin_session, revoke_admin_session, is_auth_required
from ..handlers import admin as admin_handler
from ..resources import get_resource_path

router = APIRouter(prefix="/api")


# ==================== 认证相关端点 ====================

@router.post("/auth/login")
async def api_admin_login(request: Request, response: Response):
    """管理员登录"""
    try:
        data = await request.json()
        password = data.get("password")

        if not password:
            raise HTTPException(status_code=400, detail="密码不能为空")

        if not authenticate_admin(password):
            raise HTTPException(status_code=401, detail="密码错误")

        # 创建会话
        session_id = create_admin_session()

        # 设置Cookie（可选，主要用Bearer Token）
        response.set_cookie(
            key="admin_session",
            value=session_id,
            max_age=24 * 60 * 60,  # 24小时
            httponly=True,
            secure=False,  # 在生产环境中应该设为True
            samesite="lax"
        )

        return {
            "success": True,
            "session_id": session_id,
            "message": "登录成功"
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"登录失败: {str(e)}")


@router.post("/auth/logout")
async def api_admin_logout(
    response: Response,
    session_id: str = Depends(require_admin_auth)
):
    """管理员登出"""
    try:
        # 撤销会话
        revoke_admin_session(session_id)

        # 清除Cookie
        response.delete_cookie(key="admin_session")

        return {
            "success": True,
            "message": "登出成功"
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"登出失败: {str(e)}")


@router.get("/auth/session")
async def api_admin_session_info(session_id: str = Depends(require_admin_auth)):
    """获取当前会话信息"""
    try:
        auth = get_admin_auth()
        session_info = auth.get_session_info(session_id)

        if not session_info:
            raise HTTPException(status_code=401, detail="会话无效")

        return {
            "success": True,
            "session": session_info
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取会话信息失败: {str(e)}")


@router.get("/auth/sessions")
async def api_admin_sessions(session_id: str = Depends(require_admin_auth)):
    """获取所有活跃会话"""
    try:
        auth = get_admin_auth()
        sessions = auth.get_active_sessions()

        return {
            "success": True,
            "sessions": sessions
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取会话列表失败: {str(e)}")


@router.post("/auth/check")
async def api_admin_auth_check(session_id: str = Depends(optional_admin_auth)):
    """检查认证状态"""
    auth_required = is_auth_required()
    return {
        "auth_required": auth_required,
        "authenticated": not auth_required or session_id is not None,
        "session_id": session_id
    }


# ==================== 公开API（无需认证） ====================


@router.get("/status")
async def api_status():
    return await admin_handler.get_status()


@router.post("/event_logging/batch")
async def api_event_logging_batch(request: Request):
    return await admin_handler.event_logging_batch(request)


@router.get("/stats")
async def api_stats():
    return await admin_handler.get_stats()


@router.get("/logs")
async def api_logs(limit: int = 100):
    return await admin_handler.get_logs(limit)


# ==================== 需要认证的管理员API ====================

@router.get("/accounts/export")
async def api_export_accounts(session_id: str = Depends(require_admin_auth)):
    return await admin_handler.export_accounts()


@router.post("/accounts/import")
async def api_import_accounts(request: Request, session_id: str = Depends(require_admin_auth)):
    return await admin_handler.import_accounts(request)


@router.post("/accounts/manual")
async def api_add_manual_token(request: Request, session_id: str = Depends(require_admin_auth)):
    return await admin_handler.add_manual_token(request)


@router.post("/accounts/batch")
async def api_batch_import_accounts(request: Request, session_id: str = Depends(require_admin_auth)):
    return await admin_handler.batch_import_accounts(request)


@router.post("/accounts/refresh-all")
async def api_refresh_all(session_id: str = Depends(require_admin_auth)):
    return await admin_handler.refresh_all_tokens()


@router.get("/accounts/status")
async def api_accounts_status_enhanced(session_id: str = Depends(require_admin_auth)):
    return await admin_handler.get_accounts_status_enhanced()


@router.get("/accounts/summary")
async def api_accounts_summary(session_id: str = Depends(require_admin_auth)):
    return await admin_handler.get_accounts_summary()


@router.post("/accounts/refresh-all-quotas")
async def api_refresh_all_quotas(session_id: str = Depends(require_admin_auth)):
    return await admin_handler.refresh_all_quotas()


@router.get("/refresh/progress")
async def api_refresh_progress(session_id: str = Depends(require_admin_auth)):
    return await admin_handler.get_refresh_progress()


@router.post("/refresh/all")
async def api_refresh_all_with_progress(session_id: str = Depends(require_admin_auth)):
    return await admin_handler.refresh_all_with_progress()


@router.get("/refresh/config")
async def api_get_refresh_config(session_id: str = Depends(require_admin_auth)):
    return await admin_handler.get_refresh_config()


@router.put("/refresh/config")
async def api_update_refresh_config(request: Request, session_id: str = Depends(require_admin_auth)):
    return await admin_handler.update_refresh_config(request)


@router.get("/refresh/status")
async def api_refresh_status(session_id: str = Depends(require_admin_auth)):
    return await admin_handler.get_refresh_manager_status()


@router.get("/accounts")
async def api_accounts(session_id: str = Depends(require_admin_auth)):
    return await admin_handler.get_accounts()


@router.post("/accounts")
async def api_add_account(request: Request, session_id: str = Depends(require_admin_auth)):
    return await admin_handler.add_account(request)


@router.delete("/accounts/{account_id}")
async def api_delete_account(account_id: str, session_id: str = Depends(require_admin_auth)):
    return await admin_handler.delete_account(account_id)


@router.put("/accounts/{account_id}")
async def api_update_account(account_id: str, request: Request, session_id: str = Depends(require_admin_auth)):
    return await admin_handler.update_account(account_id, request)


@router.post("/accounts/{account_id}/toggle")
async def api_toggle_account(account_id: str, session_id: str = Depends(require_admin_auth)):
    return await admin_handler.toggle_account(account_id)


@router.post("/speedtest")
async def api_speedtest(session_id: str = Depends(require_admin_auth)):
    return await admin_handler.speedtest()


@router.get("/accounts/{account_id}/test")
async def api_test_account_token(account_id: str, session_id: str = Depends(require_admin_auth)):
    return await admin_handler.test_account_token(account_id)


@router.get("/token/scan")
async def api_scan_tokens(session_id: str = Depends(require_admin_auth)):
    return await admin_handler.scan_tokens()


@router.post("/token/add-from-scan")
async def api_add_from_scan(request: Request, session_id: str = Depends(require_admin_auth)):
    return await admin_handler.add_from_scan(request)


@router.get("/config/export")
async def api_export_config(session_id: str = Depends(require_admin_auth)):
    return await admin_handler.export_config()


@router.post("/config/import")
async def api_import_config(request: Request, session_id: str = Depends(require_admin_auth)):
    return await admin_handler.import_config(request)


@router.post("/token/refresh-check")
async def api_refresh_check(session_id: str = Depends(require_admin_auth)):
    return await admin_handler.refresh_token_check()


@router.post("/accounts/{account_id}/refresh")
async def api_refresh_account(account_id: str, session_id: str = Depends(require_admin_auth)):
    return await admin_handler.refresh_account_token_with_manager(account_id)


@router.post("/accounts/{account_id}/restore")
async def api_restore_account(account_id: str, session_id: str = Depends(require_admin_auth)):
    return await admin_handler.restore_account(account_id)


@router.get("/accounts/{account_id}/usage")
async def api_account_usage(account_id: str, session_id: str = Depends(require_admin_auth)):
    return await admin_handler.get_account_usage_info(account_id)


@router.get("/accounts/{account_id}")
async def api_account_detail(account_id: str, session_id: str = Depends(require_admin_auth)):
    return await admin_handler.get_account_detail(account_id)


@router.post("/accounts/{account_id}/refresh-quota")
async def api_refresh_account_quota(account_id: str, session_id: str = Depends(require_admin_auth)):
    return await admin_handler.refresh_account_quota_with_token(account_id)


@router.get("/priority")
async def api_get_priority_accounts(session_id: str = Depends(require_admin_auth)):
    return await admin_handler.get_priority_accounts()


@router.post("/priority/{account_id}")
async def api_set_priority_account(account_id: str, request: Request, session_id: str = Depends(require_admin_auth)):
    return await admin_handler.set_priority_account(account_id, request)


@router.delete("/priority/{account_id}")
async def api_remove_priority_account(account_id: str, session_id: str = Depends(require_admin_auth)):
    return await admin_handler.remove_priority_account(account_id)


@router.put("/priority/reorder")
async def api_reorder_priority_accounts(request: Request, session_id: str = Depends(require_admin_auth)):
    return await admin_handler.reorder_priority_accounts(request)


@router.get("/quota")
async def api_quota_status(session_id: str = Depends(require_admin_auth)):
    return await admin_handler.get_quota_status()


@router.get("/kiro/login-url")
async def api_login_url(session_id: str = Depends(require_admin_auth)):
    return await admin_handler.get_kiro_login_url()


@router.get("/stats/detailed")
async def api_detailed_stats(session_id: str = Depends(require_admin_auth)):
    return await admin_handler.get_detailed_stats()


@router.post("/health-check")
async def api_health_check(session_id: str = Depends(require_admin_auth)):
    return await admin_handler.run_health_check()


@router.get("/browsers")
async def api_browsers(session_id: str = Depends(require_admin_auth)):
    return await admin_handler.get_browsers()


@router.post("/kiro/login/start")
async def api_kiro_login_start(request: Request, session_id: str = Depends(require_admin_auth)):
    return await admin_handler.start_kiro_login(request)


@router.get("/kiro/login/poll")
async def api_kiro_login_poll(session_id: str = Depends(require_admin_auth)):
    return await admin_handler.poll_kiro_login()


@router.post("/kiro/login/cancel")
async def api_kiro_login_cancel(session_id: str = Depends(require_admin_auth)):
    return await admin_handler.cancel_kiro_login()


@router.get("/kiro/login/status")
async def api_kiro_login_status(session_id: str = Depends(require_admin_auth)):
    return await admin_handler.get_kiro_login_status()


@router.post("/kiro/social/start")
async def api_social_login_start(request: Request, session_id: str = Depends(require_admin_auth)):
    return await admin_handler.start_social_login(request)


@router.post("/kiro/social/exchange")
async def api_social_token_exchange(request: Request, session_id: str = Depends(require_admin_auth)):
    return await admin_handler.exchange_social_token(request)


@router.post("/kiro/social/cancel")
async def api_social_login_cancel(session_id: str = Depends(require_admin_auth)):
    return await admin_handler.cancel_social_login()


@router.get("/kiro/social/status")
async def api_social_login_status(session_id: str = Depends(require_admin_auth)):
    return await admin_handler.get_social_login_status()


@router.post("/protocol/register")
async def api_register_protocol(session_id: str = Depends(require_admin_auth)):
    return await admin_handler.register_kiro_protocol()


@router.post("/protocol/unregister")
async def api_unregister_protocol(session_id: str = Depends(require_admin_auth)):
    return await admin_handler.unregister_kiro_protocol()


@router.get("/protocol/status")
async def api_protocol_status(session_id: str = Depends(require_admin_auth)):
    return await admin_handler.get_protocol_status()


@router.get("/protocol/callback")
async def api_protocol_callback(session_id: str = Depends(require_admin_auth)):
    return await admin_handler.get_callback_result()


@router.get("/flows")
async def api_flows(
    protocol: str = None,
    model: str = None,
    account_id: str = None,
    state: str = None,
    has_error: bool = None,
    bookmarked: bool = None,
    search: str = None,
    limit: int = 50,
    offset: int = 0,
    session_id: str = Depends(require_admin_auth),
):
    return await admin_handler.get_flows(
        protocol=protocol,
        model=model,
        account_id=account_id,
        state_filter=state,
        has_error=has_error,
        bookmarked=bookmarked,
        search=search,
        limit=limit,
        offset=offset,
    )


@router.get("/flows/stats")
async def api_flow_stats(session_id: str = Depends(require_admin_auth)):
    return await admin_handler.get_flow_stats()


@router.get("/flows/{flow_id}")
async def api_flow_detail(flow_id: str, session_id: str = Depends(require_admin_auth)):
    return await admin_handler.get_flow_detail(flow_id)


@router.post("/flows/{flow_id}/bookmark")
async def api_bookmark_flow(flow_id: str, request: Request, session_id: str = Depends(require_admin_auth)):
    return await admin_handler.bookmark_flow(flow_id, request)


@router.post("/flows/{flow_id}/note")
async def api_add_flow_note(flow_id: str, request: Request, session_id: str = Depends(require_admin_auth)):
    return await admin_handler.add_flow_note(flow_id, request)


@router.post("/flows/{flow_id}/tag")
async def api_add_flow_tag(flow_id: str, request: Request, session_id: str = Depends(require_admin_auth)):
    return await admin_handler.add_flow_tag(flow_id, request)


@router.post("/flows/export")
async def api_export_flows(request: Request, session_id: str = Depends(require_admin_auth)):
    return await admin_handler.export_flows(request)


@router.get("/settings/history")
async def api_get_history_config(session_id: str = Depends(require_admin_auth)):
    config = get_history_config()
    return config.to_dict()


@router.post("/settings/history")
async def api_update_history_config(request: Request, session_id: str = Depends(require_admin_auth)):
    data = await request.json()
    update_history_config(data)
    await persist_admin_setting("history", get_history_config().to_dict())
    return {"ok": True, "config": get_history_config().to_dict()}


@router.get("/settings/rate-limit")
async def api_get_rate_limit_config(session_id: str = Depends(require_admin_auth)):
    limiter = get_rate_limiter()
    return {
        "enabled": limiter.config.enabled,
        "min_request_interval": limiter.config.min_request_interval,
        "max_requests_per_minute": limiter.config.max_requests_per_minute,
        "global_max_requests_per_minute": limiter.config.global_max_requests_per_minute,
        "stats": limiter.get_stats(),
    }


@router.post("/settings/rate-limit")
async def api_update_rate_limit_config(request: Request, session_id: str = Depends(require_admin_auth)):
    data = await request.json()
    limiter = get_rate_limiter()
    limiter.update_config(**data)
    await persist_admin_setting(
        "rate_limit",
        {
            "enabled": limiter.config.enabled,
            "min_request_interval": limiter.config.min_request_interval,
            "max_requests_per_minute": limiter.config.max_requests_per_minute,
            "global_max_requests_per_minute": limiter.config.global_max_requests_per_minute,
        },
    )
    return {
        "ok": True,
        "config": {
            "enabled": limiter.config.enabled,
            "min_request_interval": limiter.config.min_request_interval,
            "max_requests_per_minute": limiter.config.max_requests_per_minute,
            "global_max_requests_per_minute": limiter.config.global_max_requests_per_minute,
        },
    }


@router.get("/settings/model-routing")
async def api_get_model_routing_config(session_id: str = Depends(require_admin_auth)):
    config = get_model_routing_config()
    return config.to_dict()


@router.post("/settings/model-routing")
async def api_update_model_routing_config(request: Request, session_id: str = Depends(require_admin_auth)):
    data = await request.json()
    config = update_model_routing_config(data)
    await persist_admin_setting("model_routing", config.to_dict())
    return {"ok": True, "config": config.to_dict()}


DOC_TITLES = {
    "01-quickstart": "快速开始",
    "02-features": "功能特性",
    "03-faq": "常见问题",
    "04-api": "API 参考",
    "05-server-deploy": "服务器部署",
}


@router.get("/docs")
async def api_docs_list():
    docs_dir = get_resource_path("kiro_proxy/docs")
    docs = []
    if docs_dir.exists():
        for doc_file in sorted(docs_dir.glob("*.md")):
            doc_id = doc_file.stem
            title = DOC_TITLES.get(doc_id, doc_id)
            docs.append({"id": doc_id, "title": title})
    return {"docs": docs}


@router.get("/docs/{doc_id}")
async def api_docs_content(doc_id: str):
    docs_dir = get_resource_path("kiro_proxy/docs")
    doc_file = docs_dir / f"{doc_id}.md"
    if not doc_file.exists():
        raise HTTPException(status_code=404, detail="文档不存在")
    content = doc_file.read_text(encoding="utf-8")
    title = DOC_TITLES.get(doc_id, doc_id)
    return {"id": doc_id, "title": title, "content": content}
