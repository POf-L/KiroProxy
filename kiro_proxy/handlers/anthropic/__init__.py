"""Anthropic 协议处理 - /v1/messages"""
import json
import uuid
import time
import asyncio
import httpx
from fastapi import Request, HTTPException
from fastapi.responses import StreamingResponse

from ...config import KIRO_API_URL, map_model_name
from ...core import state, RetryableRequest, is_retryable_error, stats_manager, flow_monitor, TokenUsage, apply_model_routing
from ...core.state import RequestLog
from ...core.history_manager import HistoryManager, get_history_config, is_content_length_error, TruncateStrategy
from ...core.error_handler import classify_error, ErrorType, format_error_log
from ...core.rate_limiter import get_rate_limiter
from ...credential import quota_manager
from ...kiro_api import build_headers, build_kiro_request, parse_event_stream_full, parse_event_stream, is_quota_exceeded_error
from ...core.thinking import (
    ThinkingConfig,
    build_thinking_prompt,
    infer_thinking_from_anthropic_messages,
    normalize_thinking_config,
    strip_thinking_from_history,
    strip_thinking_from_text,
)
from ...converters import (
    generate_session_id,
    convert_anthropic_tools_to_kiro,
    convert_anthropic_messages_to_kiro,
    convert_kiro_response_to_anthropic,
    extract_images_from_content,
    inject_thinking_tags_to_system,
    find_real_thinking_start_tag,
    find_real_thinking_end_tag,
    extract_thinking_from_content
)

# 尝试导入 tiktoken，如果失败则使用估算方法
try:
    import tiktoken
    _encoding = tiktoken.get_encoding("cl100k_base")
    _USE_TIKTOKEN = True
except ImportError:
    _encoding = None
    _USE_TIKTOKEN = False


def _extract_text_from_content(content) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            parts.append(_extract_text_from_content(item))
        return "".join(parts)
    if isinstance(content, dict):
        if "text" in content and isinstance(content.get("text"), str):
            return content["text"]
        if "content" in content:
            return _extract_text_from_content(content.get("content"))
    return ""


def _estimate_tokens(text: str) -> int:
    """估算/计算 token 数量
    
    优先使用 tiktoken (cl100k_base)，否则使用字符估算：
    - 中文字符：约 1.5 字符 = 1 token
    - 其他字符：约 4 字符 = 1 token
    """
    if not text:
        return 0
    
    if _USE_TIKTOKEN and _encoding:
        return len(_encoding.encode(text))
    
    # 回退到字符估算
    chinese_chars = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
    other_chars = len(text) - chinese_chars
    tokens = int(chinese_chars / 1.5) + int(other_chars / 4)
    return max(1, tokens)


def _count_tokens_from_messages(messages, system: str = "") -> int:
    total = _estimate_tokens(system) if system else 0
    for msg in messages or []:
        total += _estimate_tokens(_extract_text_from_content(msg.get("content")))
    return total


def _estimate_output_tokens_from_text(text: str) -> int:
    return _estimate_tokens(text)


async def _check_and_disable_if_exhausted(account):
    """检查账号额度，如果为 0 则禁用账号
    
    Args:
        account: 账号对象
    """
    if not account:
        return
    
    try:
        from ...core.usage import get_account_usage
        from ...core.quota_cache import CachedQuota, get_quota_cache
        
        success, result = await get_account_usage(account)
        if success:
            quota = CachedQuota.from_usage_info(account.id, result)
            get_quota_cache().set(account.id, quota)
            
            if quota.is_exhausted:
                account.enabled = False
                if hasattr(account, "auto_disabled"):
                    account.auto_disabled = True
                from ...core.state import state
                state._save_accounts()
                print(f"[Account] 账号 {account.id} ({account.name}) 额度已用尽，自动禁用")
    except Exception as e:
        print(f"[Account] 检查账号 {account.id} 额度失败: {e}")


def _handle_kiro_error(status_code: int, error_text: str, account):
    """处理 Kiro API 错误，返回 (http_status, error_type, error_message)"""
    error = classify_error(status_code, error_text)
    
    # 打印友好的错误日志
    print(format_error_log(error, account.id if account else None))
    
    # 账号封禁 - 禁用账号
    if error.should_disable_account and account:
        account.enabled = False
        if hasattr(account, "auto_disabled"):
            account.auto_disabled = False
        from ...credential import CredentialStatus
        account.status = CredentialStatus.SUSPENDED
        try:
            from ...core import state as _state
            _state._save_accounts()
        except Exception:
            pass
        print(f"[Account] 账号 {account.id} 已被禁用 (封禁)")
    
    # 仅 429 状态码触发冷却（不再根据错误文本判断）
    elif status_code == 429 and account:
        account.mark_quota_exceeded(error.message[:100])
    
    # 其他错误（非 429、非内容过长）- 异步检查额度
    elif error.type not in (ErrorType.RATE_LIMITED, ErrorType.CONTENT_TOO_LONG) and account:
        import asyncio
        asyncio.create_task(_check_and_disable_if_exhausted(account))
    
    # 映射错误类型
    error_type_map = {
        ErrorType.ACCOUNT_SUSPENDED: (403, "authentication_error"),
        ErrorType.RATE_LIMITED: (429, "rate_limit_error"),
        ErrorType.CONTENT_TOO_LONG: (400, "invalid_request_error"),
        ErrorType.AUTH_FAILED: (401, "authentication_error"),
        ErrorType.SERVICE_UNAVAILABLE: (503, "api_error"),
        ErrorType.MODEL_UNAVAILABLE: (503, "overloaded_error"),
        ErrorType.UNKNOWN: (500, "api_error"),
    }
    
    http_status, err_type = error_type_map.get(error.type, (500, "api_error"))
    return http_status, err_type, error.user_message, error


