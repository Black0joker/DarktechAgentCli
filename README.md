# DarktechAgent CLI

<div align="center">

<img src="images/darktech.png" alt="DarktechAgent Logo" width="200"/>

**An autonomous AI coding agent with a rich terminal UI.**

[![Python](https://img.shields.io/badge/Python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Textual](https://img.shields.io/badge/Textual-TUI%20Framework-purple.svg)](https://textual.textualize.io/)
[![Platform](https://img.shields.io/badge/Platform-Windows%20%7C%20Linux-green.svg)]()

</div>

---

## 📖 Overview

**DarktechAgent CLI** is a terminal-based AI coding agent that provides an interactive chat interface for autonomous code generation, file manipulation, shell command execution, and web research. Built on Python's [Textual](https://textual.textualize.io/) framework, it delivers a modern, responsive TUI experience with real-time streaming responses, tool execution visualization, and a comprehensive command system.

The agent connects to the **Darktech API** via a reverse-proxy REST service and supports multiple AI models with thinking/reasoning capabilities.

---

## ✨ Features

- **🤖 Autonomous Agent Loop** — The AI agent discovers, plans, codes, and verifies autonomously using a structured tool-calling protocol.
- **🖥️ Rich Terminal UI** — Built with Textual for a modern, dark-themed terminal experience with spinners, status bars, and streaming text.
- **🔧 17 Built-in Tools** — File I/O, shell commands, code interpreter, web search, grep, glob, and more.
- **🔐 Permission System** — `auto` mode (all tools allowed) or `read-only` mode (write tools require explicit approval).
- **🧠 Thinking Mode** — Toggle between `normal` and `thinking` modes for reasoning transparency.
- **🔄 Multi-Token Rotation** — Automatic token rotation on rate limits with chat migration across accounts (uses upload responses to track migrated chat IDs).
- **🛡️ Captcha Auto-Solve** — Integrated captcha solving via a remote solver API.
- **💾 Chat Caching** — Local persistence of all conversations in `chat_cache/` for offline access, listing (`/chats` reads from cache), and re-upload on demand.
- **📎 Image Attachments** — Attach images to prompts for multimodal interactions.
- **⌨️ Smart Autocomplete** — `/` command suggestions and `@` file/folder path browsing.
- **📊 Token Tracking** — Real-time token usage display in the status bar.
- **🔀 Multi-Model Support** — Switch between `darktech_v3`, `darktech_v2`, and `darktech_v1`.

---

## 🏗️ Architecture

```
DarktechAgentCli/
├── main.py                     # Application entry point
├── config/
│   ├── config.json             # Proxy URL & captcha solver config
│   └── tokenfile.json          # Authentication tokens (git-ignored)
├── app/
│   ├── app.py                  # MainApp — Textual application & command handler
│   ├── agent_wrapper.py        # AgentWrapper — async agent loop & API orchestration
│   ├── command_defs.py         # Command definitions & available models
│   ├── __init__.py
│   ├── agent/
│   │   ├── module.py           # Tool execution engine (all 17 tools)
│   │   ├── proxy_client.py     # ProxyClient — REST API client for Darktech proxy
│   │   ├── parser.py           # SSE stream parser
│   │   ├── edit_file.py        # 7-layer safety-first file editing engine
│   │   ├── chat_cache.py       # Per-chat message caching to disk
│   │   ├── configData.py       # Token/config file management
│   │   └── logger.py           # Structured logging (console + file)
│   ├── widgets/
│   │   ├── conversation.py     # Chat message display with streaming
│   │   ├── input_box.py        # Multi-line input with @ mentions
│   │   ├── status.py           # Status bar with spinner & elapsed timer
│   │   ├── suggestions.py      # Command & file autocomplete list
│   │   ├── tool_permission.py  # Tool permission dialog
│   │   ├── header.py           # Dynamic status header
│   │   ├── footer.py           # Workspace/model/mode footer
│   │   └── logo.py             # Gradient ASCII logo
│   ├── state/
│   │   └── app_state.py        # Application state (model, workspace, git branch)
│   ├── utils/
│   │   └── syntax_highlighter.py  # VSCode-like syntax highlighting
│   ├── screens/                # (reserved for future screens)
│   ├── layouts/                # (reserved for future layouts)
│   ├── animations/             # (reserved for future animations)
│   ├── commands/               # (reserved for future commands)
│   └── theme/                  # (reserved for future themes)
├── images/
│   └── app_icon.ico            # Application icon
├── chat_cache/                 # Local chat persistence (git-ignored)
├── DarktechAgent.spec          # PyInstaller spec (Windows)
├── DarktechAgent_linux.spec    # PyInstaller spec (Linux)
└── .gitignore
```

---

## 🚀 Getting Started

### Prerequisites

- **Python 3.10+**
- **pip** (package manager)
- A valid **Darktech API token**

> 📩 **Getting a Token:** Contact [@Warriorx0](https://t.me/Warriorx0) on Telegram to obtain your API token.

### Installation

```bash
# Clone the repository
git clone <repository-url>
cd DarktechAgentCli

# Create a virtual environment (recommended)
python -m venv venv

# Activate virtual environment
# Windows:
venv\Scripts\activate
# Linux/macOS:
source venv/bin/activate

# Install dependencies
pip install textual requests rich pygments
```

### Configuration

1. **Create/Edit `config/tokenfile.json`:**

```json
{
  "tokens": ["your-darktech-api-token-here"]
}
```

2. **Proxy configuration** is stored in `config/config.json`:

```json
{
  "DEFAULT_PROXY_URL": "https://darktechapi.runasp.net",
  "CAPTCHA_SOLVER_API_BASE": "https://capsolver.runasp.net"
}
```

### Running

```bash
python main.py
```

---

## 🔨 Building

### Windows

```bash
pip install pyinstaller
pyinstaller DarktechAgent.spec
```

### Linux

```bash
pip install pyinstaller
pyinstaller DarktechAgent_linux.spec
```

The compiled binary will be output to `dist/DarktechAgent`.

---

## 🛠️ Tools (Agent Capabilities)

The agent has access to the following tools at runtime:

| Tool | Description |
|------|-------------|
| `current_path` | Returns the workspace root path |
| `list_directory` | Lists directory contents |
| `glob` | Finds files matching a glob pattern |
| `grep_search` | Regex search across file contents |
| `read_file` | Reads file content (with line ranges) |
| `replace` | Surgical 7-layer file editing |
| `write_file` | Creates/overwrites files |
| `run_shell_command` | Executes shell commands (fg/bg) |
| `code_interpreter` | Executes Python code snippets |
| `list_background_processes` | Lists tracked background processes |
| `read_background_output` | Reads background process output |
| `kill_process` | Terminates a background process |
| `enter_plan_mode` | Toggles read-only plan mode |
| `google_web_search` | DuckDuckGo web search |
| `web_fetch` | Fetches URL content |
| `ask_user` | Asks the user a question |
| `user_response` | Delivers the final response |

---

## 🔑 API Integration

All communication flows through the **Darktech Proxy REST API**:

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/v1/chats/new` | POST | Create a new chat session |
| `/api/v1/chats/{id}` | DELETE | Delete a chat session |
| `/api/v1/chats/{id}/exists` | GET | Check if a chat exists on the server |
| `/api/v1/chats/{id}/chatTitle` | GET | Get chat title |
| `/api/v1/chats/{id}/parent` | GET | Get parent message ID |
| `/api/v1/chats/upload-cache` | POST | Upload cached chat (returns new `chat_id` + `parent_message_id`) |
| `/api/v1/chat/completions` | POST | Send prompt (SSE stream) |
| `/api/v1/chat/completions/stop` | POST | Abort generation |
| `/api/v1/files/upload` | POST | Upload text file |
| `/api/v1/files/upload-image` | POST | Upload image (multipart) |

---

## 🔒 Security

- **Path Traversal Protection** — All file operations are sandboxed to the workspace directory.
- **Atomic Writes** — File edits use temp-file + rename for crash safety.
- **Permission Modes** — Write operations can require explicit user approval.
- **Token Isolation** — Tokens are stored locally and never logged.
- **Input Sanitization** — Rich markup injection is detected and neutralized.

---

## 📁 Configuration Files

| File | Purpose |
|------|---------|
| `config/config.json` | Proxy URL, captcha solver URL |
| `config/tokenfile.json` | Authentication tokens (multi-token support) |
| `chat_cache/*.json` | Per-chat conversation cache |
| `logs/agent.log` | Application log |
| `logs/tool_calls.log` | Structured tool call log (JSON lines) |

---

## 🧩 Key Components

### AgentWrapper (`app/agent_wrapper.py`)
Orchestrates the agent loop: sends prompts, processes SSE streams, executes tools, handles errors/retries, manages token rotation, and communicates with the UI via callbacks.

### Tool Engine (`app/agent/module.py`)
Executes all 17 tools with full validation, path safety, timeout handling, and background process management.

### Edit Engine (`app/agent/edit_file.py`)
A 7-layer safety-first file editing system:
1. Exact match (confidence: 1.0)
2. Line-ending normalized (0.98)
3. Indentation normalized (0.95)
4. Whitespace normalized (0.92)
5. Operator-spacing normalized (0.90)
6. Structural/language-aware (0.88) — extension point
7. Fuzzy candidate discovery (NEVER auto-edits)

### ProxyClient (`app/agent/proxy_client.py`)
HTTP client for all Darktech Proxy API interactions with Bearer token auth, SSE streaming, and error handling. Includes `check_chat_exists(chat_id)` to verify server-side chat existence before switching, and `upload_chat_cache(payload)` to re-upload a cached chat when it no longer exists on the server.

### ChatCache (`app/agent/chat_cache.py`)
Persists conversations to `chat_cache/{chat_id}.json` with incremental message appending, thinking summaries, and token usage tracking. The `/chats` command reads directly from this cache (no server call). When switching to a cached chat via `/change_chat`, the agent first checks if the chat still exists on the server (`/api/v1/chats/{id}/exists`); if not, it auto-uploads the cache and adopts the new `chat_id` and `parent_message_id` returned by the server.

---

## 🧪 Development

### Project Structure Conventions

- **UI Layer**: `app/app.py` + `app/widgets/` — Textual widgets and event handling
- **Agent Layer**: `app/agent_wrapper.py` + `app/agent/` — API communication and tool execution
- **State**: `app/state/` — Application state management
- **Config**: `config/` — Runtime configuration files

### Adding a New Command

1. Add a `Command` entry to `COMMANDS` in `app/command_defs.py`
2. Implement the handler in `MainApp.handle_command()` in `app/app.py`

---

## 📦 Dependencies

| Package | Purpose |
|---------|---------|
| `textual` | Terminal UI framework |
| `requests` | HTTP client for API calls |
| `rich` | Terminal formatting & markup |
| `pygments` | Syntax highlighting |
| `pyinstaller` | Binary compilation (dev only) |

---

## 📄 License

Proprietary — All rights reserved.

---

## 🤝 Contributing

This is a private project. Contact the maintainers for access.

## 📬 Contact

For API tokens, support, or inquiries:
- **Telegram:** [@Warriorx0](https://t.me/Warriorx0)

---

## 📝 Changelog

See git history for detailed changes.

---

<div align="center">

**Built with ❤️ using Textual & Python**

</div>
