#!/usr/bin/env python3
"""
测试思考功能
"""
import asyncio
import json
import httpx

async def test_thinking_feature():
    """测试思考功能"""
    
    # 测试数据
    test_data = {
        "model": "claude-sonnet-4.5",
        "messages": [
            {
                "role": "user",
                "content": "请解释什么是递归，并给出一个简单的例子。"
            }
        ],
        "thinking": {
            "thinking_type": "enabled",
            "budget_tokens": 5000
        },
        "stream": True,
        "max_tokens": 1000
    }
    
    print("发送思考功能测试请求...")
    print(f"请求内容: {json.dumps(test_data, indent=2, ensure_ascii=False)}")
    print("\n" + "="*60 + "\n")
    
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            async with client.stream(
                "POST",
                "http://localhost:8080/v1/messages",
                headers={
                    "Content-Type": "application/json",
                    "x-api-key": "any",
                    "anthropic-version": "2023-06-01"
                },
                json=test_data
            ) as response:
                
                if response.status_code != 200:
                    print(f"错误: {response.status_code}")
                    print(await response.aread())
                    return
                
                print("收到响应:\n")
                
                thinking_content = []
                text_content = []
                current_block = None
                
                async for line in response.aiter_lines():
                    if line.startswith("data: "):
                        data_str = line[6:]
                        
                        if data_str == "[DONE]":
                            break
                        
                        try:
                            data = json.loads(data_str)
                            event_type = data.get("type")
                            
                            if event_type == "content_block_start":
                                block_type = data.get("content_block", {}).get("type")
                                current_block = block_type
                                print(f"\n[开始 {block_type} 块]")
                                
                            elif event_type == "content_block_delta":
                                delta = data.get("delta", {})
                                
                                if delta.get("type") == "thinking_delta":
                                    thinking = delta.get("thinking", "")
                                    thinking_content.append(thinking)
                                    print(thinking, end="", flush=True)
                                    
                                elif delta.get("type") == "text_delta":
                                    text = delta.get("text", "")
                                    text_content.append(text)
                                    print(text, end="", flush=True)
                            
                            elif event_type == "content_block_stop":
                                print(f"\n[结束块]")
                                current_block = None
                                
                            elif event_type == "message_stop":
                                print("\n\n[响应完成]")
                                break
                            
                            elif event_type == "error":
                                print("\n\n[错误]")
                                print(json.dumps(data.get("error", data), ensure_ascii=False, indent=2))
                                break
                                
                        except json.JSONDecodeError as e:
                            print(f"\n解析错误: {e}")
                            continue
                
                print("\n" + "="*60)
                print("\n思考内容汇总:")
                print("".join(thinking_content))
                
                print("\n" + "-"*40)
                print("\n回答内容汇总:")
                print("".join(text_content))
                
    except Exception as e:
        print(f"请求失败: {e}")

if __name__ == "__main__":
    asyncio.run(test_thinking_feature())
