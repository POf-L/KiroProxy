"""ThinkingStreamProcessor 单元测试

覆盖 <thinking> 标签在流式分片中被拆分的场景，避免思维链泄露到 text 输出。
"""

from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from kiro_proxy.handlers.anthropic import ThinkingStreamProcessor


def _collect_events(chunks: list[str]) -> list[dict]:
    processor = ThinkingStreamProcessor(thinking_enabled=True)
    events: list[dict] = []
    for chunk in chunks:
        events.extend(processor.process_content(chunk))
    events.extend(processor.finalize())
    return events


def _extract_text(events: list[dict]) -> str:
    return "".join(
        e["delta"]["text"]
        for e in events
        if e.get("type") == "content_block_delta"
        and e.get("delta", {}).get("type") == "text_delta"
    )


def _extract_thinking(events: list[dict]) -> str:
    return "".join(
        e["delta"]["thinking"]
        for e in events
        if e.get("type") == "content_block_delta"
        and e.get("delta", {}).get("type") == "thinking_delta"
    )


@pytest.mark.parametrize(
    "chunks,expected_thinking,expected_text",
    [
        # <thinking> 起始标签被拆分
        (["<thi", "nking>AAA</thinking>BBB"], "AAA", "BBB"),
        # </thinking> 结束标签被拆分
        (["<thinking>AAA</think", "ing>BBB"], "AAA", "BBB"),
        # 起始/结束标签都可能被拆分（跨多个分片）
        (["<thi", "nking>AAA</thi", "nking>BBB"], "AAA", "BBB"),
        # 无 thinking 标签：文本应保持原样
        (["Hello <thi", " there"], "", "Hello <thi there"),
        # 只有起始标签（无结束标签）：不得重复输出思考内容
        (["<thinking>AAA"], "AAA", ""),
    ],
)
def test_thinking_stream_processor_chunk_splitting(chunks, expected_thinking, expected_text):
    events = _collect_events(chunks)
    assert _extract_thinking(events) == expected_thinking
    assert _extract_text(events) == expected_text

    # 思考标签不应出现在 text 输出中
    text = _extract_text(events)
    assert "<thinking>" not in text
    assert "</thinking>" not in text