async def handle_count_tokens(request: Request):
    '''Handle /v1/messages/count_tokens requests.'''
    body = await request.json()
    messages = body.get("messages", [])
    system = body.get("system", "")
    if not messages and not system:
        raise HTTPException(400, "messages required")
    return {"input_tokens": _count_tokens_from_messages(messages, system)}


async def _call_kiro_for_summary(prompt: str, account, headers: dict) -> str:
    """调用 Kiro API 生成摘要（内部使用）"""
    kiro_request = build_kiro_request(prompt, "claude-haiku-4.5", [])  # 用快速模型生成摘要
    try:
        async with httpx.AsyncClient(verify=False, timeout=60) as client:
            resp = await client.post(KIRO_API_URL, json=kiro_request, headers=headers)
            if resp.status_code == 200:
                return parse_event_stream(resp.content)
    except Exception as e:
        print(f"[Summary] API 调用失败: {e}")
    return ""


async def handle_messages(request: Request):
    """处理 /v1/messages 请求"""
    start_time = time.time()
    log_id = uuid.uuid4().hex[:8]

    try:
        hdrs = request.headers
        print(
            f"[Anthropic][Headers:{log_id}] accept={hdrs.get('accept')} content-type={hdrs.get('content-type')} "
            f"anthropic-beta={hdrs.get('anthropic-beta')} user-agent={hdrs.get('user-agent')}"
        )
    except Exception:
        pass
    
    body = await request.json()
    model_req = body.get("model", "claude-sonnet-4")
    model_raw = apply_model_routing(model_req)
    model = map_model_name(model_raw)
    messages = body.get("messages", [])
    system = body.get("system", "")
    stream = body.get("stream", False)
    tools = body.get("tools", [])
    
    # 处理思考功能（Extended Thinking）
    thinking_explicit = "thinking" in body
    thinking_cfg: ThinkingConfig = (
        normalize_thinking_config(body.get("thinking")) if thinking_explicit else ThinkingConfig(False, None)
    )
    # 移除自动推断逻辑 - 只有用户明确启用时才使用思维链
    # if not thinking_explicit and infer_thinking_from_anthropic_messages(messages):
    #     # Claude Code 可能只在首轮携带 thinking 配置；如果历史里已经出现 thinking block，默认继承开启。
    #     thinking_cfg = ThinkingConfig(True, None)

    # 启用思考模式：使用“独立请求”生成思维链，避免向主请求注入提示词污染上下文
    if thinking_cfg.enabled:
        print(
            f"[Anthropic] Thinking mode enabled (separate request): budget_tokens={thinking_cfg.budget_tokens if thinking_cfg.budget_tokens is not None else 'unlimited'}"
        )
    
    # 调试：打印原始请求的关键信息
    print(
        f"[Anthropic] Request: model={body.get('model')} -> {model_raw} -> {model}, messages={len(messages)}, stream={stream}, tools={len(tools)}, thinking={'enabled' if thinking_cfg.enabled else 'disabled'}"
    )
    
    if not messages:
        raise HTTPException(400, "messages required")
    
    session_id = generate_session_id(messages)
    account = state.get_available_account(session_id)
    
    if not account:
        raise HTTPException(503, "All accounts are rate limited or unavailable")
    
    # 创建 Flow 记录
    flow_id = flow_monitor.create_flow(
        protocol="anthropic",
        method="POST",
        path="/v1/messages",
        headers=dict(request.headers),
        body=body,
        account_id=account.id,
        account_name=account.name,
    )
    
    # 检查 token 是否即将过期，尝试刷新
    if account.is_token_expiring_soon(5):
        print(f"[Anthropic] Token 即将过期，尝试刷新: {account.id}")
        success, msg = await account.refresh_token()
        if not success:
            print(f"[Anthropic] Token 刷新失败: {msg}")
    
    token = account.get_token()
    if not token:
        flow_monitor.fail_flow(flow_id, "authentication_error", f"Failed to get token for account {account.name}")
        raise HTTPException(500, f"Failed to get token for account {account.name}")
    
    # 使用账号的动态 Machine ID（提前构建，供摘要使用）
    creds = account.get_credentials()
    headers = build_headers(
        token,
        machine_id=account.get_machine_id(),
        profile_arn=creds.profile_arn if creds else None,
        client_id=creds.client_id if creds else None
    )
    
    # 限速检查
    rate_limiter = get_rate_limiter()
    can_request, wait_seconds, reason = rate_limiter.can_request(account.id)
    if not can_request:
        print(f"[Anthropic] 限速: {reason}")
        await asyncio.sleep(wait_seconds)
    
    # 转换消息格式
    user_content, history, tool_results = convert_anthropic_messages_to_kiro(messages, system)
    
    # 历史消息预处理
    history_manager = HistoryManager(get_history_config(), cache_key=session_id)
    
    # 检查是否需要智能摘要或错误重试预摘要
    async def api_caller(prompt: str) -> str:
        return await _call_kiro_for_summary(prompt, account, headers)
    if history_manager.should_summarize(history) or history_manager.should_pre_summary_for_error_retry(history, user_content):
        history = await history_manager.pre_process_async(history, user_content, api_caller)
    else:
        history = history_manager.pre_process(history, user_content)
    
    # 摘要/截断后再次修复历史交替和 toolUses/toolResults 配对
    from ...converters import fix_history_alternation
    history = fix_history_alternation(history)
    
    if history_manager.was_truncated:
        print(f"[Anthropic] {history_manager.truncate_info}")
    
    # 提取最后一条消息中的图片
    images = []
    if messages:
        last_msg = messages[-1]
        if last_msg.get("role") == "user":
            _, images = extract_images_from_content(last_msg.get("content", ""))
    
    # 构建 Kiro 请求
    kiro_tools = convert_anthropic_tools_to_kiro(tools) if tools else None
    kiro_request = build_kiro_request(user_content, model, history, kiro_tools, images, tool_results)
    
    if stream:
        return await _handle_stream(
            kiro_request,
            headers,
            account,
            model,
            log_id,
            start_time,
            session_id,
            flow_id,
            history,
            user_content,
            kiro_tools,
            images,
            tool_results,
            history_manager,
            thinking_enabled=thinking_cfg.enabled,
            budget_tokens=thinking_cfg.budget_tokens,
        )
    else:
        return await _handle_non_stream(
            kiro_request,
            headers,
            account,
            model,
            log_id,
            start_time,
            session_id,
            flow_id,
            history,
            user_content,
            kiro_tools,
            images,
            tool_results,
            history_manager,
            thinking_enabled=thinking_cfg.enabled,
            budget_tokens=thinking_cfg.budget_tokens,
        )


