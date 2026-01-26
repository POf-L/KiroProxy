def test_openai_converter_strips_thinking_from_assistant_history():
    from kiro_proxy.converters import convert_openai_messages_to_kiro

    messages = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "<thinking>AAA</thinking>\n\nHello"},
        {"role": "user", "content": "next"},
    ]

    user_content, history, _, _ = convert_openai_messages_to_kiro(messages, model="claude-sonnet-4")
    assert user_content == "next"

    assistant_msgs = [h for h in history if "assistantResponseMessage" in h]
    assert assistant_msgs, "expected at least one assistant message in history"
    assert "<thinking>" not in assistant_msgs[0]["assistantResponseMessage"]["content"]


def test_gemini_converter_strips_thinking_from_model_history():
    from kiro_proxy.converters import convert_gemini_contents_to_kiro

    contents = [
        {"role": "user", "parts": [{"text": "hi"}]},
        {"role": "model", "parts": [{"text": "<thinking>AAA</thinking>\nOK"}]},
        {"role": "user", "parts": [{"text": "next"}]},
    ]

    user_content, history, _, _ = convert_gemini_contents_to_kiro(
        contents, system_instruction={}, model="claude-sonnet-4"
    )
    assert user_content == "next"

    assistant_msgs = [h for h in history if "assistantResponseMessage" in h]
    assert assistant_msgs, "expected at least one assistant message in history"
    assert "<thinking>" not in assistant_msgs[0]["assistantResponseMessage"]["content"]


def test_anthropic_converter_strips_thinking_from_assistant_text():
    from kiro_proxy.converters import convert_anthropic_messages_to_kiro

    messages = [
        {"role": "user", "content": [{"type": "text", "text": "hi"}]},
        {"role": "assistant", "content": [{"type": "text", "text": "<thinking>AAA</thinking>\nAnswer"}]},
        {"role": "user", "content": [{"type": "text", "text": "next"}]},
    ]

    user_content, history, _ = convert_anthropic_messages_to_kiro(messages, system="")
    assert user_content == "next"

    assistant_msgs = [h for h in history if "assistantResponseMessage" in h]
    assert assistant_msgs, "expected at least one assistant message in history"
    assert "<thinking>" not in assistant_msgs[0]["assistantResponseMessage"]["content"]


def test_responses_input_converter_strips_thinking_from_assistant_text():
    from kiro_proxy.handlers.responses import _convert_responses_input_to_kiro

    input_data = [
        {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "hi"}]},
        {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "<thinking>AAA</thinking>\nAnswer"}]},
        {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "next"}]},
    ]

    user_content, history, _, _ = _convert_responses_input_to_kiro(input_data)
    assert user_content == "next"

    assistant_msgs = [h for h in history if "assistantResponseMessage" in h]
    assert assistant_msgs, "expected at least one assistant message in history"
    assert "<thinking>" not in assistant_msgs[0]["assistantResponseMessage"]["content"]

