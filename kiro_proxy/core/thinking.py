"""Thinking / Extended Thinking helpers.

This project implements "thinking" at the proxy layer by injecting thinking configuration
into user prompts. The model outputs thinking content in <thinking>...</thinking> tags
followed by the final response, which is then parsed and split into separate blocks.

Notes:
- Kiro's upstream API doesn't expose a native "thinking budget" knob, so `budget_tokens`
  is enforced only via prompt instructions (best-effort).
- If the client does not provide a budget, we treat it as "unlimited" (no prompt limit).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

import re


@dataclass(frozen=True)
class ThinkingConfig:
    enabled: bool
    budget_tokens: Optional[int] = None  # None == unlimited


def _coerce_bool(value: Any) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        v = value.strip().lower()
        if v in {"true", "1", "yes", "y", "on", "enabled"}:
            return True
        if v in {"false", "0", "no", "n", "off", "disabled"}:
            return False
    return None


def _coerce_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        v = value.strip()
        if not v:
            return None
        try:
            return int(v)
        except ValueError:
            return None
    return None


def normalize_thinking_config(raw: Any) -> ThinkingConfig:
    """Normalize multiple "thinking" shapes into a single config.

    Supported shapes (best-effort):
    - None / missing: disabled
    - bool: enabled/disabled
    - str: "enabled"/"disabled"
    - dict:
        - {"type": "enabled", "budget_tokens": 20000} (Anthropic style)
        - {"thinking_type": "enabled", "budget_tokens": 20000} (legacy)
        - {"enabled": true, "budget_tokens": 20000}
        - {"includeThoughts": true, "thinkingBudget": 20000} (Gemini-ish)
    """
    if raw is None:
        return ThinkingConfig(enabled=False, budget_tokens=None)

    bool_value = _coerce_bool(raw)
    if bool_value is not None and not isinstance(raw, dict):
        return ThinkingConfig(enabled=bool_value, budget_tokens=None)

    if isinstance(raw, dict):
        mode = raw.get("type") or raw.get("thinking_type") or raw.get("mode")
        enabled = None
        if isinstance(mode, str):
            enabled = _coerce_bool(mode)
        if enabled is None:
            enabled = _coerce_bool(raw.get("enabled"))
        if enabled is None:
            enabled = _coerce_bool(raw.get("includeThoughts") or raw.get("include_thoughts"))
        if enabled is None:
            enabled = False

        budget_tokens = None
        for key in (
            "budget_tokens",
            "budgetTokens",
            "thinkingBudget",
            "thinking_budget",
            "max_thinking_length",
            "maxThinkingLength",
        ):
            if key in raw:
                budget_tokens = _coerce_int(raw.get(key))
                break
        if budget_tokens is not None and budget_tokens <= 0:
            budget_tokens = None

        return ThinkingConfig(enabled=bool(enabled), budget_tokens=budget_tokens)

    if isinstance(raw, str):
        enabled = _coerce_bool(raw)
        return ThinkingConfig(enabled=bool(enabled), budget_tokens=None)

    return ThinkingConfig(enabled=False, budget_tokens=None)


def map_openai_reasoning_effort_to_budget(effort: Any) -> Optional[int]:
    """Map OpenAI-style reasoning effort into a best-effort budget.

    We keep this generous; if effort is "high", treat as unlimited.
    """
    if not isinstance(effort, str):
        return None
    v = effort.strip().lower()
    if v in {"high"}:
        return None
    if v in {"medium"}:
        return 20000
    if v in {"low"}:
        return 10000
    return None


def extract_thinking_config_from_openai_body(body: dict) -> tuple[ThinkingConfig, bool]:
    """Extract thinking config from OpenAI ChatCompletions/Responses-style bodies."""
    if not isinstance(body, dict):
        return ThinkingConfig(False, None), False

    if "thinking" in body:
        return normalize_thinking_config(body.get("thinking")), True

    # OpenAI Responses API style
    reasoning = body.get("reasoning")
    if "reasoning" in body:
        if isinstance(reasoning, dict):
            effort = reasoning.get("effort")
            if isinstance(effort, str) and effort.strip().lower() in {"low", "medium", "high"}:
                return ThinkingConfig(True, map_openai_reasoning_effort_to_budget(effort)), True
        cfg = normalize_thinking_config(reasoning)
        return cfg, True

    effort = body.get("reasoning_effort")
    if "reasoning_effort" in body and isinstance(effort, str) and effort.strip().lower() in {"low", "medium", "high"}:
        return ThinkingConfig(True, map_openai_reasoning_effort_to_budget(effort)), True

    return ThinkingConfig(False, None), False


def extract_thinking_config_from_gemini_body(body: dict) -> tuple[ThinkingConfig, bool]:
    """Extract thinking config from Gemini generateContent bodies (best-effort)."""
    if not isinstance(body, dict):
        return ThinkingConfig(False, None), False

    if "thinking" in body:
        return normalize_thinking_config(body.get("thinking")), True

    if "thinkingConfig" in body:
        return normalize_thinking_config(body.get("thinkingConfig")), True

    gen_cfg = body.get("generationConfig")
    if isinstance(gen_cfg, dict):
        if "thinkingConfig" in gen_cfg:
            raw = gen_cfg.get("thinkingConfig")
            cfg = normalize_thinking_config(raw)
            if cfg.enabled:
                return cfg, True
            # Budget without explicit includeThoughts/mode: treat as enabled (client guidance exists)
            if isinstance(raw, dict) and any(
                k in raw for k in ("thinkingBudget", "budgetTokens", "budget_tokens", "max_thinking_length")
            ):
                return ThinkingConfig(True, cfg.budget_tokens), True
            return cfg, True

    return ThinkingConfig(False, None), False


def infer_thinking_from_anthropic_messages(messages: list[dict]) -> bool:
    """推断历史消息中是否包含思维链内容，用于在客户端未明确指定时自动启用思维链"""
    for msg in messages or []:
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict):
                # 检查标准的 thinking 块
                if block.get("type") == "thinking":
                    return True
                # 检查文本块中嵌入的 <thinking> 标签（assistant 消息中可能存在）
                if block.get("type") == "text" and msg.get("role") == "assistant":
                    text = block.get("text", "")
                    if isinstance(text, str) and "<thinking>" in text and "</thinking>" in text:
                        return True
    return False


def infer_thinking_from_openai_messages(messages: list[dict]) -> bool:
    for msg in messages or []:
        content = msg.get("content", "")
        if isinstance(content, str):
            if "<thinking>" in content and "</thinking>" in content:
                return True
            continue
        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    text = part.get("text", "")
                    if "<thinking>" in text and "</thinking>" in text:
                        return True
    return False


def infer_thinking_from_openai_responses_input(input_data: Any) -> bool:
    """Infer thinking from OpenAI Responses API `input` payloads (best-effort)."""
    if isinstance(input_data, str):
        return "<thinking>" in input_data and "</thinking>" in input_data

    if not isinstance(input_data, list):
        return False

    for item in input_data:
        if not isinstance(item, dict):
            continue
        if item.get("type") != "message":
            continue

        content_list = item.get("content", []) or []
        for c in content_list:
            if isinstance(c, str):
                if "<thinking>" in c and "</thinking>" in c:
                    return True
                continue
            if not isinstance(c, dict):
                continue
            c_type = c.get("type")
            if c_type in {"input_text", "output_text", "text"}:
                text = c.get("text", "")
                if isinstance(text, str) and "<thinking>" in text and "</thinking>" in text:
                    return True
    return False


def infer_thinking_from_gemini_contents(contents: list[dict]) -> bool:
    for item in contents or []:
        for part in item.get("parts", []) or []:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                text = part["text"]
                if "<thinking>" in text and "</thinking>" in text:
                    return True
    return False


import re

_THINKING_PATTERN = re.compile(r"<thinking>.*?</thinking>\s*", re.DOTALL)


def strip_thinking_from_text(text: str) -> str:
    """Remove <thinking> blocks from text."""
    if not text or not isinstance(text, str):
        return text
    return _THINKING_PATTERN.sub("", text).strip()


def strip_thinking_from_history(history: list) -> list:
    """Return a copy of history with <thinking> blocks removed from all messages."""
    if not history:
        return []
    
    cleaned = []
    for msg in history:
        if not isinstance(msg, dict):
            cleaned.append(msg)
            continue
        
        new_msg = msg.copy()
        content = msg.get("content")
        
        if isinstance(content, str):
            new_msg["content"] = strip_thinking_from_text(content)
        elif isinstance(content, list):
            new_content = []
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    new_part = part.copy()
                    new_part["text"] = strip_thinking_from_text(part.get("text", ""))
                    new_content.append(new_part)
                else:
                    new_content.append(part)
            new_msg["content"] = new_content
        
        cleaned.append(new_msg)
    
    return cleaned


def format_thinking_block(thinking_content: str) -> str:
    if thinking_content is None:
        return ""
    thinking_content = str(thinking_content).strip()
    if not thinking_content:
        return ""
    return f"<thinking>\n{thinking_content}\n</thinking>"


def build_thinking_prompt(
    user_content: str,
    *,
    budget_tokens: Optional[int],
    history: list = None,
    has_tool_results: bool = False
) -> str:
    """Build a thinking prompt for kiro.rs compatible single-stage processing.

    Returns thinking configuration in kiro.rs compatible format:
    <thinking_mode>enabled</thinking_mode><max_thinking_length>{budget_tokens}</max_thinking_length>

    This format is compatible with hank9999/kiro.rs project's thinking implementation.
    The model will output thinking content in <thinking>...</thinking> tags followed by the final response.

    Note: budget_tokens is now ignored - thinking is always unlimited to allow the model to think as long as needed.
    """
    # 强制使用无限制思考预算，忽略客户端传入的限制
    budget = 999999

    thinking_config = f"<thinking_mode>enabled</thinking_mode><max_thinking_length>{budget}</max_thinking_length>"

    user_content = user_content.strip() if user_content else ""
    if not user_content:
        user_content = "Continue the conversation based on the history."

    return f"""{thinking_config}

IMPORTANT: Use the <thinking> tags for your internal reasoning and analysis. After your thinking, provide your final response directly to the user WITHOUT repeating or referencing the thinking content. The thinking should be completely separate from your final answer.

{user_content}"""