async def _handle_stream(kiro_request, headers, account, model, log_id, start_time, session_id=None, flow_id=None, history=None, user_content="", kiro_tools=None, images=None, tool_results=None, history_manager=None, thinking_enabled=False, budget_tokens: int | None = None):
    """Handle streaming responses with auto-retry on quota exceeded and network errors.
    
    When thinking_enabled=True, uses single request with <thinking> tag injection.
    The ThinkingStreamProcessor splits the response into thinking and text blocks.
    """
    
    async def generate():
        nonlocal kiro_request, history
        current_account = account
        retry_count = 0
        max_retries = 2
        full_content = ""
        saw_any_chunk = False
        saw_any_text = False
        sent_any_event = False
        content_block_index_ref = [0]
        
        print(f"[Anthropic][Stream:{log_id}] start model={model} account={getattr(current_account, 'id', None)} thinking={thinking_enabled}")

        if thinking_enabled:
            thinking_prompt = build_thinking_prompt(
                user_content, 
                budget_tokens=budget_tokens,
                history=history,
                has_tool_results=bool(tool_results)
            )
            kiro_request = build_kiro_request(thinking_prompt, model, history, kiro_tools, images, tool_results)

        thinking_processor = ThinkingStreamProcessor(thinking_enabled, index_ref=content_block_index_ref)
        
        while retry_count <= max_retries:
            try:
                async with httpx.AsyncClient(verify=False, timeout=300) as client:
                    async with client.stream("POST", KIRO_API_URL, json=kiro_request, headers=headers) as response:
                        print(f"[Anthropic][Stream:{log_id}] upstream_status={response.status_code}")
                        
                        # 仅 429 状态码触发冷却和账号切换
                        if response.status_code == 429:
                            current_account.mark_quota_exceeded("Rate limited (stream)")
                            
                            # 尝试切换账号
                            next_account = state.get_next_available_account(current_account.id)
                            if next_account and retry_count < max_retries:
                                print(f"[Stream] 429 限流，切换账号: {current_account.id} -> {next_account.id}")
                                current_account = next_account
                                token = current_account.get_token()
                                headers["Authorization"] = f"Bearer {token}"
                                retry_count += 1
                                continue
                            
                            if flow_id:
                                flow_monitor.fail_flow(flow_id, "rate_limit_error", "All accounts rate limited", 429)
                            yield f'data: {{"type":"error","error":{{"type":"rate_limit_error","message":"All accounts rate limited"}}}}\n\n'
                            return

                        # 处理可重试的服务端错误（不触发冷却，仅重试）
                        if is_retryable_error(response.status_code):
                            if retry_count < max_retries:
                                print(f"[Stream] 服务端错误 {response.status_code}，重试 {retry_count + 1}/{max_retries}")
                                retry_count += 1
                                import asyncio
                                await asyncio.sleep(0.5 * (2 ** retry_count))
                                continue
                            if flow_id:
                                flow_monitor.fail_flow(flow_id, "api_error", "Server error after retries", response.status_code)
                            yield f'data: {{"type":"error","error":{{"type":"api_error","message":"Server error after retries"}}}}\n\n'
                            return

                        if response.status_code != 200:
                            error_text = await response.aread()
                            error_str = error_text.decode()
                            print(f"=== Kiro API Error ===")
                            print(f"Status: {response.status_code}")
                            print(f"Response: {error_str[:500]}")
                            print(f"Request model: {model}")
                            print(f"History len: {len(history) if history else 0}")
                            print(f"Tool results: {len(tool_results) if tool_results else 0}")
                            # 对于 400 错误，打印更多请求细节
                            if response.status_code == 400:
                                print(f"Kiro request keys: {list(kiro_request.keys())}")
                                if 'conversationState' in kiro_request:
                                    cs = kiro_request['conversationState']
                                    print(f"  conversationState keys: {list(cs.keys())}")
                                    if 'currentMessage' in cs:
                                        cm = cs['currentMessage']
                                        print(f"  currentMessage keys: {list(cm.keys())}")
                                        if 'userInputMessage' in cm:
                                            uim = cm['userInputMessage']
                                            print(f"  userInputMessage keys: {list(uim.keys())}")
                                            content = uim.get('content', '')
                                            print(f"  content (first 200 chars): {str(content)[:200]}")
                                    if 'history' in cs:
                                        hist = cs['history']
                                        print(f"  history count: {len(hist) if hist else 0}")
                                        if hist:
                                            for i, h in enumerate(hist[:3]):
                                                print(f"    history[{i}] keys: {list(h.keys()) if isinstance(h, dict) else type(h)}")
                            print(f"======================")
                            
                            # 使用统一的错误处理
                            http_status, error_type, error_msg, error_obj = _handle_kiro_error(
                                response.status_code, error_str, current_account
                            )
                            
                            # 账号封禁 - 尝试切换账号
                            if error_obj.should_switch_account:
                                next_account = state.get_next_available_account(current_account.id)
                                if next_account and retry_count < max_retries:
                                    print(f"[Stream] 切换账号: {current_account.id} -> {next_account.id}")
                                    current_account = next_account
                                    headers["Authorization"] = f"Bearer {current_account.get_token()}"
                                    retry_count += 1
                                    continue
                            
                            # 检查是否为内容长度超限错误，尝试截断重试
                            if error_obj.type == ErrorType.CONTENT_TOO_LONG:
                                history_chars, user_chars, total_chars = history_manager.estimate_request_chars(
                                    history, user_content
                                )
                                print(f"[Stream] 内容长度超限: history={history_chars} chars, user={user_chars} chars, total={total_chars} chars")
                                async def api_caller(prompt: str) -> str:
                                    return await _call_kiro_for_summary(prompt, current_account, headers)
                                truncated_history, should_retry = await history_manager.handle_length_error_async(
                                    history, retry_count, api_caller
                                )
                                if should_retry:
                                    print(f"[Stream] 内容长度超限，{history_manager.truncate_info}")
                                    history = truncated_history
                                    # 重新构建请求
                                    kiro_request = build_kiro_request(user_content, model, history, kiro_tools, images, tool_results)
                                    retry_count += 1
                                    continue
                            
                            if flow_id:
                                flow_monitor.fail_flow(flow_id, error_type, error_msg, response.status_code, error_str)
                            yield f'data: {{"type":"error","error":{{"type":"{error_type}","message":"{error_msg}"}}}}\n\n'
                            return

                        # 标记开始流式传输
                        if flow_id:
                            flow_monitor.start_streaming(flow_id)

                        # 正常处理响应
                        msg_id = f"msg_{log_id}"
                        sent_any_event = True
                        yield f'event: message_start\ndata: {{"type":"message_start","message":{{"id":"{msg_id}","type":"message","role":"assistant","content":[],"model":"{model}","stop_reason":null,"stop_sequence":null,"usage":{{"input_tokens":0,"output_tokens":0}}}}}}\n\n'

                        # ========== 主响应流式处理（单次调用，按 <thinking> 标签拆分） ==========
                        full_response = b""
                        text_block_started = False
                        chunk_buffer = b""
                        async for chunk in response.aiter_bytes():
                            if not saw_any_chunk:
                                saw_any_chunk = True
                                print(f"[Anthropic][Stream:{log_id}] first_chunk bytes={len(chunk)}")
                            full_response += chunk
                            chunk_buffer += chunk

                            try:
                                while len(chunk_buffer) >= 12:
                                    total_len = int.from_bytes(chunk_buffer[0:4], 'big')

                                    # 如果缓冲区不足以容纳整个消息，等待更多数据
                                    if len(chunk_buffer) < total_len:
                                        break

                                    headers_len = int.from_bytes(chunk_buffer[4:8], 'big')
                                    payload_start = 12 + headers_len
                                    payload_end = total_len - 4

                                    if payload_start < payload_end:
                                        try:
                                            payload_data = chunk_buffer[payload_start:payload_end]
                                            payload = json.loads(payload_data.decode('utf-8'))
                                            content = None
                                            if 'assistantResponseEvent' in payload:
                                                content = payload['assistantResponseEvent'].get('content')
                                            elif 'content' in payload:
                                                content = payload['content']
                                            if content:
                                                full_content += content
                                                saw_any_text = True
                                                if flow_id:
                                                    flow_monitor.add_chunk(flow_id, content)

                                                events = thinking_processor.process_content(content)
                                                for event in events:
                                                    if event["type"] == "content_block_start" and event.get("content_block", {}).get("type") == "text":
                                                        text_block_started = True
                                                    if event["type"] in ["content_block_start", "content_block_delta", "content_block_stop"]:
                                                        sent_any_event = True
                                                        yield f'event: {event["type"]}\ndata: {json.dumps(event, separators=(",", ":"), ensure_ascii=False)}\n\n'
                                        except Exception as e:
                                            print(f"[Stream] Payload parse error: {e}")
                                            pass

                                    # 移动缓冲区
                                    chunk_buffer = chunk_buffer[total_len:]
                            except Exception as e:
                                print(f"[Stream] Chunk processing error: {e}")
                                pass

                        # 完成思考处理
                        final_events = thinking_processor.finalize()
                        for event in final_events:
                            sent_any_event = True
                            yield f'event: {event["type"]}\ndata: {json.dumps(event, separators=(",", ":"), ensure_ascii=False)}\n\n'

                        # 确保文本块已开始
                        if not text_block_started:
                            idx = thinking_processor._next_index()
                            yield f'event: content_block_start\ndata: {{"type":"content_block_start","index":{idx},"content_block":{{"type":"text","text":""}}}}\n\n'
                            yield f'event: content_block_stop\ndata: {{"type":"content_block_stop","index":{idx}}}\n\n'

                        result = parse_event_stream_full(full_response)

                        if result["tool_uses"]:
                            tool_start_index = content_block_index_ref[0]
                            for i, tool_use in enumerate(result["tool_uses"]):
                                idx = tool_start_index + i
                                yield f'event: content_block_start\ndata: {{"type":"content_block_start","index":{idx},"content_block":{{"type":"tool_use","id":"{tool_use["id"]}","name":"{tool_use["name"]}","input":{{}}}}}}\n\n'
                                partial_json = json.dumps(tool_use.get("input") or {}, ensure_ascii=False)
                                yield f'event: content_block_delta\ndata: {{"type":"content_block_delta","index":{idx},"delta":{{"type":"input_json_delta","partial_json":{json.dumps(partial_json, ensure_ascii=False)}}}}}\n\n'
                                yield f'event: content_block_stop\ndata: {{"type":"content_block_stop","index":{idx}}}\n\n'

                        stop_reason = result["stop_reason"]
                        input_tokens = result.get("input_tokens", 0)
                        output_tokens = result.get("output_tokens", 0)
                        if not output_tokens and full_content:
                            output_tokens = _estimate_output_tokens_from_text(full_content)
                        yield f'event: message_delta\ndata: {{"type":"message_delta","delta":{{"stop_reason":"{stop_reason}","stop_sequence":null}},"usage":{{"input_tokens":{input_tokens},"output_tokens":{output_tokens}}}}}\n\n'
                        yield f'event: message_stop\ndata: {{"type":"message_stop"}}\n\n'
                        yield 'data: [DONE]\n\n'
                        print(
                            f"[Anthropic][Stream:{log_id}] done chunks={saw_any_chunk} text={saw_any_text} sent_events={sent_any_event} "
                            f"input_tokens={input_tokens} output_tokens={output_tokens} stop_reason={stop_reason}"
                        )

                        # 完成 Flow
                        if flow_id:
                            flow_monitor.complete_flow(
                                flow_id,
                                status_code=200,
                                content=full_content,
                                tool_calls=result.get("tool_uses", []),
                                stop_reason=stop_reason,
                                usage=TokenUsage(
                                    input_tokens=result.get("input_tokens", 0),
                                    output_tokens=result.get("output_tokens", 0),
                                ),
                            )

                        current_account.request_count += 1
                        current_account.last_used = time.time()
                        get_rate_limiter().record_request(current_account.id)
                        
                        # 记录日志
                        duration = (time.time() - start_time) * 1000
                        state.add_log(RequestLog(
                            id=log_id,
                            timestamp=time.time(),
                            method="POST",
                            path="/v1/messages",
                            model=model,
                            account_id=current_account.id if current_account else None,
                            status=200,
                            duration_ms=duration,
                            error=None
                        ))
                        return

            except httpx.TimeoutException:
                if retry_count < max_retries:
                    print(f"[Stream] 请求超时，重试 {retry_count + 1}/{max_retries}")
                    retry_count += 1
                    import asyncio
                    await asyncio.sleep(0.5 * (2 ** retry_count))
                    continue
                if flow_id:
                    flow_monitor.fail_flow(flow_id, "timeout_error", "Request timeout after retries", 408)
                yield f'data: {{"type":"error","error":{{"type":"api_error","message":"Request timeout after retries"}}}}\n\n'
                return
            except httpx.ConnectError:
                if retry_count < max_retries:
                    print(f"[Stream] 连接错误，重试 {retry_count + 1}/{max_retries}")
                    retry_count += 1
                    import asyncio
                    await asyncio.sleep(0.5 * (2 ** retry_count))
                    continue
                if flow_id:
                    flow_monitor.fail_flow(flow_id, "connection_error", "Connection error after retries", 502)
                yield f'data: {{"type":"error","error":{{"type":"api_error","message":"Connection error after retries"}}}}\n\n'
                return
            except Exception as e:
                # 检查是否为可重试的网络错误
                if is_retryable_error(None, e) and retry_count < max_retries:
                    print(f"[Stream] 网络错误，重试 {retry_count + 1}/{max_retries}: {type(e).__name__}")
                    retry_count += 1
                    import asyncio
                    await asyncio.sleep(0.5 * (2 ** retry_count))
                    continue
                if flow_id:
                    flow_monitor.fail_flow(flow_id, "api_error", str(e), 500)
                yield f'data: {{"type":"error","error":{{"type":"api_error","message":"{str(e)}"}}}}\n\n'
                return

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


