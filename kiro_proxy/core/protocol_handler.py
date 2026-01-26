"""自定义协议处理器

在 Windows 上注册 kiro:// 协议，用于处理 OAuth 回调。
"""
import sys
import os
import asyncio
import threading
from pathlib import Path
from typing import Optional, Callable
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, urlencode
import socket


# 回调服务器端口
CALLBACK_PORT = 19823

# 全局回调结果
_callback_result = None
_callback_event = None
_callback_server = None
_server_thread = None


class CallbackHandler(BaseHTTPRequestHandler):
    """处理 OAuth 回调的 HTTP 请求处理器"""
    
    def log_message(self, format, *args):
        """禁用日志输出"""
        pass
    
    def do_GET(self):
        global _callback_result, _callback_event
        
        # 解析 URL
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        
        # 检查是否是回调路径
        if parsed.path == '/kiro-callback' or parsed.path == '/' or 'code' in params:
            code = params.get('code', [None])[0]
            state = params.get('state', [None])[0]
            error = params.get('error', [None])[0]
            
            print(f"[ProtocolHandler] 收到回调: code={code[:20] if code else None}..., state={state}, error={error}")
            
            if error:
                _callback_result = {"error": error}
            elif code and state:
                _callback_result = {"code": code, "state": state}
            else:
                _callback_result = {"error": "缺少授权码"}
            
            # 触发事件
            if _callback_event:
                _callback_event.set()
            
            # 返回成功页面
            self.send_response(200)
            self.send_header('Content-type', 'text/html; charset=utf-8')
            self.end_headers()
            
            html = """
            <!DOCTYPE html>
            <html>
            <head>
                <meta charset="utf-8">
                <title>登录成功</title>
                <style>
                    body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; 
                           display: flex; justify-content: center; align-items: center; height: 100vh; 
                           margin: 0; background: #1a1a2e; color: #fff; }
                    .container { text-align: center; padding: 2rem; }
                    h1 { color: #4ade80; margin-bottom: 1rem; }
                    p { color: #9ca3af; }
                </style>
            </head>
            <body>
                <div class="container">
                    <h1>✅ 登录成功</h1>
                    <p>您可以关闭此窗口并返回 Kiro Proxy</p>
                    <script>setTimeout(function(){window.close();}, 3000);</script>
                </div>
            </body>
            </html>
            """
            self.wfile.write(html.encode('utf-8'))
        else:
            self.send_response(404)
            self.end_headers()


def is_port_available(port: int) -> bool:
    """检查端口是否可用"""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(('127.0.0.1', port))
            return True
    except OSError:
        return False


def start_callback_server() -> tuple:
    """启动回调服务器
    
    Returns:
        (success, port or error)
    """
    global _callback_server, _callback_result, _callback_event, _server_thread
    
    # 如果服务器已经在运行，直接返回成功
    if _callback_server is not None and _server_thread is not None and _server_thread.is_alive():
        print(f"[ProtocolHandler] 回调服务器已在运行: http://127.0.0.1:{CALLBACK_PORT}")
        return True, CALLBACK_PORT
    
    _callback_result = None
    _callback_event = threading.Event()
    
    # 检查端口
    if not is_port_available(CALLBACK_PORT):
        # 端口被占用，可能是之前的服务器还在运行
        print(f"[ProtocolHandler] 端口 {CALLBACK_PORT} 已被占用，尝试复用")
        return True, CALLBACK_PORT
    
    try:
        _callback_server = HTTPServer(('127.0.0.1', CALLBACK_PORT), CallbackHandler)
        
        # 在后台线程运行服务器
        _server_thread = threading.Thread(target=_callback_server.serve_forever, daemon=True)
        _server_thread.start()
        
        print(f"[ProtocolHandler] 回调服务器已启动: http://127.0.0.1:{CALLBACK_PORT}")
        return True, CALLBACK_PORT
    except Exception as e:
        return False, str(e)


def stop_callback_server():
    """停止回调服务器"""
    global _callback_server, _server_thread
    
    if _callback_server:
        try:
            _callback_server.shutdown()
        except:
            pass
        _callback_server = None
        _server_thread = None
        print("[ProtocolHandler] 回调服务器已停止")


def wait_for_callback(timeout: int = 300) -> tuple:
    """等待回调
    
    Args:
        timeout: 超时时间（秒）
    
    Returns:
        (success, result or error)
    """
    global _callback_result, _callback_event
    
    if _callback_event is None:
        return False, {"error": "回调服务器未启动"}
    
    # 等待回调
    if _callback_event.wait(timeout=timeout):
        if _callback_result and "code" in _callback_result:
            return True, _callback_result
        elif _callback_result and "error" in _callback_result:
            return False, _callback_result
        else:
            return False, {"error": "未收到有效回调"}
    else:
        return False, {"error": "等待回调超时"}


