from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from kiro_proxy.core.thinking import (
    ThinkingConfig,
    build_thinking_prompt,
    extract_thinking_config_from_gemini_body,
    extract_thinking_config_from_openai_body,
    infer_thinking_from_anthropic_messages,
    infer_thinking_from_gemini_contents,
    infer_thinking_from_openai_messages,
    infer_thinking_from_openai_responses_input,
    normalize_thinking_config,
)


def test_normalize_thinking_config_defaults_to_disabled_unlimited():
    cfg = normalize_thinking_config(None)
    assert cfg == ThinkingConfig(False, None)


@pytest.mark.parametrize(
    "raw,expected",
    [
        (True, ThinkingConfig(True, None)),
        ("enabled", ThinkingConfig(True, None)),
        ({"type": "enabled"}, ThinkingConfig(True, None)),
        ({"thinking_type": "enabled", "budget_tokens": 20000}, ThinkingConfig(True, 20000)),
        ({"enabled": True, "budget_tokens": 0}, ThinkingConfig(True, None)),
        ({"includeThoughts": True, "thinkingBudget": 1234}, ThinkingConfig(True, 1234)),
        ({"type": "disabled", "budget_tokens": 9999}, ThinkingConfig(False, 9999)),
    ],
)
def test_normalize_thinking_config_variants(raw, expected):
    assert normalize_thinking_config(raw) == expected


def test_extract_thinking_config_from_openai_body():
    cfg, explicit = extract_thinking_config_from_openai_body({})
    assert cfg == ThinkingConfig(False, None)
    assert explicit is False

    cfg, explicit = extract_thinking_config_from_openai_body({"thinking": {"type": "enabled"}})
    assert cfg.enabled is True
    assert explicit is True

    cfg, explicit = extract_thinking_config_from_openai_body({"reasoning_effort": "high"})
    assert cfg.enabled is True
    assert cfg.budget_tokens is None
    assert explicit is True

    cfg, explicit = extract_thinking_config_from_openai_body({"reasoning": {"effort": "medium"}})
    assert cfg == ThinkingConfig(True, 20000)
    assert explicit is True


def test_extract_thinking_config_from_gemini_body():
    cfg, explicit = extract_thinking_config_from_gemini_body({})
    assert cfg == ThinkingConfig(False, None)
    assert explicit is False

    cfg, explicit = extract_thinking_config_from_gemini_body(
        {"generationConfig": {"thinkingConfig": {"includeThoughts": True, "thinkingBudget": 1234}}}
    )
    assert cfg == ThinkingConfig(True, 1234)
    assert explicit is True


def test_infer_thinking_from_payloads():
    assert (
        infer_thinking_from_anthropic_messages(
            [{"role": "assistant", "content": [{"type": "thinking", "thinking": "x"}]}]
        )
        is True
    )

    assert infer_thinking_from_openai_messages(
        [{"role": "assistant", "content": "<thinking>AAA</thinking>\nBBB"}]
    )

    assert infer_thinking_from_openai_responses_input(
        [
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "<thinking>AAA</thinking>BBB"}],
            }
        ]
    )

    assert infer_thinking_from_gemini_contents(
        [{"role": "model", "parts": [{"text": "<thinking>AAA</thinking>\nBBB"}]}]
    )


def test_thinking_prompts_emphasize_language_following():
    p1 = build_thinking_prompt("hi", budget_tokens=None)
    assert "Start thinking directly:" in p1
    assert "follow the user's language (use the same language as the user)" in p1
    assert "Only output your thinking process" in p1
    assert "Do not provide any final answer" in p1
    assert "hi" in p1

    p2 = build_thinking_prompt("你好", budget_tokens=123)  # Chinese input
    assert "Start thinking directly:" in p2
    assert "follow the user's language (use the same language as the user)" in p2
    assert "Only output your thinking process" in p2
    assert "This is internal reasoning only" in p2
    assert "你好" in p2