async def _handle_non_stream(kiro_request, headers, account, model, log_id, start_time, session_id=None, flow_id=None, history=None, user_content="", kiro_tools=None, images=None, tool_results=None, history_manager=None, thinking_enabled: bool = False, budget_tokens: int | None = None):
    """Handle non-streaming responses with auto-retry on quota exceeded and network errors.
    
    When thinking_enabled=True, uses single request with <thinking> tag injection.
    The response is parsed to extract thinking content from <thinking> tags.
    """
    error_msg = None
    status_code = 200
    current_account = account
    max_retries = 2
    retry_ctx = RetryableRequest(max_retries=2)

    thinking_prompt = None
    if thinking_enabled:
        thinking_prompt = build_thinking_prompt(
            user_content, 
            budget_tokens=budget_tokens,
            history=history,
            has_tool_results=bool(tool_results)
        )
        kiro_request = build_kiro_request(thinking_prompt, model, history, kiro_tools, images, tool_results)

    request_content = thinking_prompt if thinking_enabled else user_content

    for retry in range(max_retries + 1):
        try:
            async with httpx.AsyncClient(verify=False, timeout=300) as client:
                response = await client.post(KIRO_API_URL, json=kiro_request, headers=headers)
                status_code = response.status_code

                # 仅 429 状态码触发冷却和账号切换
                if response.status_code == 429:
                    current_account.mark_quota_exceeded("Rate limited")
                    
                    # 尝试切换账号
                    next_account = state.get_next_available_account(current_account.id)
                    if next_account and retry < max_retries:
                        print(f"[NonStream] 429 限流，切换账号: {current_account.id} -> {next_account.id}")
                        current_account = next_account
                        token = current_account.get_token()
                        creds = current_account.get_credentials()
                        headers["Authorization"] = f"Bearer {token}"
                        continue
                    
                    if flow_id:
                        flow_monitor.fail_flow(flow_id, "rate_limit_error", "All accounts rate limited", 429)
                    raise HTTPException(429, "All accounts rate limited")

                # 处理可重试的服务端错误（不触发冷却，仅重试）
                if is_retryable_error(response.status_code):
                    if retry < max_retries:
                        print(f"[NonStream] 服务端错误 {response.status_code}，重试 {retry + 1}/{max_retries}")
                        await retry_ctx.wait()
                        continue
                    if flow_id:
                        flow_monitor.fail_flow(flow_id, "api_error", f"Server error after {max_retries} retries", response.status_code)
                    raise HTTPException(response.status_code, f"Server error after {max_retries} retries")

                if response.status_code != 200:
                    error_msg = response.text
                    print(f"[NonStream] Kiro API Error {response.status_code}: {error_msg[:500]}")
                    
                    # 使用统一的错误处理
                    status, error_type, error_message, error_obj = _handle_kiro_error(
                        response.status_code, error_msg, current_account
                    )
                    
                    # 账号封禁或配额超限 - 尝试切换账号
                    if error_obj.should_switch_account:
                        next_account = state.get_next_available_account(current_account.id)
                        if next_account and retry < max_retries:
                            print(f"[NonStream] 切换账号: {current_account.id} -> {next_account.id}")
                            current_account = next_account
                            headers["Authorization"] = f"Bearer {current_account.get_token()}"
                            continue
                    
                    # 检查是否为内容长度超限错误，尝试截断重试
                    if error_obj.type == ErrorType.CONTENT_TOO_LONG and history_manager:
                        history_chars, user_chars, total_chars = history_manager.estimate_request_chars(
                            history, request_content
                        )
                        print(f"[NonStream] 内容长度超限: history={history_chars} chars, user={user_chars} chars, total={total_chars} chars")
                        async def api_caller(prompt: str) -> str:
                            return await _call_kiro_for_summary(prompt, current_account, headers)
                        truncated_history, should_retry = await history_manager.handle_length_error_async(
                            history, retry, api_caller
                        )
                        if should_retry:
                            print(f"[NonStream] 内容长度超限，{history_manager.truncate_info}")
                            history = truncated_history
                            kiro_request = build_kiro_request(request_content, model, history, kiro_tools, images, tool_results)
                            continue
                        else:
                            reason = f" ({history_manager.truncate_info})" if history_manager.truncate_info else ""
                            print(f"[NonStream] 内容长度超限但未重试: retry={retry}/{max_retries}{reason}")
                    
                    if flow_id:
                        flow_monitor.fail_flow(flow_id, error_type, error_message, status, error_msg)
                    raise HTTPException(status, error_message)

                result = parse_event_stream_full(response.content)
                current_account.request_count += 1
                current_account.last_used = time.time()
                get_rate_limiter().record_request(current_account.id)

                # 完成 Flow
                if flow_id:
                    full_text = "".join(result.get("content", []))
                    flow_monitor.complete_flow(
                        flow_id,
                        status_code=200,
                        content=full_text,
                        tool_calls=result.get("tool_uses", []),
                        stop_reason=result.get("stop_reason", ""),
                        usage=TokenUsage(
                            input_tokens=result.get("input_tokens", 0),
                            output_tokens=result.get("output_tokens", 0),
                        ),
                    )

                if thinking_enabled:
                    result = dict(result)
                    full_text = "".join(result.get("content", []) or [])
                    thinking_content, clean_text = extract_thinking_from_content(full_text)
                    result["content"] = [clean_text] if clean_text else []

                resp = convert_kiro_response_to_anthropic(result, model, f"msg_{log_id}")
                if thinking_enabled:
                    resp["content"].insert(0, {"type": "thinking", "thinking": thinking_content or ""})
                return resp

        except HTTPException:
            raise
        except httpx.TimeoutException as e:
            error_msg = f"Request timeout: {e}"
            status_code = 408
            if retry < max_retries:
                print(f"[NonStream] 请求超时，重试 {retry + 1}/{max_retries}")
                await retry_ctx.wait()
                continue
            if flow_id:
                flow_monitor.fail_flow(flow_id, "timeout_error", "Request timeout after retries", 408)
            raise HTTPException(408, "Request timeout after retries")
        except httpx.ConnectError as e:
            error_msg = f"Connection error: {e}"
            status_code = 502
            if retry < max_retries:
                print(f"[NonStream] 连接错误，重试 {retry + 1}/{max_retries}")
                await retry_ctx.wait()
                continue
            if flow_id:
                flow_monitor.fail_flow(flow_id, "connection_error", "Connection error after retries", 502)
            raise HTTPException(502, "Connection error after retries")
        except Exception as e:
            error_msg = str(e)
            status_code = 500
            # 检查是否为可重试的网络错误
            if is_retryable_error(None, e) and retry < max_retries:
                print(f"[NonStream] 网络错误，重试 {retry + 1}/{max_retries}: {type(e).__name__}")
                await retry_ctx.wait()
                continue
            if flow_id:
                flow_monitor.fail_flow(flow_id, "api_error", str(e), 500)
            raise HTTPException(500, str(e))
        finally:
            if retry == max_retries or status_code == 200:
                duration = (time.time() - start_time) * 1000
                state.add_log(RequestLog(
                    id=log_id,
                    timestamp=time.time(),
                    method="POST",
                    path="/v1/messages",
                    model=model,
                    account_id=current_account.id if current_account else None,
                    status=status_code,
                    duration_ms=duration,
                    error=error_msg
                ))
                # 记录统计
                stats_manager.record_request(
                    account_id=current_account.id if current_account else "unknown",
                    model=model,
                    success=status_code == 200,
                    latency_ms=duration
                )
    
    raise HTTPException(503, "All retries exhausted")