def get_callback_result() -> Optional[dict]:
    """获取回调结果（非阻塞）"""
    global _callback_result
    return _callback_result


def clear_callback_result():
    """清除回调结果"""
    global _callback_result, _callback_event
    _callback_result = None
    if _callback_event:
        _callback_event.clear()


# Windows 协议注册
def register_protocol_windows() -> tuple:
    """在 Windows 上注册 kiro:// 协议
    
    注册后，当浏览器重定向到 kiro:// URL 时，Windows 会调用我们的脚本，
    脚本将参数重定向到本地 HTTP 服务器。
    
    Returns:
        (success, message)
    """
    if sys.platform != 'win32':
        return False, "仅支持 Windows"
    
    try:
        import winreg
        
        # 获取当前 Python 解释器路径
        python_exe = sys.executable
        
        # 创建一个处理脚本
        script_dir = Path.home() / ".kiro-proxy"
        script_dir.mkdir(parents=True, exist_ok=True)
        script_path = script_dir / "protocol_redirect.pyw"
        
        # 写入重定向脚本 (.pyw 不显示控制台窗口)
        script_content = f'''# -*- coding: utf-8 -*-
# Kiro Protocol Redirect Script
import sys
import webbrowser
from urllib.parse import urlparse, parse_qs, urlencode

if len(sys.argv) > 1:
    url = sys.argv[1]
    
    # 解析 kiro:// URL
    # 格式: kiro://kiro.kiroAgent/authenticate-success?code=xxx&state=xxx
    if url.startswith('kiro://'):
        # 提取查询参数
        query_start = url.find('?')
        if query_start > -1:
            query_string = url[query_start + 1:]
            # 重定向到本地 HTTP 服务器
            redirect_url = "http://127.0.0.1:{CALLBACK_PORT}/kiro-callback?" + query_string
            webbrowser.open(redirect_url)
'''
        script_path.write_text(script_content, encoding='utf-8')
        
        # 获取 pythonw.exe 路径（无控制台窗口）
        python_dir = Path(python_exe).parent
        pythonw_exe = python_dir / "pythonw.exe"
        if not pythonw_exe.exists():
            pythonw_exe = python_exe  # 降级使用 python.exe
        
        # 注册协议
        key_path = r"SOFTWARE\\Classes\\kiro"
        
        # 创建主键
        key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path)
        winreg.SetValue(key, "", winreg.REG_SZ, "URL:Kiro Protocol")
        winreg.SetValueEx(key, "URL Protocol", 0, winreg.REG_SZ, "")
        winreg.CloseKey(key)
        
        # 创建 DefaultIcon 键
        icon_key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path + r"\\DefaultIcon")
        winreg.SetValue(icon_key, "", winreg.REG_SZ, f"{python_exe},0")
        winreg.CloseKey(icon_key)
        
        # 创建 shell\\open\\command 键
        cmd_key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path + r"\\shell\\open\\command")
        cmd = f'"{pythonw_exe}" "{script_path}" "%1"'
        winreg.SetValue(cmd_key, "", winreg.REG_SZ, cmd)
        winreg.CloseKey(cmd_key)
        
        print(f"[ProtocolHandler] 已注册 kiro:// 协议")
        print(f"[ProtocolHandler] 脚本路径: {script_path}")
        print(f"[ProtocolHandler] 命令: {cmd}")
        return True, "协议注册成功"
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return False, f"注册失败: {e}"


def unregister_protocol_windows() -> tuple:
    """取消注册 kiro:// 协议"""
    if sys.platform != 'win32':
        return False, "仅支持 Windows"
    
    try:
        import winreg
        
        def delete_key_recursive(key, subkey):
            try:
                open_key = winreg.OpenKey(key, subkey, 0, winreg.KEY_ALL_ACCESS)
                info = winreg.QueryInfoKey(open_key)
                for i in range(info[0]):
                    child = winreg.EnumKey(open_key, 0)
                    delete_key_recursive(open_key, child)
                winreg.CloseKey(open_key)
                winreg.DeleteKey(key, subkey)
            except WindowsError:
                pass
        
        delete_key_recursive(winreg.HKEY_CURRENT_USER, r"SOFTWARE\\Classes\\kiro")
        
        print("[ProtocolHandler] 已取消注册 kiro:// 协议")
        return True, "协议取消注册成功"
        
    except Exception as e:
        return False, f"取消注册失败: {e}"


def is_protocol_registered() -> bool:
    """检查 kiro:// 协议是否已注册"""
    if sys.platform != 'win32':
        return False
    
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"SOFTWARE\\Classes\\kiro")
        winreg.CloseKey(key)
        return True
    except WindowsError:
        return False

