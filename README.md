# spyder-ai-assistant

[![PyPI](https://img.shields.io/pypi/v/spyder-ai-assistant)](https://pypi.org/project/spyder-ai-assistant/)
[![Alpha](https://img.shields.io/badge/status-alpha-orange)]()
[![License: CC BY-NC 4.0](https://img.shields.io/badge/license-CC%20BY--NC%204.0-lightgrey.svg)](https://creativecommons.org/licenses/by-nc/4.0/)
[![Spyder 6+](https://img.shields.io/badge/spyder-%E2%89%A5%206.0-red)](https://www.spyder-ide.org/)
[![Python 3.11+](https://img.shields.io/badge/python-%E2%89%A5%203.11-blue)](https://www.python.org/)

AI-powered code assistance for [Spyder IDE](https://www.spyder-ide.org/), built local-first around [Ollama](https://ollama.com/) and now able to talk to OpenAI-compatible chat endpoints when you want a remote or self-hosted alternative. Local workflows stay fully offline, and runtime inspection remains read-only inside Spyder.

![Chat panel with AI explaining a Python script](docs/screenshots/chat-panel.png)

## What it does

**Chat panel** — A dockable pane where you talk to a model about your code. It supports provider-aware model selection, multi-tab sessions, per-tab chat modes, per-tab inference settings, per-exchange deletion, streams responses token by token, restores saved conversations, includes a history browser for reopening, duplicating, and deleting saved sessions, renders Markdown with syntax-highlighted code blocks, and gives you copy, insert-at-cursor, and replace-selection actions on every code snippet.

**Editor context awareness** — The AI automatically sees your current file, cursor position, selection, other open tabs, and your project's file tree. Right-click any selection in the editor to trigger actions like *Ask AI*, *Explain*, *Fix*, or *Add Docstring*.

**Inline code completions** — Copilot-style ghost text appears as you type. Press Tab to accept, `Alt+Right` to accept the next word-like segment, `Alt+Shift+Right` to accept the next line, or keep typing through a matching suggestion without losing the remaining tail. The completion provider now keeps a small local LRU cache, trims suffix overlap before display, filters obviously repetitive output, suppresses Spyder's native completion popup when a ghost suggestion is already active, pulls small relevant snippets from other open files, and can cycle through alternative candidates for the same target when you request another suggestion.

![Ghost text inline completions](docs/screenshots/ghost-completions.png)

**Thinking/reasoning display** — If you use a model that emits `<think>` blocks (like QwQ or DeepSeek-R1), the plugin renders the reasoning process in a dimmed section above the actual response.

**Live runtime inspection** — The chat panel can inspect the active Spyder IPython session on demand. It does not dump your console, variables, or kernel state into every prompt. Instead, the system prompt teaches the chat model a small read-only inspection protocol so it can ask for the latest traceback, recent console output, or specific live variables only when the question actually depends on runtime state.

**Debugging workflows** — The chat toolbar shows the active kernel state, and the quick-action row gives you one-click paths for `Explain Error`, `Fix Traceback`, `Use Variables`, `Use Console`, and `Regenerate`.

By default, everything runs locally on your GPU through Ollama. It still works offline and air-gapped with no setup beyond installing the plugin and pulling a model, but the chat pane can also target an OpenAI-compatible endpoint when you configure one in Preferences.

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

The plugin registers itself automatically. After restart, open the chat panel from **View > Panes > AI Chat**. Your available chat models appear in the toolbar dropdown, grouped by provider. Completions stay Ollama-backed and start working as you type.

Optional: if you want to use a compatible chat endpoint, set its base URL and API key under **Preferences > AI Chat**.

---

## Features in detail

### Chat panel

Open via **View > Panes > AI Chat**. Type a message and press Enter.

Each conversation lives in its own tab — click "+" to start a new one. Responses stream in real time, and code blocks come with syntax highlighting (Pygments, with automatic dark/light theme detection). You can copy any code block to your clipboard, insert it at the current caret, or replace the current editor selection directly from the chat.

Models that support reasoning (those that emit `<think>` blocks) show their thinking process in a dimmed section above the response. You can switch models mid-conversation from the toolbar dropdown, and that selector now lists provider-aware entries such as `[Ollama] ...` and `[OpenAI-compatible] ...`. You can choose a per-tab chat mode (`Coding`, `Debugging`, `Explanation`, or `Documentation`) from the toolbar preset selector, open `Settings` to override temperature and max tokens for just the active tab, delete any saved exchange from the active conversation through `Delete Turn`, click Stop to cancel a response mid-stream, use `Regenerate` to rerun the last user turn on the active tab, and use Export to save any session as Markdown with model, chat mode, per-tab inference settings, editor, and runtime metadata.

Chat sessions persist automatically. When a Spyder project is open, conversations are stored in `.spyproject/ai-assistant/chat-sessions.json` and restored when that project is reopened. When no project is active, the plugin falls back to a global session file in Spyder's config directory. The `History` button and `Chat History...` menu entry let you browse saved sessions in the current scope, reopen one into a tab, duplicate it into a new branch of the conversation, or delete it from the archive. The selected chat mode plus any per-tab temperature or max-token override also persist with the session.

When a question depends on your live session, the chat can inspect the active kernel in a read-only way. That includes:

- the latest traceback or error block
- recent visible console output
- the current variable list
- targeted inspection of named variables

This runtime inspection is on demand, not automatic. Ordinary code questions stay file-focused and lean by default. Runtime inspection never executes code on your behalf in Phase 2; it only reads state that Spyder already exposes through the current IPython console and Variable Explorer integration.

The chat toolbar also exposes the active kernel state without attaching runtime data to every prompt. The quick-action row is tuned for common debugging loops:

- `Explain Error` asks the model to inspect the latest traceback first.
- `Fix Traceback` asks for a concrete fix based on the latest runtime failure.
- `Use Variables` asks the model to inspect the current variable state only when needed.
- `Use Console` asks the model to inspect recent visible console output.
- `Regenerate` removes the last assistant answer on the active tab and reruns the last user turn.
- `Delete Turn` opens a browser for removing one saved exchange from the active tab.

The `Settings` button applies only to the active tab. A debugging tab can run with a low temperature and short responses while a drafting tab keeps the global defaults or uses a higher-temperature override. Changing one tab does not mutate the plugin-wide preferences.

Chat provider behavior is intentionally split:

- chat can target Ollama or an OpenAI-compatible endpoint
- inline completions remain Ollama-backed for now
- the unified chat model selector shows provider metadata in the dropdown and tooltip

### Editor integration

Select code in the editor and right-click for AI actions:

| Action | What it does |
|--------|-------------|
| **Ask AI** | Opens the chat with your selection as context |
| **Explain** | Asks the AI to explain the selected code |
| **Fix** | Asks the AI to find and fix bugs |
| **Add Docstring** | Generates a docstring for the selected function or class |

Behind the scenes, the AI always has access to the full content of your current file (up to 50K chars), summaries of your other open files, your project's file tree, and your cursor position. This context is assembled automatically — you don't need to copy-paste anything.

When the chat produces code, the code-block actions let you either insert it at the current caret or replace the current editor selection explicitly.

### Inline completions

Completions trigger automatically as you type with a 100 ms debounce. Ghost text appears dimmed at your cursor. The main controls are:

- `Tab` to accept the full suggestion
- `Alt+Right` to accept the next word-like segment
- `Alt+Shift+Right` to accept the next line
- `Escape` to dismiss the current suggestion
- normal typing when the next characters already match the suggestion

The completion provider now does more than just call Ollama. It keeps a small local LRU cache for repeated prompts, trims suffix overlap before display so closing brackets or delimiters are not duplicated, filters clearly repetitive low-value outputs, blocks Spyder's native completion popup when an AI ghost suggestion is already active, pulls small relevant snippets from other tracked open files, and remembers alternative candidates for the same target so repeated requests can cycle locally instead of always waiting for another model round trip. The status bar at the bottom still shows the active model name, `offline` if Ollama is not reachable, or `generating` while a completion is in flight, and its tooltip now includes local completion lifecycle counters for debugging and tuning.

The plugin uses Ollama's FIM (Fill-in-Middle) API for models that support it, which produces better completions because the model can see code both before and after the cursor. For models without FIM support, it falls back to prefix-only generation automatically. The provider also suppresses low-value requests in bad contexts, such as extremely short prefixes or mid-line positions where substantial code already exists to the right of the cursor. For richer suggestions, it can reuse just enough relevant context from other open files instead of blindly copying the whole editor set into every completion request.

---

## Configuration

All settings live in Spyder's **Preferences** dialog.

**Preferences > AI Chat** covers the default chat provider, the Ollama server URL, the optional OpenAI-compatible base URL and API key, chat and completion model names, temperature, max tokens, keyboard shortcuts, the base system prompt, and the action prompt templates (which support `{filename}` and `{code}` placeholders). The completion keyboard section now includes the manual trigger shortcut plus the partial-accept shortcuts for next word and next line. The active tab's chat mode and per-tab inference overrides are controlled directly from the chat pane and persist with the session without changing these global defaults.

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

**"No models found" in the dropdown** — if you're using Ollama, check `curl http://localhost:11434/api/tags` and pull a model such as `ollama pull qwen2.5:7b`. If you're using a compatible endpoint, confirm the configured base URL answers on `/v1/models` and the API key is valid.

**Completions aren't appearing** — Check that they're enabled in Preferences > Completion and linting > AI Chat. The status bar should show "AI: model-name". If it says "AI: offline", Ollama isn't reachable. Check Spyder's Internal Console (View > Panes > Internal Console) for error messages.

**Chat panel doesn't show up** — Look in View > Panes for "AI Chat". If it's not listed, the plugin may be installed in a different Python environment than Spyder. Verify with `python -c "import spyder_ai_assistant; print('OK')"` and restart Spyder.

**Slow responses** — Try a smaller or more aggressively quantized model. Check GPU usage with `nvidia-smi` or `rocm-smi`. The first request after startup is always slower while the model loads into VRAM.

**Too much VRAM usage** — Run `ollama ps` to see loaded models and `ollama stop <model-name>` to unload them.

**Live runtime questions get a generic answer or `Empty response` instead of inspecting the kernel** — Switch to a stronger instruction-following chat model. The runtime bridge depends on the model being willing to emit a small structured request block when it needs console or variable data. In local validation, Qwen-based chat models handled this reliably; weaker or less compliant models may ignore the protocol or return nothing.

---

## Architecture

The plugin registers two independent components via separate entry points:

```
spyder.plugins     →  AIChatPlugin (SpyderDockablePlugin)
                      Chat panel, editor integration, context menu actions

spyder.completions →  AIChatCompletionProvider (SpyderCompletionProvider)
                      Inline code completions, status bar widget
```

The chat and completion paths now share some context and prompt utilities, but the transports are intentionally separate:

- chat uses a provider registry with Ollama plus optional OpenAI-compatible backends
- completions stay Ollama-only and optimize for low-latency inline behavior

```
src/spyder_ai_assistant/
├── plugin.py                 # Main plugin: pane, menus, actions
├── completion_provider.py    # Completion provider: FIM completions
├── backend/
│   ├── client.py             # OllamaClient: Ollama API wrapper
│   ├── chat_providers.py     # Provider registry + OpenAI-compatible chat backend
│   └── worker.py             # ChatWorker: QThread bridge for provider-aware chat
├── utils/
│   ├── completion_context.py # Neighbor-file snippet selection + candidate scoring
│   ├── context.py            # Editor/project context + prompt assembly
│   ├── chat_inference.py     # Per-tab chat option normalization/resolution
│   ├── chat_exchanges.py     # Exchange browsing and deletion helpers
│   ├── chat_persistence.py   # Project/global chat session storage
│   ├── prompt_library.py     # Built-in per-tab chat modes
│   ├── runtime_bridge.py     # Read-only runtime inspection protocol
│   └── runtime_context.py    # Live shell snapshot service
├── widgets/
│   ├── chat_widget.py        # Chat pane: tabs, toolbar, input
│   ├── chat_display.py       # Message rendering: Markdown, highlighting
│   ├── chat_input.py         # Auto-resizing input text area
│   ├── chat_settings_dialog.py  # Per-tab chat settings UI
│   ├── config_page.py        # Preferences page
│   ├── exchange_delete_dialog.py  # Per-session exchange deletion UI
│   ├── ghost_text.py         # Ghost text overlay for completions
│   ├── session_history_dialog.py  # Saved-session browser UI
│   └── status.py             # Status bar widget
```

---

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup and contribution guidance, [docs/runtime-inspection.md](docs/runtime-inspection.md) for the runtime bridge design and validation checklist, [docs/chat-workflows.md](docs/chat-workflows.md) for the current chat UX, [docs/validation-workflow.md](docs/validation-workflow.md) for the tracked live Spyder validation harnesses, and [docs/release-workflow.md](docs/release-workflow.md) for the tag-driven release pipeline and post-release checks.

---

## Roadmap

This is an active project. Here's where it's headed, roughly in priority order.

### Deep terminal and kernel integration *(top priority)*

The shipped runtime bridge already gives the chat pane read-only access to the active IPython session on demand. The next step is deeper tooling around that bridge: richer variable renderers, stronger traceback-specific workflows, optional approved kernel actions, and tighter Variable Explorer integration. The goal remains full situational awareness: the AI knows what's in your session, not just what's in your files.

### Session history and persistence

Project-aware persistence and the history browser are already shipped: chat sessions are saved to `.spyproject/ai-assistant/chat-sessions.json` and restored when you reopen the project, with a global fallback when no project is active. The history browser can reopen, duplicate, and delete saved sessions in the current scope.

The remaining work here is the deeper management layer:

- search across past sessions
- pinning or labeling important conversations
- richer bulk management of saved conversations

### Multi-provider support

OpenAI-compatible chat support is now shipped. The next provider work is broader transport coverage and better provider ergonomics:

- named provider profiles instead of one compatible endpoint
- richer error reporting and connectivity diagnostics
- additional provider adapters beyond the current OpenAI-compatible path
- keeping the completion path local and Ollama-backed unless a later phase proves a strong reason to change that

### Smart setup and model management

One-click Ollama installation from the preferences page, guided model downloads without touching the terminal, and hardware-aware recommendations that detect your VRAM/RAM and suggest the best models for your system — separately for completions (fast and small) and chat (capable and reasoning-oriented).

### Smarter completions

Neighbor-file snippets, candidate scoring, and same-target cycling are now shipped. The next completion work is:

- better scope-aware truncation that prioritizes the current function, imports, and type hints
- rename-aware suggestions that detect when you've renamed a variable and offer to propagate the change
- multi-site edit proposals with inline accept/reject overlays

### Better edit UX

Accept/reject markers on AI-suggested edits, inline diff previews before applying changes, and proper undo integration so Ctrl+Z reverts an entire AI suggestion as one step.

### Comprehensive settings pane

Every tunable aspect of the plugin exposed through Spyder's Preferences: context window sizes, debounce delays, stop sequences, LSP popup suppression when AI completions are active, Ollama binary/data paths, provider API keys, and one-click restore to defaults.

### UI and experience

A redesigned chat panel with better typography and smoother streaming. Tables and LaTeX math rendering in responses. Full-text search across all chat sessions. A user-editable prompt templates library beyond the built-in shipped chat modes. Token usage and cost display for cloud providers.

### Agent and workflow features

Multi-step task execution where the AI plans and applies changes across multiple files (with approval gates). Git-aware context that includes recent diffs and branch information in the AI's awareness.

---

---

## License

This project is licensed under the [Creative Commons Attribution-NonCommercial 4.0 International License](https://creativecommons.org/licenses/by-nc/4.0/). You're free to use, share, and adapt it for non-commercial purposes, as long as you give appropriate credit. See [LICENSE](LICENSE) for the full text.