class ThinkingStreamProcessor:
    """思考内容流式处理器"""

    _THINKING_START_TAG = "<thinking>"
    _THINKING_END_TAG = "</thinking>"
    
    def __init__(self, thinking_enabled: bool = False, index_ref: list | None = None):
        self.thinking_enabled = thinking_enabled
        self.thinking_buffer = ""
        self.in_thinking_block = False
        self.thinking_extracted = False
        self.text_buffer = ""
        self._index_ref = index_ref if index_ref is not None else [0]
        self._text_index = None
        self._thinking_index = None

    def _next_index(self) -> int:
        self._index_ref[0] += 1
        return self._index_ref[0] - 1

    @staticmethod
    def _split_incomplete_tag_tail(buffer: str, tag: str) -> tuple[str, str]:
        """Split buffer into (flush, keep) parts to handle tags split across chunks.

        Keeps the longest suffix of `buffer` that could be a prefix of `tag` so the
        next chunk can complete the tag.
        """
        if not buffer:
            return "", ""
        max_suffix_len = min(len(tag) - 1, len(buffer))
        for suffix_len in range(max_suffix_len, 0, -1):
            if buffer.endswith(tag[:suffix_len]):
                return buffer[:-suffix_len], buffer[-suffix_len:]
        return buffer, ""
    
    def process_content(self, content: str) -> list:
        """处理新到达的内容块，返回生成的事件列表"""
        events = []
        
        if not self.thinking_enabled:
            # 如果未启用思考模式，直接返回文本内容
            if self._text_index is None:
                self._text_index = self._next_index()
                events.append({
                    "type": "content_block_start",
                    "index": self._text_index,
                    "content_block": {"type": "text", "text": ""}
                })
            events.append({
                "type": "content_block_delta",
                "index": self._text_index,
                "delta": {"type": "text_delta", "text": content}
            })
            return events
        
        # 将内容添加到缓冲区
        self.text_buffer += content
        
        # 查找思考标签
        while self.text_buffer:
            if not self.in_thinking_block:
                # 查找思考开始标签
                start_idx = find_real_thinking_start_tag(self.text_buffer)
                if start_idx == -1:
                    # 没有找到思考标签：只输出安全部分，保留可能被拆分的标签前缀
                    flush_text, keep = self._split_incomplete_tag_tail(
                        self.text_buffer, self._THINKING_START_TAG
                    )
                    if flush_text:
                        if self._text_index is None:
                            self._text_index = self._next_index()
                            events.append({
                                "type": "content_block_start",
                                "index": self._text_index,
                                "content_block": {"type": "text", "text": ""}
                            })
                        events.append({
                            "type": "content_block_delta",
                            "index": self._text_index,
                            "delta": {"type": "text_delta", "text": flush_text}
                        })
                    self.text_buffer = keep
                    break
                
                # 输出思考标签之前的文本
                if start_idx > 0:
                    text_before = self.text_buffer[:start_idx]
                    if self._text_index is None:
                        self._text_index = self._next_index()
                        events.append({
                            "type": "content_block_start",
                            "index": self._text_index,
                            "content_block": {"type": "text", "text": ""}
                        })
                    events.append({
                        "type": "content_block_delta",
                        "index": self._text_index,
                        "delta": {"type": "text_delta", "text": text_before}
                    })
                
                # 开始思考块
                self.in_thinking_block = True
                self._thinking_index = self._next_index()
                events.append({
                    "type": "content_block_start",
                    "index": self._thinking_index,
                    "content_block": {"type": "thinking", "thinking": ""}
                })
                
                # 移除已处理的内容
                self.text_buffer = self.text_buffer[start_idx + len(self._THINKING_START_TAG):]
            
            else:
                # 在思考块内，查找结束标签
                end_idx = find_real_thinking_end_tag(self.text_buffer)
                if end_idx == -1:
                    # 没有找到结束标签：只输出安全部分，保留可能被拆分的结束标签前缀
                    flush_thinking, keep = self._split_incomplete_tag_tail(
                        self.text_buffer, self._THINKING_END_TAG
                    )
                    if flush_thinking:
                        self.thinking_buffer += flush_thinking
                        events.append({
                            "type": "content_block_delta",
                            "index": self._thinking_index,
                            "delta": {"type": "thinking_delta", "thinking": flush_thinking}
                        })
                    self.text_buffer = keep
                    break
                
                # 找到结束标签，输出思考内容
                thinking_content = self.text_buffer[:end_idx]
                self.thinking_buffer += thinking_content
                if thinking_content:
                    events.append({
                        "type": "content_block_delta",
                        "index": self._thinking_index,
                        "delta": {"type": "thinking_delta", "thinking": thinking_content}
                    })
                
                # 结束思考块
                events.append({
                    "type": "content_block_stop",
                    "index": self._thinking_index
                })
                
                self.in_thinking_block = False
                self.thinking_extracted = True
                
                # 移除已处理的内容
                self.text_buffer = self.text_buffer[end_idx + len(self._THINKING_END_TAG):]
        
        return events
    
    def finalize(self) -> list:
        """完成处理，返回结束事件"""
        events = []
        
        # 刷出残留缓冲（可能包含被拆分的标签片段或尾部文本）
        if self.text_buffer:
            if self.in_thinking_block:
                if self._thinking_index is None:
                    self._thinking_index = self._next_index()
                    events.append({
                        "type": "content_block_start",
                        "index": self._thinking_index,
                        "content_block": {"type": "thinking", "thinking": ""}
                    })
                self.thinking_buffer += self.text_buffer
                events.append({
                    "type": "content_block_delta",
                    "index": self._thinking_index,
                    "delta": {"type": "thinking_delta", "thinking": self.text_buffer}
                })
            else:
                if self._text_index is None:
                    self._text_index = self._next_index()
                    events.append({
                        "type": "content_block_start",
                        "index": self._text_index,
                        "content_block": {"type": "text", "text": ""}
                    })
                events.append({
                    "type": "content_block_delta",
                    "index": self._text_index,
                    "delta": {"type": "text_delta", "text": self.text_buffer}
                })
            self.text_buffer = ""

        if self.in_thinking_block and self._thinking_index is not None:
            # 如果还在思考块内，强制结束
            events.append({
                "type": "content_block_stop",
                "index": self._thinking_index
            })
            self.in_thinking_block = False
        if self._text_index is not None:
            events.append({
                "type": "content_block_stop",
                "index": self._text_index
            })
        
        return events
