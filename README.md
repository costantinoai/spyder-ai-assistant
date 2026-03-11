# spyder-ai-assistant

[![PyPI](https://img.shields.io/pypi/v/spyder-ai-assistant)](https://pypi.org/project/spyder-ai-assistant/)
[![Alpha](https://img.shields.io/badge/status-alpha-orange)]()
[![License: CC BY-NC 4.0](https://img.shields.io/badge/license-CC%20BY--NC%204.0-lightgrey.svg)](https://creativecommons.org/licenses/by-nc/4.0/)
[![Spyder 6+](https://img.shields.io/badge/spyder-%E2%89%A5%206.0-red)](https://www.spyder-ide.org/)
[![Python 3.11+](https://img.shields.io/badge/python-%E2%89%A5%203.11-blue)](https://www.python.org/)

AI-powered code assistance for [Spyder IDE](https://www.spyder-ide.org/), running entirely on your machine through [Ollama](https://ollama.com/). No cloud services, no API keys, no data leaves your computer.

![Chat panel with AI explaining a Python script](docs/screenshots/chat-panel.png)

## What it does

**Chat panel** — A dockable pane where you talk to a local LLM about your code. It supports multi-tab sessions, streams responses token by token, renders Markdown with syntax-highlighted code blocks, and gives you copy and insert-to-editor buttons on every code snippet.

**Editor context awareness** — The AI automatically sees your current file, cursor position, selection, other open tabs, and your project's file tree. Right-click any selection in the editor to trigger actions like *Ask AI*, *Explain*, *Fix*, or *Add Docstring*.

**Inline code completions** — Copilot-style ghost text appears as you type. Press Tab to accept. The completions use Ollama's Fill-in-Middle API when the model supports it, with automatic fallback to prefix-only generation. You can also trigger completions manually with `Ctrl+Shift+Space`.

![Ghost text inline completions](docs/screenshots/ghost-completions.png)

**Thinking/reasoning display** — If you use a model that emits `<think>` blocks (like QwQ or DeepSeek-R1), the plugin renders the reasoning process in a dimmed section above the actual response.

Everything runs locally on your GPU through Ollama. It works offline, air-gapped, and with no setup beyond installing the plugin and pulling a model.

---

## Quick start

### 1. Install Ollama

```bash
curl -fsSL https://ollama.com/install.sh | sh
ollama list   # verify it's running
```

### 2. Pull a model

You'll want at least one model for chat. A separate smaller model for completions is optional but recommended for lower latency.

**Chat models** (pick one that fits your GPU):

| VRAM | Model | Command |
|------|-------|---------|
| 8 GB | Qwen 2.5 7B | `ollama pull qwen2.5:7b` |
| 12 GB | Qwen 2.5 14B | `ollama pull qwen2.5:14b` |
| 16 GB+ | Qwen 3.5 27B | `ollama pull huihui_ai/qwen3.5-abliterated:27b` |

**Completion models** (optional, for inline suggestions):

| VRAM | Model | Command |
|------|-------|---------|
| 8 GB | Qwen 2.5 Coder 3B | `ollama pull qwen2.5-coder:3b` |
| 12 GB+ | Qwen3 Coder 30B (3B active) | `ollama pull qooba/qwen3-coder-30b-a3b-instruct:q3_k_m` |

### 3. Install the plugin

```bash
pip install spyder-ai-assistant
```

Or from source:

```bash
pip install git+https://github.com/costantinoai/spyder-ai-assistant.git
```

> Install into the same Python environment where Spyder lives (e.g., your conda env).

### 4. Restart Spyder

The plugin registers itself automatically. After restart, open the chat panel from **View > Panes > AI Chat**. Your Ollama models appear in the toolbar dropdown. Completions start working as you type.

No configuration files, no API keys, no setup wizards.

---

## Features in detail

### Chat panel

Open via **View > Panes > AI Chat**. Type a message and press Enter.

Each conversation lives in its own tab — click "+" to start a new one. Responses stream in real time, and code blocks come with syntax highlighting (Pygments, with automatic dark/light theme detection). You can copy any code block to your clipboard or insert it directly into the editor at your cursor position.

Models that support reasoning (those that emit `<think>` blocks) show their thinking process in a dimmed, collapsible section above the response. You can switch models mid-conversation from the toolbar dropdown, which shows each model's size and VRAM usage. Click Stop to cancel a response mid-stream, and use Export to save any session as Markdown.

### Editor integration

Select code in the editor and right-click for AI actions:

| Action | What it does |
|--------|-------------|
| **Ask AI** | Opens the chat with your selection as context |
| **Explain** | Asks the AI to explain the selected code |
| **Fix** | Asks the AI to find and fix bugs |
| **Add Docstring** | Generates a docstring for the selected function or class |

Behind the scenes, the AI always has access to the full content of your current file (up to 50K chars), summaries of your other open files, your project's file tree, and your cursor position. This context is assembled automatically — you don't need to copy-paste anything.

### Inline completions

Completions trigger automatically as you type (with a 300ms debounce). Ghost text appears dimmed at your cursor — press Tab to accept it. The status bar at the bottom shows the current state: the active model name, "offline" if Ollama isn't reachable, or "generating" while a completion is in flight.

The plugin uses Ollama's FIM (Fill-in-Middle) API for models that support it, which produces better completions because the model can see code both before and after the cursor. For models without FIM support, it falls back to prefix-only generation automatically.

---

## Configuration

All settings live in Spyder's **Preferences** dialog.

**Preferences > AI Chat** covers the Ollama server URL, chat and completion model names, temperature, max tokens, keyboard shortcuts, the system prompt, and the action prompt templates (which support `{filename}` and `{code}` placeholders).

**Preferences > Completion and linting > AI Chat** has completion-specific settings: enable/disable toggle, model selection, temperature, max tokens, and debounce delay.

---

## GPU and memory

Ollama uses your GPU automatically if CUDA or ROCm drivers are installed. Models load into VRAM on first request and stay resident until evicted.

Rough VRAM requirements for Q4_K_M quantized models:

| Model size | VRAM needed | Typical use |
|-----------|-------------|-------------|
| 3B | ~2.5 GB | Completions |
| 7B | ~5 GB | Basic chat |
| 14B | ~9 GB | Good chat |
| 27B | ~15 GB | Excellent chat |

Running chat and completions simultaneously keeps both models in VRAM. A 7B chat model plus a 3B completion model needs about 7.5 GB total. If you exceed your VRAM, Ollama swaps models in and out (slower, but it works).

Without a GPU, Ollama falls back to CPU. Expect 1–3 tokens/sec on a 3B model — usable but not snappy.

---

## Troubleshooting

**"No models found" in the dropdown** — Ollama isn't running or has no models. Run `curl http://localhost:11434/api/tags` to check, then `ollama pull qwen2.5:7b` to grab a model.

**Completions aren't appearing** — Check that they're enabled in Preferences > Completion and linting > AI Chat. The status bar should show "AI: model-name". If it says "AI: offline", Ollama isn't reachable. Check Spyder's Internal Console (View > Panes > Internal Console) for error messages.

**Chat panel doesn't show up** — Look in View > Panes for "AI Chat". If it's not listed, the plugin may be installed in a different Python environment than Spyder. Verify with `python -c "import spyder_ai_assistant; print('OK')"` and restart Spyder.

**Slow responses** — Try a smaller or more aggressively quantized model. Check GPU usage with `nvidia-smi` or `rocm-smi`. The first request after startup is always slower while the model loads into VRAM.

**Too much VRAM usage** — Run `ollama ps` to see loaded models and `ollama stop <model-name>` to unload them.

---

## Architecture

The plugin registers two independent components via separate entry points:

```
spyder.plugins     →  AIChatPlugin (SpyderDockablePlugin)
                      Chat panel, editor integration, context menu actions

spyder.completions →  AIChatCompletionProvider (SpyderCompletionProvider)
                      Inline code completions, status bar widget
```

Both share the same `OllamaClient` backend but operate independently. You can use chat without completions and vice versa.

```
src/spyder_ai_assistant/
├── plugin.py                 # Main plugin: pane, menus, actions
├── completion_provider.py    # Completion provider: FIM completions
├── backend/
│   ├── client.py             # OllamaClient: Ollama API wrapper
│   └── worker.py             # OllamaWorker: QThread for streaming
├── widgets/
│   ├── chat_widget.py        # Chat pane: tabs, toolbar, input
│   ├── chat_display.py       # Message rendering: Markdown, highlighting
│   ├── chat_input.py         # Auto-resizing input text area
│   ├── config_page.py        # Preferences page
│   ├── ghost_text.py         # Ghost text overlay for completions
│   └── status.py             # Status bar widget
└── utils/
    └── context.py            # Editor context extraction
```

---

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup, release process, and how to contribute.

---

## Roadmap

This is an active project. Here's where it's headed, roughly in priority order.

### Deep terminal and kernel integration *(top priority)*

The chat panel (not completions — those stay lightweight and code-only) will get deep access to your running IPython kernel. The AI will be able to inspect live variables (types, shapes, values), read tracebacks from the console and suggest fixes, execute commands in the kernel on your behalf (with confirmation), and bridge with Spyder's Variable Explorer. The goal is full situational awareness: the AI knows what's in your session, not just what's in your files.

### Session history and persistence

Chat sessions will be saved automatically and tied to your Spyder project. Conversations persist in the `.spyproject` folder so they're there when you reopen the project — no more losing context between sessions. Full session history with search, so you can find that useful exchange from last week.

### Multi-provider support

Beyond Ollama, the plugin will support OpenAI and Anthropic APIs (bring your own key), as well as Claude Code and Codex for users who want cloud-grade models. A unified model selector will list local and cloud models together.

### Smart setup and model management

One-click Ollama installation from the preferences page, guided model downloads without touching the terminal, and hardware-aware recommendations that detect your VRAM/RAM and suggest the best models for your system — separately for completions (fast and small) and chat (capable and reasoning-oriented).

### Smarter completions

Better context selection for the completion model: scope-aware truncation that prioritizes the current function, imports, and type hints. Rename-aware suggestions that detect when you've renamed a variable and offer to propagate the change. Multi-site edit proposals with inline accept/reject overlays.

### Better edit UX

Accept/reject markers on AI-suggested edits, inline diff previews before applying changes, and proper undo integration so Ctrl+Z reverts an entire AI suggestion as one step.

### Comprehensive settings pane

Every tunable aspect of the plugin exposed through Spyder's Preferences: context window sizes, debounce delays, stop sequences, LSP popup suppression when AI completions are active, Ollama binary/data paths, provider API keys, and one-click restore to defaults.

### UI and experience

A redesigned chat panel with better typography and smoother streaming. Tables and LaTeX math rendering in responses. Full-text search across all chat sessions. A prompt templates library for common tasks. Token usage and cost display for cloud providers.

### Agent and workflow features

Multi-step task execution where the AI plans and applies changes across multiple files (with approval gates). Git-aware context that includes recent diffs and branch information in the AI's awareness.

---

---

## License

This project is licensed under the [Creative Commons Attribution-NonCommercial 4.0 International License](https://creativecommons.org/licenses/by-nc/4.0/). You're free to use, share, and adapt it for non-commercial purposes, as long as you give appropriate credit. See [LICENSE](LICENSE) for the full text.
