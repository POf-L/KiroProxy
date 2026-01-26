# Kiro 请求抓包指南

本项目提供 `scripts/capture_kiro.py`（mitmproxy addon）用于抓取 Kiro IDE 与 AWS 端点的请求/响应，便于调试协议与字段。

## 安装依赖

```bash
pip install mitmproxy
```

## 运行 mitmproxy

带 UI：

```bash
mitmproxy --mode regular@8888 -s scripts/capture_kiro.py
```

无 UI：

```bash
mitmdump --mode regular@8888 -s scripts/capture_kiro.py
```

## 配置代理与证书

- 将系统或 Kiro IDE 的 HTTP/HTTPS 代理设置为 `127.0.0.1:8888`
- 如需解密 HTTPS，安装 mitmproxy CA 证书：访问 `http://mitm.it`

## 输出文件

抓到的请求/响应会输出到 `kiro_requests/`：

- `*_request.json`
- `*_response.json`

`kiro_requests/` 已在 `.gitignore` 中忽略。
