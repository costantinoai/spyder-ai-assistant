# spyder-ai-chat

AI-powered code assistance for [Spyder IDE](https://www.spyder-ide.org/) using **local** [Ollama](https://ollama.com/) models. No cloud, no API keys, no data leaves your machine.

## What You Get

- **Chat panel** — Dockable pane to chat with a local LLM. Multi-tab sessions, streaming responses, Markdown rendering with syntax-highlighted code blocks, copy & insert-to-editor buttons.
- **Editor context awareness** — The AI sees your current file, cursor position, selection, other open files, and project structure. Right-click actions: *Ask AI*, *Explain*, *Fix*, *Add Docstring*.
- **Code completions** — Inline AI completions as you type (Copilot-style). Tab to accept, with ghost text preview. Trigger manually with `Ctrl+Shift+Space`.
- **Thinking/reasoning display** — Models that emit `<think>` blocks show a collapsible reasoning section above the response.
- **Fully local** — Runs on your GPU via Ollama. Works offline, works air-gapped.

---

## Quick Start

### 1. Install Ollama

```bash
curl -fsSL https://ollama.com/install.sh | sh
```

Ollama runs as a system service on port `11434`. Verify it's running:

```bash
ollama list    # should respond (even if empty)
```

### 2. Pull a model

For **chat** (the main conversational AI), pull a model that fits your GPU:

| GPU VRAM | Recommended Model | Command |
|----------|-------------------|---------|
| 8 GB     | Qwen 2.5 7B       | `ollama pull qwen2.5:7b` |
| 12 GB    | Qwen 2.5 14B      | `ollama pull qwen2.5:14b` |
| 16 GB+   | Qwen 3.5 27B      | `ollama pull huihui_ai/qwen3.5-abliterated:27b` |

For **code completions** (inline suggestions while typing), pull a fast code model:

| GPU VRAM | Recommended Model | Command |
|----------|-------------------|---------|
| 8 GB     | Qwen 2.5 Coder 3B | `ollama pull qwen2.5-coder:3b` |
| 12 GB+   | Qwen3 Coder 30B (3B active MoE) | `ollama pull qooba/qwen3-coder-30b-a3b-instruct:q3_k_m` |

> **Tip**: You can use the same model for both chat and completions. A separate fast model for completions gives a better experience (lower latency), but isn't required.

### 3. Install the plugin

```bash
pip install git+https://github.com/YOUR_USER/spyder-ai-chat.git
```

Or for development:

```bash
git clone https://github.com/YOUR_USER/spyder-ai-chat.git
cd spyder-ai-chat
pip install -e .
```

> **Important**: Install into the same Python environment where Spyder is installed (e.g., your conda env).

### 4. Restart Spyder

The plugin registers automatically. After restart:

- **Chat panel**: appears in **View > Panes > AI Chat**
- **Completions**: appear automatically as you type (configurable in Preferences)
- **Model selector**: dropdown in the chat panel toolbar — all your Ollama models are listed

That's it. No configuration files, no API keys, no setup wizards.

---

## Features

### Chat Panel

Open via **View > Panes > AI Chat**. Type a message and press Enter (or click Send).

- **Multi-tab sessions** — Click "+" to open new chat tabs. Each tab has its own conversation history.
- **Streaming responses** — Tokens appear in real-time as the model generates them.
- **Syntax highlighting** — Code blocks are highlighted with Pygments (dark/light theme auto-detected).
- **Code actions** — Each code block has a *Copy* button and an *Insert into Editor* button.
- **Thinking display** — Models with reasoning capabilities show their thinking process in a dimmed, collapsible section.
- **Export** — Save any chat session as a Markdown file.
- **Model switching** — Change models mid-conversation from the toolbar dropdown. Shows model size and VRAM usage.
- **Stop generation** — Click Stop to cancel a response mid-stream.

### Editor Integration

Right-click on selected code in the editor for AI actions:

| Action | What it does |
|--------|-------------|
| **Ask AI** | Opens the chat with your selection as context |
| **Explain** | Asks the AI to explain the selected code |
| **Fix** | Asks the AI to find and fix bugs in the selection |
| **Add Docstring** | Generates a docstring for the selected function/class |

The AI always sees:
- The **full content** of your current file (up to 50K chars)
- **Summaries** of other open files (first ~30 lines each, up to 8 files)
- Your **project structure** (file tree, up to 3 levels deep)
- Your **cursor position** and **selected text**

### Code Completions

Inline AI completions powered by Ollama's Fill-in-Middle (FIM) API:

- **Automatic**: Completions trigger as you type (300ms debounce).
- **Manual trigger**: Press `Ctrl+Shift+Space` to request a completion at cursor.
- **Tab to accept**: Ghost text appears dimmed — press Tab to insert it.
- **Status bar**: Shows completion status ("AI: model-name", "AI: offline", etc.)

---

## Configuration

All settings are in Spyder's **Preferences** dialog:

### Chat Settings

**Preferences > AI Chat**

| Setting | Default | Description |
|---------|---------|-------------|
| Ollama Host | `http://localhost:11434` | URL of the Ollama API server |
| Chat Model | (auto-detected) | Model used for chat conversations |
| Temperature | `0.7` | Creativity of responses (0.0–2.0) |
| System Prompt | (built-in) | Custom system prompt for the AI |
| Action Prompts | (built-in) | Templates for Explain, Fix, Docstring actions |
| Completion Shortcut | `Ctrl+Shift+Space` | Keyboard shortcut for manual completions |

### Completion Settings

**Preferences > Completion and linting > AI Chat**

| Setting | Default | Description |
|---------|---------|-------------|
| Enable completions | `true` | Toggle inline AI completions on/off |
| Completion Model | (auto-detected) | Model used for code completions |
| Temperature | `0.15` | Lower = more predictable completions |
| Max Tokens | `256` | Maximum length of a completion |
| Debounce (ms) | `300` | Delay before triggering after typing stops |

---

## GPU & Memory Guide

Ollama automatically uses your GPU if CUDA or ROCm drivers are installed. Models are loaded into VRAM on first request and stay resident until Ollama evicts them.

### How much VRAM do I need?

A rough guide for quantized models (Q4_K_M, the most common quantization):

| Model Size | VRAM Required | Example |
|-----------|---------------|---------|
| 3B params | ~2.5 GB | Code completions |
| 7B params | ~5 GB | Basic chat |
| 14B params | ~9 GB | Good chat quality |
| 27B params | ~15 GB | Excellent quality |

**Running chat + completions simultaneously**: Both models stay in VRAM. A 7B chat + 3B completion model needs ~7.5 GB. If you run out of VRAM, Ollama swaps models (slower but works).

### CPU-only mode

If you don't have a GPU, Ollama falls back to CPU. Expect:
- 3B models: usable (1-3 tokens/sec)
- 7B+ models: slow but functional
- Completions: may feel laggy

---

## Troubleshooting

### "No models found" in the dropdown

Ollama is either not running or has no models installed:

```bash
# Check if Ollama is running
curl http://localhost:11434/api/tags

# Pull a model
ollama pull qwen2.5:7b
```

### Completions aren't appearing

1. Check that completions are enabled: **Preferences > Completion and linting > AI Chat > Enable completions**
2. Check the status bar at the bottom — it should show "AI: model-name"
3. If it shows "AI: offline", Ollama may not be running
4. Check Spyder's internal console (**View > Panes > Internal Console**) for errors

### Chat panel doesn't appear

1. Check **View > Panes** — "AI Chat" should be listed
2. If not listed, the plugin may not be installed in the correct environment:
   ```bash
   python -c "import spyder_ai_chat; print('OK')"
   ```
3. Restart Spyder after installation

### Responses are slow

- Use a smaller or more quantized model
- Check GPU utilization: `nvidia-smi` (NVIDIA) or `rocm-smi` (AMD)
- Ensure the model is loaded in VRAM (first request after startup is slower)

### Ollama uses too much VRAM

```bash
# See loaded models and their VRAM usage
ollama ps

# Unload all models from VRAM
ollama stop <model-name>
```

---

## Architecture

The plugin has two components registered via separate entry points:

```
spyder.plugins  →  AIChatPlugin (SpyderDockablePlugin)
                   └── Chat panel, editor integration, context menu actions

spyder.completions  →  AIChatCompletionProvider (SpyderCompletionProvider)
                       └── Inline code completions, status bar widget
```

Both share the `OllamaClient` backend but run independently. You can use chat without completions and vice versa.

### Source Layout

```
src/spyder_ai_chat/
├── plugin.py                 # Main plugin: registers pane, menus, actions
├── completion_provider.py    # Completion provider: FIM completions
├── backend/
│   ├── client.py             # OllamaClient: API wrapper
│   └── worker.py             # OllamaWorker: QThread for streaming chat
├── widgets/
│   ├── chat_widget.py        # Chat pane: tabs, input, toolbar
│   ├── chat_display.py       # Message rendering: Markdown, syntax highlighting
│   ├── chat_input.py         # Auto-resizing input text area
│   ├── config_page.py        # Preferences page for chat settings
│   ├── ghost_text.py         # Ghost text overlay for completions
│   └── status.py             # Status bar widget for completion state
└── utils/
    └── context.py            # Editor context extraction and system prompt building
```

---

## Development

```bash
git clone https://github.com/YOUR_USER/spyder-ai-chat.git
cd spyder-ai-chat
pip install -e ".[dev]"

# Run tests
pytest

# Launch Spyder with the plugin
spyder
```

### Dependencies

- `spyder >= 6.0.0` — The IDE
- `ollama >= 0.4.0` — Python client for Ollama's API
- `Pygments` — Syntax highlighting (bundled with Spyder)

---

## Roadmap

Planned features and improvements, roughly in priority order.

### Deep Terminal & Kernel Integration *(top priority)*

> **Scope:** This applies to the **chat panel only**. Autocompletion stays lightweight — it only sees the code surrounding the cursor, not the kernel or variables.

- **Live kernel variable access** — The chat AI can inspect variables in the running Spyder/IPython kernel: types, shapes, values, dtypes. Ask "what's in `df`?" and get a real answer, not a guess.
- **Runtime-aware chat** — Chat responses informed by the actual state of the session: loaded modules, defined functions, DataFrame columns, tensor shapes, fitted model parameters.
- **Error diagnosis from terminal output** — AI reads tracebacks and stderr from the IPython console and proactively suggests fixes, with one-click "apply fix" actions.
- **Command execution** — Let the AI run commands in the IPython console on your behalf (with confirmation), e.g., "show me the first 5 rows of `df`" → executes `df.head()` and feeds the result back into the conversation.
- **Variable explorer integration** — Bridge with Spyder's Variable Explorer so the AI knows what's in your workspace without you having to describe it.
- **Session context in chat** — Automatically include recent console history, errors, and key variable summaries in the system prompt so the AI has full situational awareness.

### Multi-Provider Support

- **OpenAI and Anthropic API integration** — Use GPT-4, Claude, or other cloud models alongside local Ollama models. Bring your own API key.
- **Claude Code / Codex integration** — Connect to Claude Code or OpenAI Codex via subscription or API for users who want cloud-grade completions without running local models.
- **Unified model selector** — Single dropdown that lists local (Ollama) and cloud models together, with clear provider labels.

### Smart Setup & Model Management

- **One-click Ollama install** — Detect if Ollama is missing and offer to download and install it directly from the plugin settings page.
- **Guided model download** — Browse, search, and pull Ollama models from the preferences UI without touching the terminal.
- **Hardware-aware model recommendations** — Use [LLMFit](https://github.com/containers/ramalama/tree/main/ramalama/model_inspect) (or similar) to detect available VRAM/RAM and recommend the best models that fit the current system — separately for completions (fast, small) and chat (capable, reasoning).
- **Dual-model configuration** — Explicit split setup: one model optimized for fast autocompletion (low latency, small footprint) and one for chat/reasoning (larger, thinking-capable, agentic).

### Smarter Completions

- **Improved context for completions** — Better selection of reference lines and surrounding code sent to the completion model. Smarter truncation, scope-aware context (e.g., prioritize the current function, imports, and type hints).
- **Rename-aware suggestions** — VS Code-style intelligence: when you rename a variable, the AI detects the pattern and proactively suggests the same rename at all other usage sites in the file.
- **Multi-site edit suggestions** — Inline diff overlays (accept/reject per change) for AI-proposed edits across multiple locations, similar to VS Code's Copilot Edit experience.

### Better Edit UX

- **Accept/reject overlays** — Green/red inline markers on AI-suggested edits with one-click accept or reject, instead of replacing code silently.
- **Inline diff preview** — Show a side-by-side or inline diff before applying any AI-generated change to the editor.
- **Undo integration** — Group all AI-applied changes into a single undo step so Ctrl+Z reverts the entire suggestion cleanly.

### Comprehensive Settings Pane

- **All settings in one place** — Every configurable aspect of the plugin exposed through Spyder's Preferences dialog, organized into clear sections.
- **Completion tuning** — Context window size (prefix/suffix chars), debounce delay, max tokens, temperature, stop sequences, and whether to suppress Spyder's built-in LSP suggestion popup when AI completions are active.
- **Chat tuning** — System prompt, action prompt templates ({filename}, {code} placeholders), context budget limits (max file chars, max open files, tree depth), and conversation history length.
- **Ollama management** — Server URL, custom Ollama binary/data directory path, model pull progress, and connection health indicator.
- **Provider settings** — API keys and endpoints for cloud providers (OpenAI, Anthropic) when multi-provider support lands.
- **Keyboard shortcuts** — Configurable keybindings for manual completion trigger, accept/reject, and chat panel toggle.
- **Defaults and reset** — One-click restore to defaults per section.

### UI & Experience

- **Redesigned chat panel** — Modern, polished UI with better typography, avatars, and smoother streaming animations.
- **Markdown preview improvements** — Tables, LaTeX math rendering, and image support in chat responses.
- **Conversation search** — Full-text search across all chat sessions and tabs.
- **Prompt templates library** — Saved prompt templates for common tasks (review, refactor, test generation, etc.) accessible from a quick-pick menu.
- **Token usage display** — Show token count and estimated cost (for cloud providers) per message and per session.

### Agent & Workflow Features

- **Multi-step task execution** — Let the AI plan and execute multi-file changes autonomously (with approval gates), similar to agentic coding assistants.
- **Git-aware context** — Include recent diffs, commit messages, and branch context in the AI's awareness for more relevant suggestions.

---

## License

This project is licensed under the [Creative Commons Attribution-NonCommercial 4.0 International License](https://creativecommons.org/licenses/by-nc/4.0/).

You are free to use, share, and adapt this software for **non-commercial purposes**, provided you give appropriate credit. See [LICENSE](LICENSE) for details.
