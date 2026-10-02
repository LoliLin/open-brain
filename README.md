# Open Brain

OpenAI 兼容的 HTTP 服务，回复由人在网页控制台手工完成。

任何走 Chat Completions / Completions 的客户端都可以把 `base_url` 指过来。请求会挂起，直到操作员在控制台点「发送回复」。适合人工充当模型、评测、或给还不想接真实模型的客户端做联调。

## 工作流程

1. 启动服务，浏览器打开控制台。
2. 客户端按 OpenAI 协议发请求（`/v1/chat/completions` 或 `/v1/completions`）。
3. 服务把对话放进队列，HTTP 连接保持打开。
4. 控制台实时弹出任务；操作员阅读消息后输入回复（或拒绝）。
5. 客户端收到标准 OpenAI JSON；若 `stream: true`，则在人工写完后按块以 SSE 推送。

内存队列，不落盘。重启服务会丢掉未完成的任务。

## 要求

- Python 3.11+

## 安装

```bash
cd open-brain
pip install -e .
```

开发（含 pytest / httpx）：

```bash
pip install -e ".[dev]"
```

也可以只用 `requirements.txt`：

```bash
pip install -r requirements.txt
```

## 启动

```bash
python -m open_brain --port 8000
```

或安装后：

```bash
open-brain --port 8000
```

参数：

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `--host` | `OPEN_BRAIN_HOST` 或 `0.0.0.0` | 监听地址 |
| `--port` | `OPEN_BRAIN_PORT` 或 `8000` | 端口 |
| `--reload` | 关 | 代码变更自动重启（开发用） |

启动后：

- 操作员控制台：http://127.0.0.1:8000/
- OpenAI Base URL：`http://127.0.0.1:8000/v1`
- 健康检查：`GET /health`

## 操作员控制台

打开根路径即可，无需登录。

- **任务管理与实时刷新**：左侧列表自动维护状态（pending / completed / rejected），支持实时搜索过滤与新任务提示音（免音频文件的 Web Audio API 合成）。
- **消息前缀折叠与缓存**：自动识别并折叠与前序轮次完全相同的历史前缀消息，免去重复翻看已知历史的烦恼；仅高亮展示本次最新增量消息；对于完全相同的历史请求支持一键采纳缓存回复。
- **工具调用（Tool Calls）专属优化**：
  - 自动解析展示客户端声明的 `tools` 函数清单、参数 Schema 与必填标记。
  - 支持交互式工具调用构建器（一键生成参数模板、实时 JSON 格式校验、自动分配 Call ID）。
  - 对话流中结构化高亮呈现 `tool_calls` 与工具执行返回结果（`role: tool`），支持一键复制代码块。
- **常用回复模板（Snippets）**：内置并支持自定义常用话术，一键填入输入框。
- **标准回复与拒绝**：支持普通文本回复与结构化工具调用回复；`Ctrl+Enter` 快捷发送；拒绝会让客户端收到标准错误（`code: operator_rejected`）。
- 控制台通过 WebSocket `/operator/ws` 实时双向刷新。

## 客户端

默认不校验 API Key。客户端随便填一个 `api_key` 即可。若设置了 `OPEN_BRAIN_API_KEY`，请求必须带：

```
Authorization: Bearer <OPEN_BRAIN_API_KEY>
```

### Python（openai SDK）

```python
from openai import OpenAI

client = OpenAI(base_url="http://127.0.0.1:8000/v1", api_key="sk-any")

resp = client.chat.completions.create(
    model="human",
    messages=[{"role": "user", "content": "你好"}],
)
print(resp.choices[0].message.content)
```

流式：

```python
stream = client.chat.completions.create(
    model="human",
    stream=True,
    messages=[{"role": "user", "content": "慢慢说"}],
)
for chunk in stream:
    delta = chunk.choices[0].delta.content
    if delta:
        print(delta, end="", flush=True)
```

`model` 可以是 `human`，也可以是客户端硬编码的 `gpt-4o` 等。服务会原样写回响应里的 `model` 字段，方便对接只认固定模型名的软件。

### curl

非流式（会一直等到人工回复）：

```bash
curl http://127.0.0.1:8000/v1/chat/completions \
  -H "content-type: application/json" \
  -d "{\"model\":\"human\",\"messages\":[{\"role\":\"user\",\"content\":\"hi\"}]}"
```

流式：

```bash
curl -N http://127.0.0.1:8000/v1/chat/completions \
  -H "content-type: application/json" \
  -d "{\"model\":\"human\",\"stream\":true,\"messages\":[{\"role\":\"user\",\"content\":\"hi\"}]}"
```

旧版 Completions：

```bash
curl http://127.0.0.1:8000/v1/completions \
  -H "content-type: application/json" \
  -d "{\"model\":\"human\",\"prompt\":\"Say hi\"}"
```

## 兼容接口

