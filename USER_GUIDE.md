# DarktechAgent CLI — User Guide

<div align="center">

<img src="images/darktech.png" alt="DarktechAgent Logo" width="200"/>

</div>

Welcome to **DarktechAgent CLI**, your AI-powered coding assistant that lives in the terminal. This guide will walk you through everything you need to know to use the application effectively.

---

## 📋 Table of Contents

1. [Installation & Setup](#installation--setup)
2. [First Launch](#first-launch)
3. [Basic Usage](#basic-usage)
4. [Commands Reference](#commands-reference)
5. [Keyboard Shortcuts](#keyboard-shortcuts)
6. [File Mentions (@)](#file-mentions-)
7. [Permission Modes](#permission-modes)
8. [Thinking Mode](#thinking-mode)
9. [Image Attachments](#image-attachments)
10. [Chat Management](#chat-management)
11. [Token Management](#token-management)
12. [Understanding the UI](#understanding-the-ui)
13. [Troubleshooting](#troubleshooting)

---

## 🚀 Installation & Setup

### Option A: Run from Source

1. Make sure you have **Python 3.10+** installed.
2. Open a terminal and navigate to the project folder.
3. Install dependencies:
   ```bash
   pip install textual requests rich pygments
   ```
4. Run the application:
   ```bash
   python main.py
   ```

### Option B: Run the Compiled Binary

1. Download the `DarktechAgent` executable for your platform.
2. Place it in your desired working directory.
3. Create a `config` folder next to the executable.
4. Run the executable.

### Token Setup

Before using the agent, you need an authentication token.

> 📩 **Getting a Token:** Contact [@Warriorx0](https://t.me/Warriorx0) on Telegram to obtain your API token.

Once you have your token:

1. Create (or edit) the file `config/tokenfile.json`:
   ```json
   {
     "tokens": ["YOUR_TOKEN_HERE"]
   }
   ```
2. Alternatively, add tokens from within the app using:
   ```
   /add_token YOUR_TOKEN_HERE
   ```

---

## 🖥️ First Launch

When you launch DarktechAgent, you'll see:

```
   ▄▄▄
  ▄█████▄
 ▄███████▄
████████
 ██████▀
  ████▀
   ██▀

Welcome to the terminal UI!
Type a message or use /commands (e.g., /clear)
```

The agent initializes automatically. Once ready, the status bar shows **"Ready"** and the input box becomes active.

---

## 💬 Basic Usage

### Sending a Message

Simply type your request and press **Enter**:

```
> Create a Python function that calculates fibonacci numbers
```

The agent will:
1. Analyze your request
2. Use tools to explore your workspace
3. Write code, run commands, and verify results
4. Stream its response back to you in real-time

### Multi-line Input

Press **Ctrl+J** to insert a new line without sending. Press **Enter** to send.

### Canceling a Request

Press **Escape** while the agent is working to cancel the current operation.

---

## 📝 Commands Reference

Type `/` to see autocomplete suggestions. Here's the full list:

### Session Commands

| Command | Description |
|---------|-------------|
| `/new` | Start a new chat session |
| `/clear` | Clear the conversation display |
| `/quit` | Exit the application |
| `/help` | Show available commands |

### Model & Mode

| Command | Description |
|---------|-------------|
| `/model` | Cycle through available models |
| `/model <name>` | Set a specific model (`darktech_v3`, `darktech_v2`, `darktech_v1`) |
| `/mode` | Toggle between `normal` and `thinking` mode |
| `/permission` | Toggle between `auto` and `read-only` permission |

### Chat Management

| Command | Description |
|---------|-------------|
| `/chats` | List all chats from local cache (no server call) |
| `/change_chat <number or ID>` | Switch to a different chat (auto-uploads if not on server) |
| `/current_chat` | Show current chat ID and title |
| `/remove_chat <number or ID>` | Delete a specific chat |
| `/remove_chats` | Delete all chats except the current one |
| `/cached_chats` | List locally cached chats (same as `/chats`) |
| `/upload_chat <index or ID>` | Upload a cached chat to the server manually |

### Token Management

| Command | Description |
|---------|-------------|
| `/tokens` | Show number of available tokens |
| `/add_token <token>` | Add a new token to the pool |
| `/change_token <token>` | Replace the active token |

### Account

| Command | Description |
|---------|-------------|
| `/me` | Display your account info (ID, name, email) |

### Attachments

| Command | Description |
|---------|-------------|
| `/attach <path>` | Attach an image to the next message |
| `/attachments` | List pending attachments |
| `/clear_attachments` | Remove all pending attachments |

### Settings

| Command | Description |
|---------|-------------|
| `/settings` | Display current settings |

---

## ⌨️ Keyboard Shortcuts

| Shortcut | Action |
|----------|--------|
| `Enter` | Send message / Select suggestion |
| `Ctrl+J` | Insert new line (multi-line input) |
| `Escape` | Cancel running agent / Close suggestions |
| `Ctrl+T` | Toggle mode (normal ↔ thinking) |
| `Ctrl+Q` | Toggle permission (auto ↔ read-only) |
| `Ctrl+E` | Exit application |
| `Up/Down` | Navigate suggestion list |
| `Tab` | Select highlighted suggestion |
| `Ctrl+A` | Select all text in input |

---

## 📂 File Mentions (@)

You can reference files and folders in your messages using `@`:

```
> Fix the bug in @src/main.py
> Explain what @config/settings.json does
> Refactor @app/utils/ to use async
```

**How it works:**
- Type `@` to see a list of files/folders in your workspace
- Type `@folder/` to browse inside a directory
- Use arrow keys + Enter/Tab to select from suggestions
- The agent will read and work with the referenced files

---

## 🔐 Permission Modes

### Auto Mode (Default)
All tools execute automatically without asking. Best for trusted workspaces.

### Read-Only Mode
Write operations (file edits, shell commands) require your approval:
- **Allow once** — Execute this one time
- **Allow session** — Allow this tool for the rest of the session
- **Deny** — Block the operation

Toggle with `/permission` or **Ctrl+Q**.

---

## 🧠 Thinking Mode

When thinking mode is active (default), the agent shows its reasoning process:
- You'll see thinking titles in the status bar
- The agent's decision-making is more transparent

Toggle with `/mode` or **Ctrl+T**.

---

## 🖼️ Image Attachments

You can attach images for the agent to analyze:

```
/attach path/to/screenshot.png
```

Then send your message:
```
> What's wrong with this UI? Fix the layout issues.
```

Supported formats: JPG, JPEG, PNG, GIF, BMP, WebP, SVG, ICO, TIFF

Manage attachments:
- `/attachments` — See what's queued
- `/clear_attachments` — Remove all

---

## 💬 Chat Management

### Creating a New Chat
```
/new
```
Starts a fresh conversation. The screen clears and a new session is created.

### Listing Chats
```
/chats
```
Shows all your chats from the local cache with their IDs, titles, and message counts. This is instant — no server call is made.

### Switching Chats
```
/change_chat 3
```
or
```
/change_chat 74f7ff38-fa92-44be-a48a-62c88781ecf1
```

When switching chats, the app checks if the chat still exists on the server:
- **If it exists** → switches immediately using the local cache.
- **If it doesn't exist** → automatically uploads the cached chat to the server, gets a new chat ID, and sets it as the current chat. You'll see a message like:
  ```
  Chat abc123 not found on server. Uploading from cache...
  Chat uploaded successfully. New ID: def456
  ```

### Deleting Chats
```
/remove_chat 2        ← Delete by list number
/remove_chats         ← Delete all except current
```

### Uploading Cached Chats
If you want to manually upload a cached chat to the server (e.g., to sync it):
```
/cached_chats         ← List available caches
/upload_chat 1        ← Upload by index
```

> **Note:** You don't need to manually upload before switching — `/change_chat` handles this automatically when the chat doesn't exist on the server.

---

## 🔑 Token Management

The app supports multiple tokens for automatic rotation when rate limits are hit.

### Adding Tokens
```
/add_token YOUR_NEW_TOKEN
```

### Checking Token Count
```
/tokens
```

### How Rotation Works
When you hit a daily usage limit:
1. The app automatically switches to the next token
2. Your chats are migrated to the new account
3. Your conversation continues seamlessly

> **Tip:** Add multiple tokens for uninterrupted usage. Need more tokens? Contact [@Warriorx0](https://t.me/Warriorx0) on Telegram.

---

## 🖥️ Understanding the UI

### Layout

```
┌─────────────────────────────────────────┐
│  ◆ Ready                    [Header]    │
│                                         │
│  > User messages appear here            │
│  ✦ Agent responses stream here          │
│  ✓ Tool executions shown here           │
│                                         │
├─────────────────────────────────────────┤
│  ⠋ Thinking... (5s) · 1,234 tokens    │ ← Status bar
├─────────────────────────────────────────┤
│  Type your message...       [Input]     │
├─────────────────────────────────────────┤
│  Workspace: . Model: darktech_v3 ...   │ ← Footer
└─────────────────────────────────────────┘
```

### Status Indicators

| Status | Meaning |
|--------|--------|
| **Ready** | Agent is idle, you can type |
| **Thinking...** | Agent is processing your request |
| **Loading...** | Fetching data (chats, user info) |
| **Canceling...** | Cancellation in progress |

### Tool Execution Cards

When the agent uses tools, you'll see cards like:
```
✓ ReadFile  src/main.py
  Lines 1-50 of 120 (50 lines read)
```
```
✓ EditFile  src/main.py [lines 10-15] (exact)
  + def fibonacci(n):
  - def fib(n):
```

---

## 🔧 Troubleshooting

### "Agent not initialized"
- The app failed to connect to the API on startup
- Check your internet connection
- Verify `config/config.json` has the correct proxy URL

### "There is no token"
- Add a token: `/add_token YOUR_TOKEN`
- Or edit `config/tokenfile.json` directly

### "Rate limit reached"
- Add more tokens: `/add_token ANOTHER_TOKEN`
- The app will auto-rotate if multiple tokens exist
- Wait for the daily quota to reset

### Agent seems stuck
- Press **Escape** to cancel
- Try sending your message again
- Check `logs/agent.log` for errors

### Chat not responding
- Use `/new` to start a fresh session
- Try `/change_chat` to switch to another session
- If the chat was deleted server-side, `/change_chat` will auto-upload it from cache and assign a new ID

### Captcha prompt
- The app solves captchas automatically
- You'll see progress messages like "Solving the captcha for you..."
- If it fails, try again after a few seconds

### Input box is disabled
- The agent is processing — wait for it to finish
- If stuck, press **Escape** to cancel

---

## 💡 Tips & Best Practices

1. **Be specific** — Clear instructions produce better results.
2. **Use @ mentions** — Point the agent to relevant files directly.
3. **Start with /new** — Begin complex tasks in a fresh chat.
4. **Use thinking mode** — See the agent's reasoning for complex tasks.
5. **Use read-only mode** — When working on sensitive code, require approval for writes.
6. **Add multiple tokens** — Avoid interruptions from rate limits.
7. **Cancel early** — If the agent goes off-track, press Escape and rephrase.
8. **Attach screenshots** — For UI-related tasks, images help the agent understand context.

---

## ❓ FAQ

**Q: What models are available?**
A: `darktech_v3` (default), `darktech_v2`, and `darktech_v1`. Use `/model` to switch.

**Q: Where are my chats stored?**
A: Chats are cached locally in the `chat_cache/` folder as JSON files. The `/chats` command reads directly from this cache — no server call is needed to list your chats.

**Q: Can I use the agent offline?**
A: No, the agent requires an internet connection to communicate with the API.

**Q: What's the workspace?**
A: The workspace is the directory where you launched the app. All file operations are relative to it.

**Q: How do I change the workspace?**
A: Launch the app from a different directory. The workspace is set to your current working directory on startup.

**Q: Can the agent access files outside the workspace?**
A: No. All file operations are sandboxed to the workspace directory for security.

---

<div align="center">

**Happy coding with DarktechAgent! 🚀**

For API tokens, issues, or questions:
📩 **Telegram:** [@Warriorx0](https://t.me/Warriorx0)

</div>
