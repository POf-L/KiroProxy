---
title: KiroProxy
emoji: 🚀
colorFrom: blue
colorTo: purple
sdk: docker
pinned: false
license: mit
short_description: Kiro IDE API 反向代理服务器 - 支持多账号管理和多协议转换
app_port: 7860
---

<p align="center">
  <img src="assets/icon.svg" width="80" height="96" alt="Kiro Proxy">
</p>

<h1 align="center">Kiro API Proxy</h1>

<p align="center">
  Kiro IDE API 反向代理服务器，支持多账号轮询、Token 自动刷新、配额管理
</p>

<p align="center">
  <a href="#功能特性">功能</a> ?
  <a href="#快速开始">快速开始</a> ?
  <a href="#cli-配置">CLI 配置</a> ?
  <a href="#api-端点">API</a> ?
  <a href="#许可证">许可证</a>
</p>

---

> **?? 测试说明**
> 
> 本项目支持 **Claude Code**、**Codex CLI**、**Gemini CLI** 三种客户端，工具调用功能已全面支持。

## 功能特性

### 核心功能
- **多协议支持** - OpenAI / Anthropic / Gemini 三种协议兼容
- **完整工具调用** - 三种协议的工具调用功能全面支持
- **图片理解** - 支持 Claude Code / Codex CLI 图片输入
- **网络搜索** - 支持 Claude Code / Codex CLI 网络搜索工具
- **思考功能** - 支持 Claude 的扩展思考功能（Extended Thinking）
- **多账号轮询（默认随机）** - 每次请求随机切换账号，分散压力，避免单账号 RPM 过高
- **会话粘性（可选）** - 非 `random` 策略下，同一会话 60 秒内使用同一账号，保持上下文
- **Web UI** - 简洁的管理界面，支持监控、日志、设置

### v1.7.1 新功能
- **Windows 支持补强** - 注册表浏览器检测 + PATH 回退，兼容便携版
- **打包资源修复** - PyInstaller 打包后可正常加载图标与内置文档
- **Token 扫描稳定性** - Windows 路径编码处理修复

### v1.6.3 新功能
- **命令行工具 (CLI)** - 无 GUI 服务器也能轻松管理
  - `python run.py accounts list` - 列出账号
  - `python run.py accounts export/import` - 导出/导入账号
  - `python run.py accounts add` - 交互式添加 Token
  - `python run.py accounts scan` - 扫描本地 Token
  - `python run.py login google/github` - 命令行登录
  - `python run.py login remote` - 生成远程登录链接
- **远程登录链接** - 在有浏览器的机器上完成授权，Token 自动同步
- **账号导入导出** - 跨机器迁移账号配置
- **手动添加 Token** - 直接粘贴 accessToken/refreshToken

### v1.6.2 新功能
- **Codex CLI 完整支持** - 使用 OpenAI Responses API (`/v1/responses`)
  - 完整工具调用支持（shell、file 等所有工具）
  - 图片输入支持（`input_image` 类型）
  - 网络搜索支持（`web_search` 工具）
  - 错误代码映射（rate_limit、context_length 等）
- **Claude Code 增强** - 图片理解和网络搜索完整支持
  - 支持 Anthropic 和 OpenAI 两种图片格式
  - 支持 `web_search` / `web_search_20250305` 工具

### v1.6.1 新功能
- **请求限速** - 通过限制请求频率降低账号封禁风险
  - 每账号最小请求间隔
  - 每账号每分钟最大请求数
  - 全局每分钟最大请求数
  - WebUI 设置页面可配置
- **账号封禁检测** - 自动检测 TEMPORARILY_SUSPENDED 错误
  - 友好的错误日志输出
  - 自动禁用被封禁账号
  - 自动切换到其他可用账号
- **统一错误处理** - 三种协议使用统一的错误分类和处理

### v1.6.0 功能
- **历史消息管理** - 4 种策略处理对话长度限制，可自由组合
  - 自动截断：发送前优先保留最新上下文并摘要前文，必要时按数量/字符数截断
  - 智能摘要：用 AI 生成早期对话摘要，保留关键信息
  - 摘要缓存：历史变化不大时复用最近摘要，减少重复 LLM 调用（默认启用）
  - 错误重试：遇到长度错误时自动截断重试（默认启用）
  - 预估检测：预估 token 数量，超限预先截断
- **Gemini 工具调用** - 完整支持 functionDeclarations/functionCall/functionResponse
- **设置页面** - WebUI 新增设置标签页，可配置历史消息管理策略

### v1.5.0 功能
- **用量查询** - 查询账号配额使用情况，显示已用/余额/使用率
- **多登录方式** - 支持 Google / GitHub / AWS Builder ID 三种登录方式
- **流量监控** - 完整的 LLM 请求监控，支持搜索、过滤、导出
- **浏览器选择** - 自动检测已安装浏览器，支持无痕模式
- **文档中心** - 内置帮助文档，左侧目录 + 右侧 Markdown 渲染