这些路径按 OpenAI 形状返回，可被官方 SDK 和大多数第三方客户端使用。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/v1/models` | 模型列表 |
| `GET` | `/v1/models/{id}` | 单个模型（任意 id 都接受） |
| `POST` | `/v1/chat/completions` | Chat Completions，支持 `stream` |
| `POST` | `/v1/completions` | 旧版 Completions，支持 `stream` |

`GET /v1/models` 当前返回：`human`、`gpt-4o`、`gpt-4o-mini`、`gpt-4`、`gpt-3.5-turbo`、`o1`、`o3`。第一项可由 `OPEN_BRAIN_MODEL` 覆盖。客户端请求里的 `model` 不必在列表中。

Chat Completions 成功响应示例：

```json
{
  "id": "chatcmpl-...",
  "object": "chat.completion",
  "created": 1710000000,
  "model": "human",
  "choices": [
    {
      "index": 0,
      "message": {"role": "assistant", "content": "你好"},
      "finish_reason": "stop",
      "logprobs": null
    }
  ],
  "usage": {
    "prompt_tokens": 12,
    "completion_tokens": 2,
    "total_tokens": 14
  },
  "system_fingerprint": "open-brain-human"
}
```

流式为 `text/event-stream`：先发 `role: assistant` 的空 delta，再按约 12 字符一块推 `content`，最后带 `finish_reason` 并以 `data: [DONE]` 结束。

错误形状：

```json
{
  "error": {
    "message": "...",
    "type": "invalid_request_error",
    "param": null,
    "code": "operator_rejected"
  }
}
```

常见失败：

| 情况 | HTTP | `error.code` |
| --- | --- | --- |
| API Key 不对 | 401 | `invalid_api_key` |
| 操作员拒绝 | 400 | `operator_rejected` |
| 等待超时 | 504 | `timeout` |
| 客户端断开 | 499 | `cancelled` |

`usage` 里的 token 数按字符粗估（约 4 字符 = 1 token），不是真实 tokenizer。

请求里多出来的字段（`temperature`、`tools`、`response_format` 等）会收下并在控制台直观呈现。控制台原生支持查看客户端 `tools` 规范并交互式发起 `tool_calls`。

## 操作员 HTTP API

控制台底层就是这些接口，也可以自己写脚本当操作员。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/operator/jobs` | 最近任务（pending 优先展示由前端排序） |
| `GET` | `/operator/jobs/{id}` | 单个任务详情（含 prefix_match / exact_match） |
| `POST` | `/operator/jobs/{id}/reply` | 提交回复（支持纯文本或 tool_calls） |
| `POST` | `/operator/jobs/{id}/reject` | 拒绝 |
| `GET` | `/operator/snippets` | 获取常用回复模板列表 |
| `POST` | `/operator/snippets` | 新增常用回复模板 |
| `DELETE` | `/operator/snippets/{id}` | 删除常用回复模板 |
| `GET` | `/operator/cache` | 获取历史前缀与精确缓存统计 |
| `POST` | `/operator/cache/clear` | 清空响应缓存 |
| `WS` | `/operator/ws` | 任务创建/完成/拒绝/取消事件 |

回复文本示例：

```json
{"content": "助手要说的话", "finish_reason": "stop"}
```

回复工具调用示例：

```json
{
  "content": null,
  "finish_reason": "tool_calls",
  "tool_calls": [
    {
      "id": "call_12345",
      "type": "function",
      "function": {
        "name": "get_current_weather",
        "arguments": "{\"location\": \"Hangzhou\"}"
      }
    }
  ]
}
```

`finish_reason`：`stop` | `length` | `content_filter` | `tool_calls`。

拒绝：

```json
{"message": "operator rejected the request", "code": "operator_rejected"}
```

## 环境变量

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `OPEN_BRAIN_HOST` | `0.0.0.0` | 监听地址 |
| `OPEN_BRAIN_PORT` | `8000` | 端口 |
| `OPEN_BRAIN_MODEL` | `human` | `/v1/models` 列表第一项 |
| `OPEN_BRAIN_API_KEY` | 空 | 非空则校验 Bearer；空则不校验 |
| `OPEN_BRAIN_WAIT_TIMEOUT` | `21600` | 等人回复的秒数（默认 6 小时） |

Windows PowerShell 示例：

```powershell
$env:OPEN_BRAIN_API_KEY = "sk-secret"
$env:OPEN_BRAIN_PORT = "8000"
python -m open_brain
```

## 测试

```bash
pip install -e ".[dev]"
python -m pytest tests/test_api.py -q
```

覆盖：模型列表、非流式 Chat Completions、SSE 流式、操作员 HTTP 回复、拒绝路径。

## 限制

- 单进程内存队列，不做多实例共享。
- 人工回复完成之后才开始流式推送，不是边打字边推。
- 不实现 embeddings、images、audio、responses API、真实 function calling 执行。
- `n > 1` 仍只返回一条 choice。
- 任务 TTL 与等待超时相同；过期后客户端收到 504。
- 控制台没有鉴权。若服务暴露到公网，应自己在前面加反向代理认证，并设置 `OPEN_BRAIN_API_KEY`。
