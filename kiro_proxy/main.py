import time
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from . import __version__
from .core import get_quota_scheduler, get_refresh_manager, scheduler, state
from .core.admin_settings import load_admin_settings_async
from .routers import admin, protocols, web


async def _background_initial_token_check(refresh_manager, accounts):
    """后台执行初始Token检查和刷新

    这个函数在服务启动后异步执行，不会阻塞启动流程。
    对每个启用的账号检查Token状态，如果需要则进行刷新。
    """
    try:
        # 稍微延迟一下，确保服务完全启动
        await asyncio.sleep(1)

        print(f"[Background] 开始检查 {len(accounts)} 个账号的 Token 状态...")

        refresh_count = 0
        success_count = 0

        for account in accounts:
            if not account.enabled:
                continue

            if refresh_manager.should_refresh_token(account):
                refresh_count += 1
                try:
                    print(f"[Background] 检查账号 {account.name} Token...")
                    success, msg = await refresh_manager.refresh_token_if_needed(account)
                    if success:
                        success_count += 1
                        print(f"[Background] 账号 {account.name} Token 刷新成功")
                    else:
                        print(f"[Background] 账号 {account.name} Token 刷新失败: {msg}")
                except Exception as e:
                    print(f"[Background] 账号 {account.name} Token 刷新异常: {e}")

        if refresh_count > 0:
            print(f"[Background] Token 检查完成: {success_count}/{refresh_count} 个账号刷新成功")
        else:
            print("[Background] 所有账号 Token 状态正常，无需刷新")

    except Exception as e:
        print(f"[Background] 后台 Token 检查异常: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await scheduler.start()
    
    await load_admin_settings_async()

    refresh_manager = get_refresh_manager()
    refresh_manager.set_accounts_getter(lambda: state.accounts)

    # 启动配额调度器
    quota_scheduler = get_quota_scheduler()
    quota_scheduler.set_accounts_getter(lambda: state.accounts)
    await quota_scheduler.start()

    # 启动自动刷新定时器（会在后台异步检查和刷新Token）
    await refresh_manager.start_auto_refresh()

    # 创建后台任务进行初始Token检查（非阻塞）
    accounts = state.accounts
    if accounts:
        print(f"[Startup] 服务已启动，将在后台检查 {len(accounts)} 个账号的 Token 状态...")
        asyncio.create_task(_background_initial_token_check(refresh_manager, accounts))

    yield

    await refresh_manager.stop_auto_refresh()
    await quota_scheduler.stop()
    await scheduler.stop()


def create_app() -> FastAPI:
    app = FastAPI(title="Kiro API Proxy", docs_url="/docs", redoc_url=None, lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def log_requests(request: Request, call_next):
        start_time = time.time()
        path = request.url.path
        method = request.method

        body_str = "<skipped>"

        print(f"[Request] {method} {path} | Body: {body_str}")

        response = await call_next(request)

        duration = (time.time() - start_time) * 1000
        print(f"[Response] {method} {path} - {response.status_code} ({duration:.2f}ms)")
        return response

    app.include_router(web.router)
    app.include_router(protocols.router)
    app.include_router(admin.router)

    return app


app = create_app()


def run(port: int = 8080):
    import uvicorn

    print(f"\n{'='*50}")
    print(f"  Kiro API Proxy v{__version__}")
    print(f"  http://localhost:{port}")
    print(f"{'='*50}\n")
    uvicorn.run(app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    import sys

    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8080
    run(port)