### v1.4.0 功能
- **Token 预刷新** - 后台每 5 分钟检查，提前 15 分钟自动刷新
- **健康检查** - 每 10 分钟检测账号可用性，自动标记状态
- **请求统计增强** - 按账号/模型统计，24 小时趋势
- **请求重试机制** - 网络错误/5xx 自动重试，指数退避

## 工具调用支持

| 功能 | Anthropic (Claude Code) | OpenAI (Codex CLI) | Gemini |
|------|------------------------|-------------------|--------|
| 工具定义 | ? `tools` | ? `tools.function` | ? `functionDeclarations` |
| 工具调用响应 | ? `tool_use` | ? `tool_calls` | ? `functionCall` |
| 工具结果 | ? `tool_result` | ? `tool` 角色消息 | ? `functionResponse` |
| 强制工具调用 | ? `tool_choice` | ? `tool_choice` | ? `toolConfig.mode` |
| 工具数量限制 | ? 50 个 | ? 50 个 | ? 50 个 |
| 历史消息修复 | ? | ? | ? |
| 图片理解 | ? | ? | ? |
| 网络搜索 | ? | ? | ? |

## 已知限制

### 对话长度限制

Kiro API 有输入长度限制。当对话历史过长时，会返回错误：

```
Input is too long. (CONTENT_LENGTH_EXCEEDS_THRESHOLD)
```

#### 自动处理（v1.6.0+）

代理内置了历史消息管理功能，可在「设置」页面配置：

- **错误重试**（默认）：遇到长度错误时自动截断并重试
- **智能摘要**：用 AI 生成早期对话摘要，保留关键信息
- **摘要缓存**（默认）：历史变化不大时复用最近摘要，减少重复 LLM 调用
- **自动截断**：每次请求前优先保留最新上下文并摘要前文，必要时按数量/字符数截断
- **预估检测**：预估 token 数量，超限预先截断

如需 **关闭自动压缩/重试**（超限时直接报错），可设置环境变量 `KIROPROXY_HISTORY_ERROR_RETRY=0`，或将历史配置的 `strategies` 中移除 `error_retry`。

摘要缓存可通过以下配置项调整（默认值）：
- `summary_cache_enabled`: `true`
- `summary_cache_min_delta_messages`: `3`
- `summary_cache_min_delta_chars`: `4000`
- `summary_cache_max_age_seconds`: `180`

#### 手动处理

1. 在 Claude Code 中输入 `/clear` 清空对话历史
2. 告诉 AI 你之前在做什么，它会读取代码文件恢复上下文

## 快速开始

### 方式一：下载预编译版本

从 [Releases](../../releases) 下载对应平台的安装包，解压后直接运行。

### 方式二：从源码运行

```bash
# 克隆项目
git clone https://github.com/yourname/kiro-proxy.git
cd kiro-proxy

# 创建虚拟环境
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# 安装依赖
pip install -r requirements.txt

# 运行
python run.py

# 或指定端口
python run.py 8081
```

启动后访问 http://localhost:8080

### 命令行工具 (CLI)

无 GUI 服务器可使用 CLI 管理账号：

```bash
# 账号管理
python run.py accounts list              # 列出账号
python run.py accounts export -o acc.json  # 导出账号
python run.py accounts import acc.json   # 导入账号
python run.py accounts add               # 交互式添加 Token
python run.py accounts scan --auto       # 扫描并自动添加本地 Token

# 登录
python run.py login google               # Google 登录
python run.py login github               # GitHub 登录
python run.py login remote --host myserver.com:8080  # 生成远程登录链接

# 服务
python run.py serve                      # 启动服务 (默认 8080)
python run.py serve -p 8081              # 指定端口
python run.py status                     # 查看状态
```

### 登录获取 Token

**方式一：在线登录（推荐）**
1. 打开 Web UI，点击「在线登录」
2. 选择登录方式：Google / GitHub / AWS Builder ID
3. 在浏览器中完成授权
4. 账号自动添加

**方式二：扫描 Token**
1. 打开 Kiro IDE，使用 Google/GitHub 账号登录
2. 登录成功后 token 自动保存到 `~/.aws/sso/cache/`
3. 在 Web UI 点击「扫描 Token」添加账号

## CLI 配置

### 模型对照表

| Kiro 模型 | 能力 | Claude Code | Codex |
|-----------|------|-------------|-------|
| `claude-sonnet-4` | ??? 推荐 | `claude-sonnet-4` | `gpt-4o` |
| `claude-sonnet-4.5` | ???? 更强 | `claude-sonnet-4.5` | `gpt-4o` |
| `claude-haiku-4.5` | ? 快速 | `claude-haiku-4.5` | `gpt-4o-mini` |

### Claude Code 配置

```
名称: Kiro Proxy
API Key: any
Base URL: http://localhost:8080
模型: claude-sonnet-4
```

### Codex 配置

Codex CLI 使用 OpenAI Responses API，配置如下：

```bash
# 设置环境变量
export OPENAI_API_KEY=any
export OPENAI_BASE_URL=http://localhost:8080/v1

# 运行 Codex
codex
```

或在 `~/.codex/config.toml` 中配置：

```toml
[providers.openai]
api_key = "any"
base_url = "http://localhost:8080/v1"
```

