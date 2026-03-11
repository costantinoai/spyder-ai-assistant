# Ollama API Reference for Spyder IDE Plugin

> Compiled: 2026-03-10
> Ollama Python SDK: v0.6.1 | OpenAI Python SDK compatible
> Sources: Official Ollama docs, GitHub repos, DeepWiki

---

## Table of Contents

1. [Overview](#1-overview)
2. [Native REST API](#2-native-rest-api)
   - [POST /api/chat](#21-post-apichat--chat-completions)
   - [POST /api/generate](#22-post-apigenerate--text-generation)
   - [GET /api/tags](#23-get-apitags--list-models)
   - [POST /api/show](#24-post-apishow--model-info)
   - [GET /api/ps](#25-get-apips--running-models)
3. [OpenAI-Compatible API](#3-openai-compatible-api)
   - [POST /v1/chat/completions](#31-post-v1chatcompletions)
   - [POST /v1/completions](#32-post-v1completions)
   - [GET /v1/models](#33-get-v1models)
4. [Streaming Protocols](#4-streaming-protocols)
   - [Native NDJSON Streaming](#41-native-ndjson-streaming)
   - [OpenAI SSE Streaming](#42-openai-sse-streaming)
5. [Python Client Libraries](#5-python-client-libraries)
   - [Official ollama Package](#51-official-ollama-python-package)
   - [OpenAI SDK with Ollama](#52-openai-python-sdk-with-ollama)
6. [Model Parameters Reference](#6-model-parameters-reference)
7. [Plugin Integration Patterns](#7-plugin-integration-patterns)

---

## 1. Overview

**Base URL:** `http://localhost:11434` (configurable via `OLLAMA_HOST` env var)

Ollama exposes two API families:
- **Native API** at `/api/*` -- NDJSON streaming, full feature set
- **OpenAI-compatible API** at `/v1/*` -- SSE streaming, drop-in replacement for OpenAI SDK

Content types:
- Requests: `application/json`
- Streaming responses (native): `application/x-ndjson` (newline-delimited JSON)
- Streaming responses (OpenAI): `text/event-stream` (Server-Sent Events)
- Non-streaming responses: `application/json`

Authentication: None required for local. The `/v1/*` endpoints accept an `api_key` parameter but ignore it.

---

## 2. Native REST API

### 2.1. POST /api/chat -- Chat Completions

The primary endpoint for multi-turn conversations. Streaming is **enabled by default**.

#### Request

```json
{
  "model": "qwen2.5-coder:7b",
  "messages": [
    {
      "role": "system",
      "content": "You are a helpful coding assistant for a Python IDE."
    },
    {
      "role": "user",
      "content": "Write a function to compute fibonacci numbers"
    }
  ],
  "stream": true,
  "format": "json",
  "options": {
    "temperature": 0.3,
    "top_p": 0.9,
    "num_predict": 512,
    "num_ctx": 4096,
    "stop": ["\n```\n"]
  },
  "keep_alive": "5m"
}
```

**Parameters:**

| Parameter    | Type              | Required | Default | Description |
|-------------|-------------------|----------|---------|-------------|
| `model`     | string            | Yes      | --      | Model name (e.g., `"qwen2.5-coder:7b"`) |
| `messages`  | array of Message  | Yes      | --      | Conversation history |
| `stream`    | bool              | No       | `true`  | Enable streaming |
| `format`    | string or object  | No       | --      | `"json"` or a JSON Schema object |
| `options`   | object            | No       | --      | Model parameters (see Section 6) |
| `keep_alive`| string or number  | No       | `"5m"`  | How long to keep model in memory |
| `tools`     | array of Tool     | No       | --      | Function definitions for tool calling |

**Message object:**

| Field     | Type   | Description |
|-----------|--------|-------------|
| `role`    | string | `"system"`, `"user"`, `"assistant"`, or `"tool"` |
| `content` | string | Message text |
| `images`  | array  | Base64-encoded images (for multimodal models) |

#### Response (non-streaming, `"stream": false`)

```json
{
  "model": "qwen2.5-coder:7b",
  "created_at": "2025-02-08T11:22:15.229839Z",
  "message": {
    "role": "assistant",
    "content": "def fibonacci(n):\n    if n <= 1:\n        return n\n    return fibonacci(n-1) + fibonacci(n-2)"
  },
  "done": true,
  "done_reason": "stop",
  "total_duration": 407998750,
  "load_duration": 6202542,
  "prompt_eval_count": 47,
  "prompt_eval_duration": 36000000,
  "eval_count": 65,
  "eval_duration": 363000000
}
```

#### Response (streaming, default)

Each line is a separate JSON object (NDJSON):

```
{"model":"qwen2.5-coder:7b","created_at":"2025-02-08T11:22:15.100Z","message":{"role":"assistant","content":"def"},"done":false}
{"model":"qwen2.5-coder:7b","created_at":"2025-02-08T11:22:15.150Z","message":{"role":"assistant","content":" fibonacci"},"done":false}
{"model":"qwen2.5-coder:7b","created_at":"2025-02-08T11:22:15.200Z","message":{"role":"assistant","content":"(n)"},"done":false}
...
{"model":"qwen2.5-coder:7b","created_at":"2025-02-08T11:22:15.900Z","message":{"role":"assistant","content":""},"done":true,"done_reason":"stop","total_duration":1200000000,"load_duration":400000000,"prompt_eval_count":12,"prompt_eval_duration":100000000,"eval_count":65,"eval_duration":700000000}
```

**Response fields:**

| Field                  | Type   | Description |
|-----------------------|--------|-------------|
| `model`               | string | Model used |
| `created_at`          | string | ISO 8601 timestamp |
| `message`             | object | `{role, content}` -- the generated chunk |
| `done`                | bool   | `true` on final chunk |
| `done_reason`         | string | `"stop"`, `"length"`, or `"tool_calls"` (final chunk only) |
| `total_duration`      | int    | Total time in nanoseconds (final chunk only) |
| `load_duration`       | int    | Model load time in nanoseconds (final chunk only) |
| `prompt_eval_count`   | int    | Prompt token count (final chunk only) |
| `prompt_eval_duration`| int    | Prompt processing time in ns (final chunk only) |
| `eval_count`          | int    | Generated token count (final chunk only) |
| `eval_duration`       | int    | Generation time in nanoseconds (final chunk only) |

**Duration conversion:** Divide by `1_000_000_000` to get seconds. Tokens/sec = `eval_count / (eval_duration / 1e9)`.

---

### 2.2. POST /api/generate -- Text Generation

Simpler endpoint for single-prompt completions. Useful for code completion (fill-in-the-middle with `suffix`).

#### Request

```json
{
  "model": "qwen2.5-coder:7b",
  "prompt": "def quicksort(arr):\n    if len(arr) <= 1:\n        return arr\n    pivot = arr[0]\n    ",
  "suffix": "\n    return quicksort(less) + [pivot] + quicksort(greater)",
  "system": "Complete the Python code. Only output code, no explanations.",
  "stream": true,
  "options": {
    "temperature": 0.2,
    "num_predict": 256,
    "num_ctx": 4096,
    "stop": ["\ndef ", "\nclass "]
  }
}
```

**Parameters:**

| Parameter  | Type             | Required | Default | Description |
|-----------|------------------|----------|---------|-------------|
| `model`   | string           | Yes      | --      | Model name |
| `prompt`  | string           | Yes      | --      | Input text / code prefix |
| `suffix`  | string           | No       | --      | Text after cursor (fill-in-the-middle) |
| `system`  | string           | No       | --      | System prompt |
| `template`| string           | No       | --      | Override prompt template |
| `context` | array of int     | No       | --      | Context from previous `/api/generate` response |
| `stream`  | bool             | No       | `true`  | Enable streaming |
| `raw`     | bool             | No       | `false` | Skip prompt templating |
| `format`  | string or object | No       | --      | `"json"` or JSON Schema |
| `images`  | array of string  | No       | --      | Base64-encoded images |
| `options` | object           | No       | --      | Model parameters (see Section 6) |
| `keep_alive`| string or number| No      | `"5m"` | Memory retention duration |

#### Response (non-streaming)

```json
{
  "model": "qwen2.5-coder:7b",
  "created_at": "2025-02-08T11:02:55.115275Z",
  "response": "less = [x for x in arr[1:] if x <= pivot]\n    greater = [x for x in arr[1:] if x > pivot]",
  "done": true,
  "done_reason": "stop",
  "context": [1, 2, 3, 4],
  "total_duration": 302810709,
  "load_duration": 13315375,
  "prompt_eval_count": 47,
  "prompt_eval_duration": 132000000,
  "eval_count": 30,
  "eval_duration": 156000000
}
```

#### Response (streaming)

```
{"model":"qwen2.5-coder:7b","created_at":"...","response":"less","done":false}
{"model":"qwen2.5-coder:7b","created_at":"...","response":" =","done":false}
{"model":"qwen2.5-coder:7b","created_at":"...","response":" [","done":false}
...
{"model":"qwen2.5-coder:7b","created_at":"...","response":"","done":true,"done_reason":"stop","total_duration":302810709,"load_duration":13315375,"prompt_eval_count":47,"prompt_eval_duration":132000000,"eval_count":30,"eval_duration":156000000}
```

> **Note:** The `context` field (array of token IDs) can be passed back in subsequent requests to maintain context without resending the full conversation. However, for the Spyder plugin, using `/api/chat` with explicit `messages` history is recommended for clarity.

---

### 2.3. GET /api/tags -- List Models

Returns all locally available models.

#### Request

```bash
curl http://localhost:11434/api/tags
```

No request body needed.

#### Response

```json
{
  "models": [
    {
      "name": "qwen2.5-coder:7b",
      "model": "qwen2.5-coder:7b",
      "modified_at": "2025-02-08T15:33:44.760304367Z",
      "size": 4700000000,
      "digest": "sha256:abc123def456...",
      "details": {
        "parent_model": "",
        "format": "gguf",
        "family": "qwen2",
        "families": ["qwen2"],
        "parameter_size": "7B",
        "quantization_level": "Q4_0"
      }
    },
    {
      "name": "llama3.2:latest",
      "model": "llama3.2:latest",
      "modified_at": "2025-01-15T10:00:00Z",
      "size": 2000000000,
      "digest": "sha256:789abc...",
      "details": {
        "parent_model": "",
        "format": "gguf",
        "family": "llama",
        "families": ["llama"],
        "parameter_size": "3B",
        "quantization_level": "Q4_K_M"
      }
    }
  ]
}
```

**Model details fields:**

| Field               | Type   | Description |
|--------------------|--------|-------------|
| `name`             | string | Full model name with tag |
| `model`            | string | Model identifier |
| `modified_at`      | string | ISO 8601 last modification time |
| `size`             | int    | Model file size in bytes |
| `digest`           | string | SHA256 digest of model |
| `details.format`   | string | Model format (e.g., `"gguf"`) |
| `details.family`   | string | Model family (e.g., `"llama"`, `"qwen2"`) |
| `details.parameter_size` | string | Parameter count (e.g., `"7B"`) |
| `details.quantization_level` | string | Quantization (e.g., `"Q4_0"`, `"Q4_K_M"`, `"F16"`) |

---

### 2.4. POST /api/show -- Model Info

Returns detailed information about a specific model.

#### Request

```json
{
  "model": "qwen2.5-coder:7b"
}
```

#### Response

```json
{
  "name": "qwen2.5-coder:7b",
  "model": "qwen2.5-coder:7b",
  "modified_at": "2025-02-08T15:33:44Z",
  "size": 4700000000,
  "digest": "sha256:abc123...",
  "details": {
    "parent_model": "",
    "format": "gguf",
    "family": "qwen2",
    "families": ["qwen2"],
    "parameter_size": "7B",
    "quantization_level": "Q4_0"
  },
  "model_info": {
    "general.name": "qwen2.5-coder",
    "general.architecture": "qwen2",
    "qwen2.context_length": 32768
  },
  "template": "{{ .System }}\n{{ .Prompt }}",
  "system": "You are a helpful assistant.",
  "license": "Apache License 2.0...",
  "parameters": "stop \"<|im_start|>\"\nstop \"<|im_end|>\"\nnum_ctx 32768"
}
```

**Key fields for the plugin:**
- `model_info` -- contains context length (`*.context_length`), architecture info
- `details.parameter_size` -- useful for displaying to user
- `details.quantization_level` -- useful for displaying to user
- `parameters` -- default model parameters as newline-delimited key-value pairs

---

### 2.5. GET /api/ps -- Running Models

Lists models currently loaded in memory.

#### Request

```bash
curl http://localhost:11434/api/ps
```

#### Response

```json
{
  "models": [
    {
      "name": "qwen2.5-coder:7b",
      "model": "qwen2.5-coder:7b",
      "size": 4700000000,
      "digest": "sha256:abc123...",
      "details": {
        "parent_model": "",
        "format": "gguf",
        "family": "qwen2",
        "families": ["qwen2"],
        "parameter_size": "7B",
        "quantization_level": "Q4_0"
      },
      "expires_at": "2025-02-08T16:00:00Z",
      "size_vram": 4700000000,
      "processor": "GPU"
    }
  ]
}
```

**Additional fields vs /api/tags:**

| Field        | Type   | Description |
|-------------|--------|-------------|
| `expires_at`| string | When model will be unloaded (based on `keep_alive`) |
| `size_vram` | int    | VRAM usage in bytes |
| `processor` | string | `"GPU"`, `"CPU"`, or `"GPU/CPU"` (partial offload) |

---

## 3. OpenAI-Compatible API

Ollama implements a subset of the OpenAI API at `/v1/*`. This allows using the `openai` Python SDK as-is.

**Base URL:** `http://localhost:11434/v1/`
**API Key:** Required by SDK but ignored -- use any string (e.g., `"ollama"`)

### 3.1. POST /v1/chat/completions

#### Request

```json
{
  "model": "qwen2.5-coder:7b",
  "messages": [
    {"role": "system", "content": "You are a helpful coding assistant."},
    {"role": "user", "content": "Explain Python decorators"}
  ],
  "temperature": 0.7,
  "max_tokens": 512,
  "stream": true,
  "stop": ["\n---"],
  "top_p": 0.9,
  "frequency_penalty": 0.0,
  "presence_penalty": 0.0,
  "seed": 42
}
```

**Supported parameters:**

| OpenAI Parameter     | Ollama Mapping              | Supported |
|---------------------|-----------------------------|-----------|
| `model`             | `model`                     | Yes |
| `messages`          | `messages`                  | Yes |
| `temperature`       | `options.temperature`       | Yes |
| `top_p`             | `options.top_p`             | Yes |
| `max_tokens`        | `options.num_predict`       | Yes |
| `stream`            | `stream`                    | Yes |
| `stop`              | `options.stop`              | Yes |
| `seed`              | `options.seed`              | Yes |
| `frequency_penalty` | `options.frequency_penalty` | Yes |
| `presence_penalty`  | `options.presence_penalty`  | Yes |
| `response_format`   | `format`                    | Yes (JSON mode) |
| `tools`             | `tools`                     | Yes |
| `n`                 | --                          | No |
| `logprobs`          | --                          | No |
| `logit_bias`        | --                          | No |
| `user`              | --                          | No |

#### Response (non-streaming)

```json
{
  "id": "chatcmpl-abc123",
  "object": "chat.completion",
  "created": 1707400000,
  "model": "qwen2.5-coder:7b",
  "system_fingerprint": "fp_ollama",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "Python decorators are functions that modify the behavior of other functions..."
      },
      "finish_reason": "stop"
    }
  ],
  "usage": {
    "prompt_tokens": 25,
    "completion_tokens": 150,
    "total_tokens": 175
  }
}
```

#### Response (streaming SSE)

Each chunk is prefixed with `data: ` and the stream ends with `data: [DONE]`:

```
data: {"id":"chatcmpl-abc123","object":"chat.completion.chunk","created":1707400000,"model":"qwen2.5-coder:7b","system_fingerprint":"fp_ollama","choices":[{"index":0,"delta":{"role":"assistant","content":"Python"},"finish_reason":null}]}

data: {"id":"chatcmpl-abc123","object":"chat.completion.chunk","created":1707400000,"model":"qwen2.5-coder:7b","system_fingerprint":"fp_ollama","choices":[{"index":0,"delta":{"content":" decorators"},"finish_reason":null}]}

data: {"id":"chatcmpl-abc123","object":"chat.completion.chunk","created":1707400000,"model":"qwen2.5-coder:7b","system_fingerprint":"fp_ollama","choices":[{"index":0,"delta":{"content":""},"finish_reason":"stop"}]}

data: [DONE]
```

**Key differences from native API:**
- `finish_reason` values: `"stop"`, `"length"`, `"tool_calls"` (same as OpenAI)
- `system_fingerprint` is always `"fp_ollama"`
- `usage` field only present in the final non-streaming response (not in chunks)
- Streaming uses `delta` (incremental) instead of `message` (full)

---

### 3.2. POST /v1/completions

For simple text/code completions (non-chat). Supports `suffix` for fill-in-the-middle.

#### Request

```json
{
  "model": "qwen2.5-coder:7b",
  "prompt": "def fibonacci(n):\n    ",
  "suffix": "\n    return result",
  "max_tokens": 200,
  "temperature": 0.2,
  "stream": true
}
```

#### Response (non-streaming)

```json
{
  "id": "cmpl-abc123",
  "object": "text_completion",
  "created": 1707400000,
  "model": "qwen2.5-coder:7b",
  "system_fingerprint": "fp_ollama",
  "choices": [
    {
      "text": "if n <= 1:\n        return n\n    a, b = 0, 1\n    for _ in range(2, n+1):\n        a, b = b, a + b\n    result = b",
      "index": 0,
      "finish_reason": "stop"
    }
  ],
  "usage": {
    "prompt_tokens": 10,
    "completion_tokens": 45,
    "total_tokens": 55
  }
}
```

#### Response (streaming SSE)

```
data: {"id":"cmpl-abc123","object":"text_completion.chunk","created":1707400000,"model":"qwen2.5-coder:7b","system_fingerprint":"fp_ollama","choices":[{"text":"if","index":0,"finish_reason":null}]}

data: {"id":"cmpl-abc123","object":"text_completion.chunk","created":1707400000,"model":"qwen2.5-coder:7b","system_fingerprint":"fp_ollama","choices":[{"text":" n","index":0,"finish_reason":null}]}

...

data: [DONE]
```

---

### 3.3. GET /v1/models

Lists available models in OpenAI format.

#### Response

```json
{
  "object": "list",
  "data": [
    {
      "id": "qwen2.5-coder:7b",
      "object": "model",
      "created": 1707400000,
      "owned_by": "library"
    },
    {
      "id": "llama3.2:latest",
      "object": "model",
      "created": 1705300000,
      "owned_by": "library"
    }
  ]
}
```

---

## 4. Streaming Protocols

### 4.1. Native NDJSON Streaming

The native Ollama API uses **Newline-Delimited JSON (NDJSON)**. Each line is a complete JSON object terminated by `\n`.

**Parsing algorithm:**

```python
import httpx
import json

def stream_chat_native(model, messages, options=None):
    """Stream chat using native Ollama API (NDJSON)."""
    url = "http://localhost:11434/api/chat"
    payload = {
        "model": model,
        "messages": messages,
        "stream": True,
    }
    if options:
        payload["options"] = options

    with httpx.stream("POST", url, json=payload, timeout=120.0) as response:
        for line in response.iter_lines():
            if line:
                chunk = json.loads(line)
                if not chunk.get("done", False):
                    yield chunk["message"]["content"]
                else:
                    # Final chunk contains metrics
                    yield {
                        "done": True,
                        "eval_count": chunk.get("eval_count", 0),
                        "eval_duration": chunk.get("eval_duration", 0),
                    }
```

**Key characteristics:**
- Content-Type: `application/x-ndjson`
- Each line is a valid JSON object
- No prefix -- raw JSON per line
- Final object has `"done": true` with performance metrics
- No separate termination signal (the `done` flag is the signal)

### 4.2. OpenAI SSE Streaming

The `/v1/*` endpoints use **Server-Sent Events (SSE)** format.

**Parsing algorithm:**

```python
import httpx
import json

def stream_chat_openai(model, messages, options=None):
    """Stream chat using OpenAI-compatible API (SSE)."""
    url = "http://localhost:11434/v1/chat/completions"
    payload = {
        "model": model,
        "messages": messages,
        "stream": True,
    }
    if options:
        payload.update(options)  # temperature, max_tokens, etc. go at top level

    with httpx.stream("POST", url, json=payload, timeout=120.0) as response:
        for line in response.iter_lines():
            if not line or line.startswith(":"):
                continue  # Skip empty lines and SSE comments
            if line == "data: [DONE]":
                break
            if line.startswith("data: "):
                data = json.loads(line[6:])  # Strip "data: " prefix
                delta = data["choices"][0].get("delta", {})
                content = delta.get("content", "")
                if content:
                    yield content
```

**Key characteristics:**
- Content-Type: `text/event-stream`
- Each event line starts with `data: ` prefix
- Blank lines separate events
- Stream terminates with `data: [DONE]`
- Chunk uses `delta` (not `message`) with incremental content
- First chunk includes `delta.role`, subsequent chunks include only `delta.content`

### 4.3. Comparison Table

| Feature                | Native (`/api/*`)       | OpenAI (`/v1/*`)          |
|-----------------------|-------------------------|---------------------------|
| Format                | NDJSON                  | SSE                       |
| Content-Type          | `application/x-ndjson`  | `text/event-stream`       |
| Line prefix           | None                    | `data: `                  |
| Termination           | `"done": true` in JSON  | `data: [DONE]`            |
| Content field          | `message.content`       | `choices[0].delta.content`|
| Metrics in stream     | Yes (final chunk)       | No (non-streaming only)   |
| Token usage           | `eval_count` (final)    | `usage` (non-stream only) |
| Object type           | N/A                     | `chat.completion.chunk`   |

---

## 5. Python Client Libraries

### 5.1. Official `ollama` Python Package

**Install:** `pip install ollama` (v0.6.1, MIT license, Python >=3.8)
**Repo:** https://github.com/ollama/ollama-python
**Dependencies:** `httpx`

#### Synchronous Client

```python
from ollama import Client

client = Client(host="http://localhost:11434")

# --- Chat (non-streaming) ---
response = client.chat(
    model="qwen2.5-coder:7b",
    messages=[
        {"role": "system", "content": "You are a coding assistant."},
        {"role": "user", "content": "Write a Python fibonacci function"},
    ],
    options={"temperature": 0.3, "num_predict": 512},
)
print(response.message.content)
# Also accessible as: response["message"]["content"]

# --- Chat (streaming) ---
stream = client.chat(
    model="qwen2.5-coder:7b",
    messages=[{"role": "user", "content": "Explain list comprehensions"}],
    stream=True,
)
for chunk in stream:
    print(chunk.message.content, end="", flush=True)

# --- Generate (code completion) ---
response = client.generate(
    model="qwen2.5-coder:7b",
    prompt="def quicksort(arr):\n    ",
    suffix="\n    return sorted_arr",
    system="Complete the code. Output only code.",
    options={"temperature": 0.2, "num_predict": 256},
)
print(response.response)

# --- List models ---
models = client.list()
for m in models.models:
    print(f"{m.name} ({m.details.parameter_size}, {m.details.quantization_level})")

# --- Show model info ---
info = client.show("qwen2.5-coder:7b")
print(f"Family: {info.details.family}")
print(f"Context: {info.model_info.get('qwen2.context_length', 'unknown')}")

# --- Running models ---
ps = client.ps()
for m in ps.models:
    print(f"{m.name} - VRAM: {m.size_vram / 1e9:.1f} GB - {m.processor}")
```

#### Async Client

```python
import asyncio
from ollama import AsyncClient

async def chat_stream():
    client = AsyncClient(host="http://localhost:11434")

    # Streaming chat
    async for chunk in await client.chat(
        model="qwen2.5-coder:7b",
        messages=[{"role": "user", "content": "What is a closure?"}],
        stream=True,
    ):
        print(chunk.message.content, end="", flush=True)

    # Non-streaming
    response = await client.chat(
        model="qwen2.5-coder:7b",
        messages=[{"role": "user", "content": "Hello"}],
    )
    print(response.message.content)

    # List models
    models = await client.list()
    for m in models.models:
        print(m.name)

asyncio.run(chat_stream())
```

#### Method Signatures (Key Methods)

```python
# Chat -- conversations with message history
client.chat(
    model: str,
    messages: list[dict],           # [{"role": "user", "content": "..."}]
    stream: bool = False,
    tools: list | None = None,      # Function calling
    format: str | dict | None = None,  # "json" or JSON schema
    options: dict | None = None,    # Model params
    keep_alive: str | float | None = None,
    think: bool | str | None = None,   # Reasoning models
) -> ChatResponse | Iterator[ChatResponse]

# Generate -- single-prompt completions
client.generate(
    model: str,
    prompt: str | None = None,
    suffix: str | None = None,      # Fill-in-the-middle
    system: str | None = None,
    stream: bool = False,
    raw: bool | None = None,        # Skip templating
    format: str | dict | None = None,
    images: list | None = None,
    options: dict | None = None,
    keep_alive: str | float | None = None,
) -> GenerateResponse | Iterator[GenerateResponse]

# List available models
client.list() -> ListResponse

# Show model details
client.show(model: str) -> ShowResponse

# Running models
client.ps() -> ProcessResponse

# Embeddings
client.embed(
    model: str,
    input: str | list[str],
    truncate: bool | None = None,
    options: dict | None = None,
    keep_alive: str | float | None = None,
    dimensions: int | None = None,
) -> EmbedResponse
```

#### Error Handling

```python
import ollama

try:
    response = ollama.chat(model="nonexistent-model")
except ollama.ResponseError as e:
    print(f"Error: {e.error}")
    print(f"Status: {e.status_code}")
    if e.status_code == 404:
        print("Model not found -- need to pull it first")
```

---

### 5.2. OpenAI Python SDK with Ollama

**Install:** `pip install openai`

Use the standard OpenAI SDK by pointing `base_url` to Ollama.

#### Setup

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:11434/v1/",
    api_key="ollama",  # Required by SDK, ignored by Ollama
)
```

#### Chat Completions (non-streaming)

```python
response = client.chat.completions.create(
    model="qwen2.5-coder:7b",
    messages=[
        {"role": "system", "content": "You are a coding assistant for a Python IDE."},
        {"role": "user", "content": "How do I read a CSV file with pandas?"},
    ],
    temperature=0.7,
    max_tokens=512,
)
print(response.choices[0].message.content)
print(f"Tokens: {response.usage.prompt_tokens} + {response.usage.completion_tokens}")
```

#### Chat Completions (streaming)

```python
stream = client.chat.completions.create(
    model="qwen2.5-coder:7b",
    messages=[
        {"role": "system", "content": "You are a coding assistant."},
        {"role": "user", "content": "Write a binary search function"},
    ],
    temperature=0.3,
    max_tokens=512,
    stream=True,
)

full_response = []
for chunk in stream:
    delta = chunk.choices[0].delta
    if delta.content:
        print(delta.content, end="", flush=True)
        full_response.append(delta.content)

final_text = "".join(full_response)
```

#### Text Completions (code completion)

```python
response = client.completions.create(
    model="qwen2.5-coder:7b",
    prompt="import numpy as np\n\ndef normalize(arr):\n    ",
    suffix="\n    return normalized",
    max_tokens=200,
    temperature=0.2,
)
print(response.choices[0].text)
```

#### List Models

```python
models = client.models.list()
for model in models.data:
    print(f"{model.id} (owned by: {model.owned_by})")
```

#### Async Usage

```python
from openai import AsyncOpenAI
import asyncio

async_client = AsyncOpenAI(
    base_url="http://localhost:11434/v1/",
    api_key="ollama",
)

async def stream_response():
    stream = await async_client.chat.completions.create(
        model="qwen2.5-coder:7b",
        messages=[{"role": "user", "content": "Explain async/await"}],
        stream=True,
    )
    async for chunk in stream:
        if chunk.choices[0].delta.content:
            print(chunk.choices[0].delta.content, end="", flush=True)

asyncio.run(stream_response())
```

---

## 6. Model Parameters Reference

These go in the `options` field (native API) or as top-level parameters (OpenAI API).

### Sampling Parameters

| Parameter            | Type  | Default    | Range        | Description |
|---------------------|-------|------------|--------------|-------------|
| `temperature`       | float | 0.8        | 0.0 - 2.0   | Randomness. Lower = more deterministic. Use 0.1-0.3 for code. |
| `top_k`             | int   | 40         | 0+           | Top-K sampling. 0 = disabled. |
| `top_p`             | float | 0.9        | 0.0 - 1.0   | Nucleus sampling. |
| `min_p`             | float | 0.0        | 0.0 - 1.0   | Minimum probability threshold. |
| `seed`              | int   | --         | any          | Fixed seed for reproducible output. |

### Generation Control

| Parameter            | Type    | Default | Description |
|---------------------|---------|---------|-------------|
| `num_predict`       | int     | -1      | Max tokens to generate. -1 = infinite, -2 = fill context. |
| `stop`              | array   | --      | Stop sequences (up to 4 strings). |
| `repeat_penalty`    | float   | 1.1     | Penalize repeated tokens. |
| `frequency_penalty` | float   | 0.0     | Penalize based on frequency in output. |
| `presence_penalty`  | float   | 0.0     | Penalize based on presence in output. |

### Context & Performance

| Parameter    | Type | Default | Description |
|-------------|------|---------|-------------|
| `num_ctx`   | int  | 2048    | Context window size (tokens). Many models support 4096-32768+. |
| `num_batch` | int  | 512     | Batch size for prompt processing. |
| `num_thread`| int  | auto    | CPU threads for inference. |
| `num_gpu`   | int  | -1      | GPU layers. -1 = all layers on GPU. 0 = CPU only. |
| `main_gpu`  | int  | 0       | Primary GPU index for multi-GPU. |

### Recommended Settings for Plugin Use Cases

| Use Case               | temperature | top_p | num_predict | stop sequences |
|------------------------|-------------|-------|-------------|----------------|
| Code completion        | 0.1 - 0.2  | 0.9   | 256         | `["\ndef ", "\nclass ", "\n\n\n"]` |
| Code chat / explain    | 0.5 - 0.7  | 0.9   | 1024        | --             |
| Docstring generation   | 0.3        | 0.9   | 256         | `['"""', "'''"]` |
| Bug fix suggestions    | 0.3        | 0.9   | 512         | --             |

---

## 7. Plugin Integration Patterns

### 7.1. Which API to Use?

**Recommendation: Use the official `ollama` Python package as primary, with OpenAI SDK as optional backend.**

| Feature                | `ollama` package       | `openai` SDK           |
|-----------------------|------------------------|------------------------|
| Native features       | Full support           | Subset                 |
| `suffix` (FIM)        | `generate()` native    | `/v1/completions`      |
| Streaming             | NDJSON iterator        | SSE iterator           |
| Token metrics         | In stream (final chunk)| Non-streaming only     |
| Model management      | `pull()`, `show()`, `ps()` | `models.list()` only |
| Async support         | `AsyncClient`          | `AsyncOpenAI`          |
| Error types           | `ollama.ResponseError` | `openai.APIError`      |
| Extra dependency      | `httpx`                | `httpx`, `pydantic`    |
| Swap to cloud LLM     | No (Ollama only)       | Yes (change base_url)  |

### 7.2. Checking Ollama Availability

```python
import httpx

def check_ollama(host="http://localhost:11434", timeout=2.0):
    """Check if Ollama is running. Returns True/False."""
    try:
        r = httpx.get(f"{host}/api/tags", timeout=timeout)
        return r.status_code == 200
    except (httpx.ConnectError, httpx.TimeoutException):
        return False
```

### 7.3. Conversation History Pattern

```python
class OllamaChat:
    """Manages chat history for Ollama conversations."""

    def __init__(self, model, system_prompt=None, host="http://localhost:11434"):
        from ollama import Client
        self.client = Client(host=host)
        self.model = model
        self.messages = []
        if system_prompt:
            self.messages.append({"role": "system", "content": system_prompt})

    def send(self, user_message, stream=True, options=None):
        self.messages.append({"role": "user", "content": user_message})
        response = self.client.chat(
            model=self.model,
            messages=self.messages,
            stream=stream,
            options=options or {"temperature": 0.5},
        )
        if stream:
            return self._handle_stream(response)
        else:
            assistant_msg = response.message.content
            self.messages.append({"role": "assistant", "content": assistant_msg})
            return assistant_msg

    def _handle_stream(self, stream):
        chunks = []
        for chunk in stream:
            content = chunk.message.content
            chunks.append(content)
            yield content
        # After streaming completes, save full response to history
        full_response = "".join(chunks)
        self.messages.append({"role": "assistant", "content": full_response})

    def clear(self):
        """Reset conversation, keeping system prompt."""
        system = [m for m in self.messages if m["role"] == "system"]
        self.messages = system
```

### 7.4. Code Completion Pattern

```python
def complete_code(client, model, prefix, suffix="", context_code=""):
    """
    Fill-in-the-middle code completion using /api/generate.

    Args:
        client: ollama.Client instance
        model: Model name (e.g., "qwen2.5-coder:7b")
        prefix: Code before the cursor
        suffix: Code after the cursor
        context_code: Additional file context to include in system prompt
    """
    system = "You are a code completion engine. Output ONLY the code that fills the gap. No explanations, no markdown."
    if context_code:
        system += f"\n\nFile context:\n```\n{context_code}\n```"

    response = client.generate(
        model=model,
        prompt=prefix,
        suffix=suffix if suffix else None,
        system=system,
        options={
            "temperature": 0.15,
            "num_predict": 256,
            "stop": ["\ndef ", "\nclass ", "\n\n\n"],
        },
    )
    return response.response
```

### 7.5. Model Selection Helper

```python
def get_available_models(client):
    """
    Returns list of models with metadata, sorted by relevance for coding.

    Returns list of dicts with: name, family, size, quantization, is_loaded
    """
    models_list = client.list()
    running = {m.name for m in client.ps().models}

    result = []
    for m in models_list.models:
        result.append({
            "name": m.name,
            "family": m.details.family,
            "parameter_size": m.details.parameter_size,
            "quantization": m.details.quantization_level,
            "size_gb": round(m.size / 1e9, 1),
            "is_loaded": m.name in running,
        })

    # Sort: loaded models first, then by name
    result.sort(key=lambda x: (not x["is_loaded"], x["name"]))
    return result
```

---

## Appendix: Error Codes

| HTTP Status | Meaning | Common Cause |
|-------------|---------|--------------|
| 200         | Success | -- |
| 400         | Bad request | Invalid JSON, missing required field |
| 404         | Not found | Model not pulled / does not exist |
| 500         | Server error | Model loading failure, OOM |
| Connection refused | Ollama not running | Service not started |

Error response body (native API):
```json
{"error": "model 'nonexistent' not found, try pulling it first"}
```

Error response body (OpenAI API):
```json
{
  "error": {
    "message": "model 'nonexistent' not found",
    "type": "not_found_error",
    "code": null
  }
}
```

---

## Sources

- Ollama official API docs: https://docs.ollama.com/api/introduction
- Ollama GitHub API reference: https://github.com/ollama/ollama/blob/main/docs/api.md
- OpenAI compatibility: https://docs.ollama.com/api/openai-compatibility
- Ollama Python library: https://github.com/ollama/ollama-python (PyPI: ollama v0.6.1)
- DeepWiki API reference: https://deepwiki.com/ollama/ollama/3-api-reference
- DeepWiki OpenAI compatibility: https://deepwiki.com/ollama/ollama/3.4-openai-compatibility-layer