## 思考功能支持

### 什么是思考功能

思考功能（Extended Thinking）允许 Claude 在生成回答前展示其思考过程，帮助用户理解 AI 的推理步骤。

### 如何使用

在请求中添加 `thinking`（或对应协议的 thinking 配置）即可启用：

```json
{
  "model": "claude-sonnet-4.5",
  "messages": [
    {
      "role": "user",
      "content": "解释一下量子计算的原理"
    }
  ],
  "thinking": {
    "thinking_type": "enabled",
    "budget_tokens": 20000
  },
  "stream": true
}
```

OpenAI Chat Completions (`POST /v1/chat/completions`) 也支持：

```json
{
  "model": "gpt-4o",
  "messages": [{"role": "user", "content": "解释一下量子计算的原理"}],
  "thinking": { "type": "enabled" },
  "stream": true
}
```

OpenAI Responses (`POST /v1/responses`) 也支持：

```json
{
  "model": "gpt-4o",
  "input": "解释一下量子计算的原理",
  "thinking": { "type": "enabled" }
}
```

Gemini generateContent (`POST /v1/models/{model}:generateContent`) 也支持：

```json
{
  "contents": [{"role": "user", "parts": [{"text": "解释一下量子计算的原理"}]}],
  "generationConfig": {
    "thinkingConfig": { "includeThoughts": true }
  }
}
```

### 参数说明

- `thinking_type`: 思考类型，设为 `"enabled"` 启用思考功能
- `budget_tokens`: 思考过程的 token 预算（不传则视为无限制）

### 响应格式

启用思考功能后，流式响应会包含两种内容块：

1. **思考块**（type: "thinking"）：展示 AI 的思考过程
2. **文本块**（type: "text"）：最终的回答内容

示例响应：
```
data: {"type":"content_block_start","index":1,"content_block":{"type":"thinking","thinking":""}}
data: {"type":"content_block_delta","index":1,"delta":{"type":"thinking_delta","thinking":"让我思考一下量子计算的原理..."}}
data: {"type":"content_block_stop","index":1}
data: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}
data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"量子计算是一种..."}}
data: {"type":"content_block_stop","index":0}
```

## API 端点

| 协议 | 端点 | 用途 |
|------|------|------|
| OpenAI | `POST /v1/chat/completions` | Chat Completions API |
| OpenAI | `POST /v1/responses` | Responses API (Codex CLI) |
| OpenAI | `GET /v1/models` | 模型列表 |
| Anthropic | `POST /v1/messages` | Claude Code |
| Anthropic | `POST /v1/messages/count_tokens` | Token 计数 |
| Gemini | `POST /v1/models/{model}:generateContent` | Gemini CLI |

### 管理 API

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/accounts` | GET | 获取所有账号状态 |
| `/api/accounts/{id}` | GET | 获取账号详情 |
| `/api/accounts/{id}/usage` | GET | 获取账号用量信息 |
| `/api/accounts/{id}/refresh` | POST | 刷新账号 Token |
| `/api/accounts/{id}/restore` | POST | 恢复账号（从冷却状态） |
| `/api/accounts/refresh-all` | POST | 刷新所有即将过期的 Token |
| `/api/flows` | GET | 获取流量记录 |
| `/api/flows/stats` | GET | 获取流量统计 |
| `/api/flows/{id}` | GET | 获取流量详情 |
| `/api/quota` | GET | 获取配额状态 |
| `/api/stats` | GET | 获取统计信息 |
| `/api/health-check` | POST | 手动触发健康检查 |
| `/api/browsers` | GET | 获取可用浏览器列表 |
| `/api/docs` | GET | 获取文档列表 |
| `/api/docs/{id}` | GET | 获取文档内容 |

## 项目结构

```
.
├── run.py
├── build.py
├── pyproject.toml
├── requirements.txt
├── kiro_proxy/
│   ├── main.py                    # FastAPI 应用入口
│   ├── config.py                  # 全局配置
│   ├── converters/                # 协议转换
│   ├── core/                      # 核心模块
│   ├── credential/                # 凭证管理
│   ├── auth/                      # 认证模块
│   ├── handlers/                  # API 处理器
│   │   ├── anthropic/             # /v1/messages
│   │   ├── admin/                 # 管理 API
│   │   ├── openai.py              # /v1/chat/completions
│   │   ├── responses.py           # /v1/responses (Codex CLI)
│   │   └── gemini.py              # /v1/models/{model}:generateContent
│   ├── routers/                   # 路由层
│   ├── web/                       # Web UI
│   └── docs/                      # 内置文档
├── assets/                        # 资源文件
├── legacy/                        # 兼容旧入口
├── scripts/                       # 辅助脚本
├── examples/                      # 示例
└── tests/                         # 测试
```

## 构建

```bash
# 安装构建依赖
pip install pyinstaller

# 构建
python build.py
```

输出文件在 `dist/` 目录。

## 免责声明

本项目仅供学习研究，禁止商用。使用本项目产生的任何后果由使用者自行承担，与作者无关。

本项目与 Kiro / AWS / Anthropic 官方无关。
