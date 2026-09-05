import os
import asyncio
from pathlib import Path
from textual.app import App, ComposeResult
from textual.containers import Container, VerticalScroll
from textual.binding import Binding
from .widgets.header import CustomHeader
from .widgets.conversation import ConversationView
from .widgets.input_box import InputBox
from .widgets.footer import CustomFooter
from .widgets.status import StatusBar
from .widgets.suggestions import SuggestionsList
from .widgets.tool_permission import ToolPermissionWidget
from .widgets import Logo
from .state import AppState
from app.command_defs import COMMANDS, AVAILABLE_MODELS
from .agent_wrapper import AgentWrapper
from app.agent.configData import save_tokenfile, get_token_count, add_token
from app.agent.chat_cache import ChatCache, CACHE_DIR
from app.agent.module import create_client, _BASE
import json
import traceback
from app.agent.logger import Logger
from rich.markup import escape
from rich.text import Text
class MainApp(App):
    BINDINGS = [
        Binding("ctrl+q", "toggle_permission", "Toggle Permission", show=True),
        Binding("ctrl+t", "toggle_mode", "Toggle Mode", show=True),
        Binding("ctrl+e", "quit", "Exit App", show=False),
        Binding("escape", "cancel_agent", "Cancel", show=False),
    ]

    # Commands that call the Darktech API through the agent client and therefore
    # require a loaded token (a token must be configured).
    TOKEN_REQUIRED_COMMANDS = {
        "new", "me",
        "remove_chat", "remove_chats",
        "upload_chat", "attach",
    }
    
    CSS = """
    App {
        background: #0d1117;
    }
    #main_container {
        layout: grid;
        grid-size: 1;
        grid-rows: 1fr auto auto auto;
        height: 100%;
        background: #0d1117;
    }
    #scrollable_area {
        height: 1fr;
        border: solid #30363d;
        background: #0d1117;
        overflow-y: auto;
    }
    #status {
        height: 1;
        background: #0d1117;
        color: #8b949e;
    }
    #input_area {
        layout: vertical;
        height: auto;
        max-height: 22;
        border: solid #30363d;
        padding: 0;
        background: #0d1117;
    }
    #suggestions {
        height: auto;
        max-height: 8;
        background: #161b22;
        border: solid #30363d;
        overflow: auto;
        display: none;
    }
    #suggestions.visible {
        display: block;
    }
    #input_box {
    height: 6;
    padding: 0 1;
    color: #c9d1d9;
    background: #161b22;
    border: solid #30363d;
    }
    #tool_permission {
        height: auto;
        max-height: 6;
        background: #161b22;
        border: solid #30363d;
        padding: 0;
        margin: 0;
        display: none;
    }
    #tool_permission.visible {
        display: block;
    }
    .permission-option {
        height: 1;
        padding: 0 1;
    }
    #logo { align-horizontal: center; margin-bottom: 1; }
    #conversation {
        height: auto;
        overflow-y: hidden;
    }
    #footer {
        height: 1;
        background: #0d1117;
        color: #8b949e;
    }
    """

    def __init__(self):
        super().__init__()
        self.state = AppState()
        self.suggestions = SuggestionsList(id="suggestions")
        self.suggestions.hide()
        self.agent = None
        self.waiting_for_agent_response = False
        self.timer_active = False
        self.thinking_title = ""
        self.last_chats = []  # Store the last fetched chat list for number-based selection
        self.waiting_for_tool_permission = False  # Track when waiting for tool permission
        self.is_canceling = False  # Track when ESC cancellation is in progress
        self._active_tool_widget = None  # Running tool card, finalized when the tool completes
        self._streaming_widget = None  # Widget used for streaming ask_user/user_response text
        self._stream_buffer = ""  # Accumulated streamed text

    def compose(self) -> ComposeResult:
        with Container(id="main_container"):
            with VerticalScroll(id="scrollable_area"):
                yield Logo(id="logo")
                yield CustomHeader(id="header")
                yield ConversationView(id="conversation")
            yield StatusBar(id="status")
            with Container(id="input_area"):
                yield self.suggestions
                yield ToolPermissionWidget(id="tool_permission")
                yield InputBox(id="input_box")
            yield CustomFooter(id="footer")

    def on_mount(self) -> None:
        self._update_window_title("DarktechAgent")
        self.state.set_workspace(os.getcwd())
        self.state.model="darktech_v3"
        conversation = self.query_one("#conversation")
        conversation.write("Welcome to the terminal UI!")
        conversation.write("Type a message or use /commands (e.g., /clear)")
        conversation.write(f"Available commands: {', '.join([cmd.name for cmd in COMMANDS])}")
        self.suggestions.hide()
        
        # Initialize the agent
        self.agent = AgentWrapper(self)
        callbacks = {
            'on_message': self._on_agent_message,
            'on_tool': self._on_agent_tool,
            'on_tool_start': self._on_agent_tool_start,
            'on_status': self._on_agent_status,
            # 'on_question': self._on_agent_question,
            'on_finish': self._on_agent_finish,
            'on_error': self._on_agent_error,
            'on_error_display': self._on_agent_error_display,
            'on_error_warnning': self._on_agent_warnning_display,
            'on_stream_text': self._on_agent_stream_text,
            'on_thinking_title': self._on_thinking_title,
            'on_tool_permission': self._on_tool_permission,
            'on_captcha_progress': self._on_captcha_progress,
        }
        self.agent.register_callbacks(callbacks)
        
        # Set status to Loading while initializing
        status_bar = self.query_one("#status")
        status_bar.start_elapsed_timer("Initializing...")
        self.timer_active = True
        input_box = self.query_one("#input_box")
        input_box.disabled = True
        
        # Initialize agent using asyncio task to avoid blocking the UI
        asyncio.create_task(self._initialize_agent_async(conversation))

    async def _initialize_agent_async(self, conversation) -> None:
        """Async worker function to initialize agent."""
        try:
            success = await asyncio.to_thread(self.agent.initialize)
            if success:
                self._display_init_success(conversation, self.agent.session_id)
            else:
                self._display_init_failure(conversation)
        except Exception as e:
            self._display_init_error(conversation, str(e))

    def _display_init_success(self, conversation, session_id) -> None:
        """Display successful initialization in UI thread."""
        # conversation.write("Agent initialized successfully. Chat will be created on first prompt.")
        self._reset_loading_status()
        # Refresh the footer so it picks up the initialized agent's mode/permission;
        # its first render happened before the agent was fully initialized.
        footer = self.query_one("#footer")
        footer.update(footer.render())
        self.query_one("#input_box").focus()

    def _display_init_failure(self, conversation) -> None:
        """Display initialization failure in UI thread."""
        conversation.write("Agent initialization failed. Check logs.")
        self._reset_loading_status()
        self.query_one("#input_box").focus()

    def _display_init_error(self, conversation, error_msg) -> None:
        """Display initialization error in UI thread."""
        conversation.write(f"Agent initialization error: {error_msg}")
        self._reset_loading_status()
        self.query_one("#input_box").focus()

    def on_input_box_text_changed(self, event: InputBox.TextChanged) -> None:
        text = event.text
        if text.startswith("/"):
            # Don't show suggestions if command is fully typed (exact match) or has args being typed
            parts = text.split()
            if len(parts) > 1:
                # User is typing args after the command, hide suggestions
                self.suggestions.hide()
                self.suggestions.refresh()
            elif any(cmd.name == text for cmd in COMMANDS):
                # Exact command match (e.g., user selected from list or fully typed) - hide
                self.suggestions.hide()
                self.suggestions.refresh()
            else:
                matches = [cmd for cmd in COMMANDS if cmd.name.startswith(text)]
                if matches:
                    self.suggestions.update_items(matches)
                else:
                    self.suggestions.hide()
                    self.suggestions.refresh()
        elif "@" in text:
            # Handle @ mention for file/folder suggestions
            input_box = self.query_one("#input_box")
            input_box._check_at_mention(text)
        else:
            self.suggestions.hide()
            self.suggestions.refresh()

    def on_input_box_message_sent(self, event: InputBox.MessageSent) -> None:
        text = event.text
        if not text.strip():
            return
        conversation = self.query_one("#conversation")
        status = self.query_one("#status")

        # If we are waiting for a response to an agent question, handle it specially
        if self.waiting_for_agent_response and self.agent and self.agent.running:
            # Display the user's response in the conversation
            conversation.write(f"> (response) {text}")
            # Set status to Thinking and disable input while agent processes
            self.set_status_with_input("Thinking...")
            # Provide the response to the agent
            self.waiting_for_agent_response = False
            self.agent.provide_response(text)
            # Do not process as a new prompt
            return
        elif self.waiting_for_agent_response:
            # Agent is not running but waiting flag is set - reset it
            self.waiting_for_agent_response = False
            self.set_status_with_input("Ready")
            # Fall through to treat as normal prompt

        # Normal prompt handling
        conversation.write(f"> {text}")
        self.suggestions.hide()
        self.suggestions.refresh()

        if text.startswith("/"):
            self.handle_command(text, conversation, status)
        else:
            # Start the agent with the user's prompt
            self.start_agent(text)

    def on_input_box_suggestion_selected(self, event: InputBox.SuggestionSelected) -> None:
        input_box = self.query_one("#input_box")
        input_box.apply_suggestion(event.text)
        self.suggestions.hide()
        self.suggestions.refresh()
        input_box.focus()

    def on_suggestions_list_selected(self, event: SuggestionsList.Selected) -> None:
        value = event.value
        input_box = self.query_one("#input_box")
        input_box.apply_suggestion(value)
        self.suggestions.hide()
        self.suggestions.refresh()
        input_box.focus()

    def command_requires_arguments(self, cmd_name: str) -> bool:
        for cmd in COMMANDS:
            if cmd.name == cmd_name:
                return cmd.requires_arguments
        return False

    @staticmethod
    def _set_terminal_title(title: str) -> None:
        """Set the terminal window/tab title using Windows API and ANSI escape."""
        try:
            import ctypes
            from ctypes import wintypes
            # Method 1: SetConsoleTitleW (works on legacy console)
            ctypes.windll.kernel32.SetConsoleTitleW(title)
            # Method 2: Write ANSI OSC sequence directly to console output handle
            # This bypasses Textual's stdout and works on Windows Terminal tabs
            STD_OUTPUT_HANDLE = -11
            handle = ctypes.windll.kernel32.GetStdHandle(STD_OUTPUT_HANDLE)
            if handle and handle != -1:
                sequence = f"\x1b]0;{title}\x07".encode('utf-8')
                written = wintypes.DWORD()
                ctypes.windll.kernel32.WriteConsoleA(
                    handle, sequence, len(sequence), ctypes.byref(written), None
                )
        except Exception:
            pass

    def _update_window_title(self, status_text: str) -> None:
        """Update the Windows terminal tab/window title with current status."""
        base_title = "DarktechAgent"
        if status_text == "Ready":
            title = base_title
        else:
            title = f"{base_title} - {status_text}"
        self._set_terminal_title(title)

    def set_status_with_input(self, status_text: str) -> None:
        """Set status bar text and enable/disable input box accordingly."""
        status = self.query_one("#status")
        input_box = self.query_one("#input_box")
        
        status.set_status(status_text)
        self._update_window_title(status_text)
        # Disable input when not Ready, enable when Ready
        input_box.disabled = (status_text != "Ready")
        # Refresh header to reflect new status
        try:
            header = self.query_one("#header")
            header.refresh()
        except Exception:
            pass

    def start_agent(self, prompt: str) -> None:
        """Start the agent with the given prompt."""
        if self.agent is None:
            conversation = self.query_one("#conversation")
            conversation.write("Agent not initialized.")
            return
        if self.agent.running:
            conversation = self.query_one("#conversation")
            conversation.write("Agent is already running.")
            return
        # Clear previous tokens and disable input
        status_bar = self.query_one("#status")
        status_bar.clear_tokens()
        self.set_status_with_input("Thinking...")
        # Run agent in background thread
        self.agent.run(prompt)

    def handle_command(self, command: str, conversation, status) -> None:
        parts = command[1:].strip().split()
        if not parts:
            return
        cmd = parts[0].lower()

        # Commands that call the Darktech API need a token loaded in the client.
        if cmd in self.TOKEN_REQUIRED_COMMANDS:
            if self.agent is None or self.agent.client is None:
                conversation.write("Agent not initialized.")
                self.set_status_with_input("Ready")
                return
            from app.agent.configData import get_active_token
            if get_active_token() is None:
                conversation.write(
                    "[yellow]Warning: There is no token. Add a token with `/add_token <token>`.[/yellow]"
                )
                self.set_status_with_input("Ready")
                return

        if cmd == "clear":
            conversation.clear()
            self.set_status_with_input("Ready")
        elif cmd == "help":
            conversation.write(f"Available commands: {', '.join([c.name for c in COMMANDS])}")
            self.set_status_with_input("Ready")
        elif cmd == "new":
            self.handle_new_command(conversation)
        elif cmd == "settings":
            settings_str = ", ".join(f"{k}={v}" for k, v in self.state.settings.items()) if self.state.settings else "No settings configured."
            conversation.write(f"Settings: {settings_str}")
            self.set_status_with_input("Ready")
        elif cmd == "model":
            available_models = AVAILABLE_MODELS
            if len(parts) == 1:
                # Cycle through available models
                current = self.state.model
                try:
                    current_index = available_models.index(current)
                except ValueError:
                    current_index = 0
                new_model = available_models[(current_index + 1) % len(available_models)]
            else:
                # Explicit model name provided
                requested = parts[1]
                if requested not in available_models:
                    conversation.write(
                        f"[yellow]Unknown model '{requested}'. Available: {', '.join(available_models)}[/yellow]"
                    )
                    self.set_status_with_input("Ready")
                    return
                new_model = requested

            self.state.set_model(new_model)
            if self.agent and self.agent.client:
                self.agent.update_model(new_model)
            conversation.write(f"Model changed to: {new_model}")
            footer = self.query_one("#footer")
            footer.update(footer.render())
            self.set_status_with_input("Ready")
        elif cmd == "quit":
            self.exit()
        elif cmd == "chats":
            self.handle_chats_command(conversation)
        elif cmd == "change_chat":
            if len(parts) < 2:
                conversation.write("Usage: /change_chat <chat_id>")
                self.set_status_with_input("Ready")
            else:
                chat_id = parts[1]
                self.handle_change_chat_command(conversation, chat_id)
        elif cmd == "permission":
            self.handle_permission_command(conversation)
            self.set_status_with_input("Ready")
        elif cmd == "change_token":
            if len(parts) < 2:
                conversation.write("Usage: /change_token <token>")
                self.set_status_with_input("Ready")
            else:
                token = parts[1]
                self.handle_change_token_command(conversation, token)
        elif cmd == "me":
            self.handle_me_command(conversation)
        elif cmd == "remove_chat":
            if len(parts) < 2:
                conversation.write("Usage: /remove_chat <chat_id or index>")
                self.set_status_with_input("Ready")
            else:
                chat_identifier = parts[1]
                self.handle_remove_chat_command(conversation, chat_identifier)
        elif cmd == "remove_chats":
            self.handle_remove_chats_command(conversation)
        elif cmd == "mode":
            self.handle_mode_command(conversation)
        elif cmd == "current_chat":
            self.handle_current_chat_command(conversation)
        elif cmd == "upload_chat":
            if len(parts) < 2:
                conversation.write("Usage: /upload_chat <index or chat_id>")
                self.set_status_with_input("Ready")
            else:
                chat_arg = parts[1]
                self.handle_upload_chat_command(conversation, chat_arg)
        elif cmd == "attach":
            if len(parts) < 2:
                conversation.write("Usage: /attach <image_path>")
                self.set_status_with_input("Ready")
            else:
                image_path = " ".join(parts[1:])
                self.handle_attach_command(conversation, image_path)
                # handle_attach_command manages its own status lifecycle
        elif cmd == "attachments":
            self.handle_attachments_command(conversation)
            self.set_status_with_input("Ready")
        elif cmd == "clear_attachments":
            self.handle_clear_attachments_command(conversation)
            self.set_status_with_input("Ready")
        elif cmd == "tokens":
            count = get_token_count()
            conversation.write(f"Available tokens: {count}")
            self.set_status_with_input("Ready")
        elif cmd == "add_token":
            if len(parts) < 2:
                conversation.write("Usage: /add_token <token>")
            else:
                token = parts[1]
                if add_token(token):
                    count = get_token_count()
                    conversation.write(f"Token added successfully. Total tokens: {count}")
                else:
                    conversation.write("[red]Failed to save token.[/red]")
            self.set_status_with_input("Ready")
        elif cmd == "cached_chats":
            self.handle_cached_chats_command(conversation)
            self.set_status_with_input("Ready")

        else:
            conversation.write(f"Unknown command: {command}")
            self.set_status_with_input("Ready")

        self.query_one("#input_box").focus()

    # Callback methods for agent
    def _on_agent_message(self, message: str) -> None:
        """Called when agent sends a message."""
        conversation = self.query_one("#conversation")
        conversation.write(f"\u2726 {message}")
        self.call_after_refresh(self._scroll_to_bottom)

    def _on_agent_stream_text(self, char: str) -> None:
        """Called incrementally as ask_user/user_response text streams in."""
        if self.is_canceling:
            return
        conversation = self.query_one("#conversation")
        if self._streaming_widget is None:
            # Start a new streaming widget
            self._stream_buffer = ""
            self._streaming_widget = conversation.write_stream_start()
        self._stream_buffer += char
        conversation.write_stream_update(self._streaming_widget, self._stream_buffer)
        self.call_after_refresh(self._scroll_to_bottom)

    # Friendly display names for tools in the conversation view.
    _TOOL_DISPLAY_NAMES = {
        "read_file": "ReadFile",
        "list_directory": "ListDir",
        "replace": "EditFile",
        "write_file": "WriteFile",
        "run_shell_command": "RunCommand",
        "current_path": "CurrentPath",
        "glob": "Glob",
        "grep_search": "GrepSearch",
        "google_web_search": "WebSearch",
        "web_fetch": "WebFetch",
        "enter_plan_mode": "PlanMode",
        "list_background_processes": "ListBgProcs",
        "read_background_output": "ReadBgOutput",
        "kill_process": "KillProcess",
        "code_interpreter": "CodeInterpreter",
        "ask_user": "AskUser",
        "user_response": "UserResponse",
    }

    def _tool_display(self, tool: str) -> str:
        return self._TOOL_DISPLAY_NAMES.get(tool, tool)

    def _tool_arg_summary(self, tool: str, args: dict) -> str:
        """Return a short human-readable summary of a tool's arguments."""
        if tool == "code_interpreter":
            code = args.get('code', '')
            code_lines = code.split('\n')
            if len(code_lines) > 8:
                return '\n'.join(code_lines[:8]) + '\n...'
            return code
        if tool == "run_shell_command":
            return args.get('command', '')
        if tool == "grep_search":
            pattern = args.get('pattern', '')
            include = args.get('include', '')
            return f"pattern='{pattern}'" + (f" include={include}" if include else "")
        if tool == "glob":
            pattern = args.get('pattern', '')
            search_path = args.get('path', '.')
            return f"pattern='{pattern}' in {search_path}"
        if tool == "google_web_search":
            return args.get('query', '')
        if tool == "web_fetch":
            return args.get('url', '')
        if tool in ("read_background_output", "kill_process"):
            return f"id={args.get('id', '')}"
        if tool == "enter_plan_mode":
            return f"plan={args.get('plan', True)}"
        if tool == "read_file":
            path = args.get('path', '')
            start_line = args.get('start_line')
            end_line = args.get('end_line')
            if start_line is not None and end_line is not None:
                return f"{path} [lines {start_line}-{end_line}]"
            if start_line is not None:
                return f"{path} [line {start_line}+]"
            if end_line is not None:
                return f"{path} [lines 1-{end_line}]"
            return path
        return args.get('path', '')

    def _on_agent_tool_start(self, tool_data: dict) -> None:
        """Called when the agent is about to execute a tool (renders a running card)."""
        tool = tool_data.get('tool', 'unknown')
        args = tool_data.get('arguments', {})
        display = self._tool_display(tool)
        path = self._tool_arg_summary(tool, args)
        conversation = self.query_one("#conversation")

        label = Text(display, style="bold #58a6ff")
        if path:
            label.append("  ")
            label.append(str(path), style="dim #8b949e")

        self._active_tool_widget = conversation.write_tool_running(label)
        self.call_after_refresh(self._scroll_to_bottom)

    def _on_agent_tool(self, tool_data: dict) -> None:
        """Called when a tool finishes executing; finalizes the running card in place."""
        tool = tool_data.get('tool', 'unknown')
        args = tool_data.get('arguments', {})
        denied = tool_data.get('denied', False)
        status = tool_data.get('status', 'success')
        display = self._tool_display(tool)
        path = self._tool_arg_summary(tool, args)

        # Show different indicator based on execution status
        if denied:
            check = "[red]\u2717[/red]"
            state = "denied"
        elif status == 'success':
            check = "[green]\u2713[/green]"
            state = "success"
        else:
            check = "[red]\u2717[/red]"
            state = "error"
        
        # Build the header line for the tool card
        if path:
            header = f"{check} [bold #58a6ff]{escape(display)}[/] [dim]{escape(str(path))}[/]"
        else:
            header = f"{check} [bold #58a6ff]{escape(display)}[/]"
        if denied:
            header += " [red](denied)[/red]"
        lines = [header]

        # Display result details for read_file and edit operations
        result_data = tool_data.get('result', {})
        if status == 'success' and result_data:
            if tool == 'read_file':
                start_line = result_data.get('start_line', 1)
                end_line = result_data.get('end_line', 0)
                total_lines = result_data.get('total_lines', 0)
                truncated = result_data.get('truncated', False)
                lines_read = end_line - start_line + 1 if end_line >= start_line else 0
                trunc_note = " [yellow](truncated)[/]" if truncated else ""
                lines.append(f"  [dim cyan]Lines {start_line}-{end_line} of {total_lines} ({lines_read} lines read){trunc_note}[/]")
            elif tool == 'replace':
                diff_preview = result_data.get('diff_preview', '')
                lines_added = result_data.get('lines_added', 0)
                lines_removed = result_data.get('lines_removed', 0)
                match_type = result_data.get('match_type', '')
                start_line = result_data.get('start_line')
                end_line = result_data.get('end_line')
                if diff_preview:
                    if start_line is not None and end_line is not None:
                        lines.append(f"  [dim cyan]Edit at lines {start_line}-{end_line} ({match_type})[/]")
                    else:
                        lines.append(f"  [dim cyan]Edit applied ({match_type})[/]")
                    diff_lines = diff_preview.split('\n')
                    max_diff_lines = 15
                    for dl in diff_lines[:max_diff_lines]:
                        if dl.startswith('+'):
                            lines.append(f"  [green]{escape(dl)}[/]")
                        elif dl.startswith('-'):
                            lines.append(f"  [red]{escape(dl)}[/]")
                        else:
                            lines.append(f"  [dim]{escape(dl)}[/]")
                    if len(diff_lines) > max_diff_lines:
                        lines.append(f"  [dim]... ({len(diff_lines) - max_diff_lines} more diff lines)[/]")
                else:
                    summary = "  [dim cyan]Replaced"
                    if lines_added:
                        summary += f" +{lines_added}"
                    if lines_removed:
                        summary += f" -{lines_removed}"
                    if start_line is not None:
                        el = end_line if end_line is not None else start_line
                        summary += f" (lines {start_line}-{el})"
                    summary += f" ({match_type})"
                    lines.append(summary + "[/]")

        conversation = self.query_one("#conversation")
        widget = getattr(self, '_active_tool_widget', None)
        if widget is not None:
            conversation.finalize_tool(widget, lines, state)
            self._active_tool_widget = None
        else:
            # Fallback: no running card (start callback skipped) -> write normally
            for ln in lines:
                conversation.write(ln)

        self.call_after_refresh(self._scroll_to_bottom)

    def _on_agent_status(self, data) -> None:
        """Called when agent status changes.
        
        Args:
            data: Either a string (legacy) or dict with 'status' and optional 'tokens' keys.
        """
        # Support both legacy string format and new dict format
        if isinstance(data, dict):
            status = data.get('status', '')
            tokens = data.get('tokens')
        else:
            status = data
            tokens = None
        
        status_bar = self.query_one("#status")
        
        # Update tokens if provided (without triggering status changes)
        if tokens is not None:
            status_bar.set_tokens(tokens)
        
        # Only process status transitions if a status value was explicitly provided
        if not status:
            return
        
        # If we're in the middle of cancellation, ignore status updates until we're done
        if self.is_canceling:
            return
        
        # If the agent is not running, ignore status updates (agent has finished or been cancelled).
        # This prevents stale callbacks queued before cancellation from overriding the UI state.
        # The action_cancel_agent() or _on_agent_finish() methods handle resetting to Ready.
        if self.agent is not None and not self.agent.running:
            return
        
        if status == 'running':
            if not self.timer_active:
                status_bar.start_elapsed_timer("Thinking...")
                self.timer_active = True
        elif status == 'waiting':
            self.timer_active = False
            self.set_status_with_input("Ready")
            input_box = self.query_one("#input_box")
            input_box.focus()
        elif status == 'finished':
            self.timer_active = False
            self.set_status_with_input("Ready")
        elif status:
            status_bar.set_status(status)

    # def _on_agent_question(self, question: str) -> None:
    #     """Called when agent asks a question."""
    #     conversation = self.query_one("#conversation")
    #     conversation.write(f"[white]Agent asks: {question}[/white]")
    #     self.waiting_for_agent_response = True
    #     input_box = self.query_one("#input_box")
    #     input_box.disabled = False
    #     input_box.focus()
    #     self.call_after_refresh(self._scroll_to_bottom)

    def _on_agent_finish(self, message: str) -> None:
        """Called when agent finishes."""
        conversation = self.query_one("#conversation")
        if self._streaming_widget is not None:
            # Finalize the streaming widget with the green finish style
            conversation.write_stream_end(self._streaming_widget, self._stream_buffer)
            self._streaming_widget = None
            self._stream_buffer = ""
        elif message:
            conversation.write(f"[#88d8b0]✦ {escape(message)}[/#88d8b0]")
        # Reset states
        self.waiting_for_agent_response = False
        self.timer_active = False
        self.thinking_title = ""
        self.set_status_with_input("Ready")
        self.call_after_refresh(self._scroll_to_bottom)
        # Ensure input is focused
        input_box = self.query_one("#input_box")
        input_box.focus()

    def _on_agent_error(self, error: str) -> None:
        """Called when agent encounters an error."""
        conversation = self.query_one("#conversation")
        if self._streaming_widget is not None:
            conversation.write_stream_end(self._streaming_widget, self._stream_buffer)
            self._streaming_widget = None
            self._stream_buffer = ""
        conversation.write(f"[red]Error: {escape(error)}[/red]")
        if error=='The chat is in progress!':
            return
        # Reset states
        self.waiting_for_agent_response = False
        self.timer_active = False
        self.thinking_title = ""
        self.set_status_with_input("Ready")
        self.call_after_refresh(self._scroll_to_bottom)
        input_box = self.query_one("#input_box")
        input_box.focus()

    def _on_agent_error_display(self, error: str) -> None:
        """Called when agent needs to display an error without resetting state or enabling input.
        
        Used for transient errors like stream timeouts (where the agent will retry)
        and quota_limit errors (where the agent has stopped but input should remain disabled).
        """
        conversation = self.query_one("#conversation")
        conversation.write(f"[red]Error: {escape(error)}[/red]")
        self.call_after_refresh(self._scroll_to_bottom)
    def _on_captcha_progress(self, message: str) -> None:
        """Called while the captcha solver is working; shows friendly progress lines."""
        if self.is_canceling:
            return
        conversation = self.query_one("#conversation")
        conversation.write(f"[bold #d29922]\u25B8[/bold #d29922] [italic]{escape(message)}[/italic]")
        self.call_after_refresh(self._scroll_to_bottom)

    def _on_agent_warnning_display(self, error: str) -> None:
        """Called when agent needs to display a warning in the conversation.

        Used for transient warnings like stream timeouts (where the agent will retry)
        and other non-fatal notices. The warning is written to the conversation
        without resetting the status bar, so the spinner and elapsed timer keep
        running while the agent retries.
        """
        if self.is_canceling:
            return
        conversation = self.query_one("#conversation")
        conversation.write(f"[yellow]Warning: {escape(error)}[/yellow]")
        self.call_after_refresh(self._scroll_to_bottom)
        # If the agent already stopped (e.g. missing token), reset the UI so the
        # input box becomes usable again.
        if self.agent is None or not self.agent.running:
            self.waiting_for_agent_response = False
            self.timer_active = False
            self.thinking_title = ""
            self.set_status_with_input("Ready")
            input_box = self.query_one("#input_box")
            input_box.focus()

    def _on_thinking_title(self, title: str) -> None:
        """Called when thinking title is updated in thinking mode."""
        # Ignore thinking title updates if we are currently canceling
        if self.is_canceling:
            return
        self.thinking_title = title
        # Update the status bar to show the thinking title without resetting the timer
        if self.timer_active:
            status_bar = self.query_one("#status")
            status_text = f"Thinking: {title}"
            # Update just the text, preserving the elapsed seconds
            status_bar.update_status_text(status_text)
            self._update_window_title(status_text)

    def _on_tool_permission(self, tool_data: dict) -> None:
        """Called when agent needs permission to execute a tool."""
        tool = tool_data.get('tool', 'unknown')
        args = tool_data.get('arguments', {})

        display = self._tool_display(tool)
        arg_summary = self._tool_arg_summary(tool, args)

        # Show the permission widget with tool info (no conversation writes)
        permission_widget = self.query_one("#tool_permission")
        permission_widget.set_tool_info(display, arg_summary)
        permission_widget.add_class("visible")
        permission_widget.reset()

        # Disable input box so it doesn't capture keyboard events
        input_box = self.query_one("#input_box")
        input_box.disabled = True

        # Focus the permission widget
        permission_widget.focus()

        self.waiting_for_tool_permission = True

    def on_tool_permission_widget_selected(self, message: ToolPermissionWidget.Selected) -> None:
        """Handle permission option selection."""
        # Hide the permission widget immediately (no conversation writes)
        permission_widget = self.query_one("#tool_permission")
        permission_widget.remove_class("visible")

        # Provide the permission decision to the agent.
        # Status is already "Thinking..." and input is already disabled from
        # when the agent started, so skip set_status_with_input to avoid
        # redundant UI updates (window title, header refresh) that cause jank.
        self.waiting_for_tool_permission = False
        self.agent.provide_tool_permission(message.value)

    def handle_chats_command(self, conversation) -> None:
        """Handle the /chats command to list all cached chats."""
        if not CACHE_DIR.exists():
            conversation.write("No chats found.")
            self.last_chats = []
            self.set_status_with_input("Ready")
            return

        cache_files = sorted(CACHE_DIR.glob("*.json"))
        if not cache_files:
            conversation.write("No chats found.")
            self.last_chats = []
            self.set_status_with_input("Ready")
            return

        # Build chat list from cache files
        chats = []
        for cache_file in cache_files:
            try:
                payload = json.loads(cache_file.read_text(encoding='utf-8'))
                chat_id = payload.get('chat_id', cache_file.stem)
                title = payload.get('title') or 'Untitled'
                msg_count = len(payload.get('messages', []))
                chats.append({'id': chat_id, 'title': title, 'messages': msg_count})
            except (json.JSONDecodeError, IOError):
                continue

        if not chats:
            conversation.write("No chats found.")
            self.last_chats = []
        else:
            # Store the chat list for number-based selection
            self.last_chats = chats
            conversation.write(f"Found {len(chats)} chat(s):")
            conversation.write("")
            for i, chat in enumerate(chats, 1):
                chat_id = chat.get('id', 'N/A')
                title = chat.get('title', 'Untitled')
                msg_count = chat.get('messages', 0)
                conversation.write(f"[bold cyan]{i}.[/bold cyan] [yellow]ID:[/yellow] [bold white]{chat_id}[/bold white]")
                conversation.write(f"   [dim]Title:[/dim] {title}  [dim]Messages:[/dim] {msg_count}")
                conversation.write("")
            conversation.write("[dim]Use /change_chat <number or ID> to switch to a chat[/dim]")
        self.set_status_with_input("Ready")
        self.query_one("#input_box").focus()

    def handle_change_chat_command(self, conversation, chat_id: str) -> None:
        """Handle the /change_chat command to switch to a different chat."""
        if self.agent is None or self.agent.client is None:
            conversation.write("Agent not initialized.")
            return
        
        # Check if chat_id is a number (for selection by index)
        if chat_id.isdigit():
            index = int(chat_id)
            if not self.last_chats:
                conversation.write("No chats loaded. Please run /chats first.")
                self.set_status_with_input("Ready")
                return
            if index < 1 or index > len(self.last_chats):
                conversation.write(f"Invalid number. Please enter a number between 1 and {len(self.last_chats)}.")
                self.set_status_with_input("Ready")
                return
            # Get the actual chat ID from the stored list
            chat_id = self.last_chats[index - 1].get('id')
            if not chat_id:
                conversation.write("Invalid chat data.")
                self.set_status_with_input("Ready")
                return
        
        # Set status to Loading with timer while changing chat (like Thinking)
        if not self.timer_active:
            status_bar = self.query_one("#status")
            status_bar.start_elapsed_timer("Loading...")
            self.timer_active = True
        input_box = self.query_one("#input_box")
        input_box.disabled = True
        
        # Run change_chat using asyncio task to avoid blocking the UI
        asyncio.create_task(self._change_chat_async(conversation, chat_id))

    async def _change_chat_async(self, conversation, chat_id: str) -> None:
        """Async worker function to change chat using local cache.

        Checks if the chat exists on the server first. If not, uploads the
        cached chat and uses the returned chat_id and parent_message_id.
        """
        try:
            # Check if the chat exists on the server
            exists = await asyncio.to_thread(self.agent.client.check_chat_exists, chat_id)

            if exists:
                # Chat exists on server - use cache directly
                self.agent.session_id = chat_id

                # Load or create cache for the target chat
                self.agent.chat_cache = ChatCache(
                    chat_id=chat_id,
                    model_name=self.agent.model,
                    thinking_mode=(self.agent.mode == "thinking"),
                )

                # Derive parent_message_id from the last cached message
                messages = self.agent.chat_cache.messages
                parent_message_id = None
                if messages:
                    parent_message_id = messages[-1].get("message_id")
                self.agent.parent_message_id = parent_message_id

                # Get title from cache
                title = self.agent.chat_cache.title or "Untitled"

                self._display_change_chat_result(conversation, chat_id, parent_message_id, title)
            else:
                # Chat does not exist on server - upload it from cache
                conversation.write(f"[yellow]Chat {chat_id} not found on server. Uploading from cache...[/yellow]")

                cache_file = CACHE_DIR / f"{chat_id}.json"
                if not cache_file.exists():
                    self._display_change_chat_error(conversation, f"Cache file not found for chat {chat_id}")
                    return

                try:
                    payload = json.loads(cache_file.read_text(encoding='utf-8'))
                except (json.JSONDecodeError, IOError) as e:
                    self._display_change_chat_error(conversation, f"Could not read cache file: {e}")
                    return

                if not isinstance(payload, dict) or not payload.get('chat_id') or not payload.get('messages'):
                    self._display_change_chat_error(conversation, "Invalid cache format.")
                    return

                # Normalize and upload
                normalized = self.agent._normalize_cache_payload(payload)
                result = await asyncio.to_thread(self.agent.client.upload_chat_cache, normalized)

                new_chat_id = result.get('chat_id') if isinstance(result, dict) else None
                new_parent_message_id = result.get('parent_message_id') if isinstance(result, dict) else None

                if not new_chat_id:
                    self._display_change_chat_error(conversation, f"Upload failed: {result}")
                    return

                # Update cache file with new chat_id and parent_message_id
                new_path = CACHE_DIR / f"{new_chat_id}.json"
                payload['chat_id'] = new_chat_id
                if new_parent_message_id and payload.get('messages'):
                    payload['messages'][-1]['message_id'] = new_parent_message_id
                new_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding='utf-8')
                # Remove old cache file if different
                if cache_file != new_path and cache_file.exists():
                    cache_file.unlink()

                # Set as current chat
                self.agent.session_id = new_chat_id
                self.agent.parent_message_id = new_parent_message_id
                self.agent.previous_message_id = new_parent_message_id

                # Load cache for the new chat
                self.agent.chat_cache = ChatCache(
                    chat_id=new_chat_id,
                    model_name=self.agent.model,
                    thinking_mode=(self.agent.mode == "thinking"),
                )

                title = payload.get('title') or "Untitled"
                conversation.write(f"[green]Chat uploaded successfully. New ID: {new_chat_id}[/green]")
                self._display_change_chat_result(conversation, new_chat_id, new_parent_message_id, title)

        except Exception as e:
            try:
                from pathlib import Path as _Path
                _log_dir = _Path(__file__).parent.parent / "logs"
                _log_dir.mkdir(parents=True, exist_ok=True)
                with open(_log_dir / "error.log", 'w', encoding='utf-8') as file:
                    file.write(traceback.format_exc())
            except Exception as log_error:
                import sys
                print(f"Failed to write error log: {log_error}", file=sys.stderr)
            self._display_change_chat_error(conversation, str(e))

    def _display_change_chat_result(self, conversation, chat_id: str, parent_message_id: str, title: str) -> None:
        """Display change chat result in the UI thread."""
        conversation.write(f"[green]Changed to chat: {chat_id}[/green]")
        conversation.write(f"  [dim]Title:[/dim]             {title}")
        if parent_message_id:
            conversation.write(f"  [dim]Parent Message ID:[/dim] {parent_message_id}")
        else:
            conversation.write(f"  [dim]Parent Message ID:[/dim] N/A")
        self._reset_loading_status()
        # Focus the input box after completion
        self.query_one("#input_box").focus()

    def _display_change_chat_error(self, conversation, error_msg: str) -> None:
        """Display change chat error in the UI thread."""
        conversation.write(f"Error changing chat: {error_msg}")
        self._reset_loading_status()
        # Focus the input box after completion
        self.query_one("#input_box").focus()

    def _reset_loading_status(self) -> None:
        """Reset the loading status back to Ready."""
        self.timer_active = False
        self.set_status_with_input("Ready")

    

    def action_toggle_mode(self) -> None:
        """Toggle mode via Ctrl+T shortcut."""
        if self.agent is None:
            return
        new_mode = self.agent.toggle_mode()
        conversation = self.query_one("#conversation")
        conversation.write(f"[dim]Mode toggled to: '{new_mode}'[/dim]")
        # Force footer to update with new mode
        footer = self.query_one("#footer")
        footer.update(footer.render())
        self.call_after_refresh(self._scroll_to_bottom)

    def action_toggle_permission(self) -> None:
        """Toggle permission mode via Ctrl+Q shortcut."""
        if self.agent is None:
            return
        
        # Toggle the permission mode
        current_mode = self.agent.permission_mode
        new_mode = "auto" if current_mode == "read-only" else "read-only"
        self.agent.permission_mode = new_mode
        
        # conversation = self.query_one("#conversation")
        # conversation.write(f"[dim]Permission mode toggled: '{current_mode}' \u2192 '{new_mode}'[/dim]")
        
        # Refresh the footer to show the new permission mode
        footer = self.query_one("#footer")
        footer.update(footer.render())
        self.call_after_refresh(self._scroll_to_bottom)

    def action_cancel_agent(self) -> None:
        """Cancel the running agent via ESC key.
        
        - If agent is running: stop the current API completion on the server,
          cancel the asyncio task, write a cancellation notice to the conversation,
          reset status to Ready, and enable the input box.
        - If agent is not running: no-op.
        - If suggestions are visible, do nothing (let InputBox handle ESC for those).
        """
        # If suggestions are visible, don't cancel agent - let InputBox handle ESC
        if self.suggestions.is_visible:
            return

        if self.agent is None or not self.agent.running:
            return

        # Stop the elapsed timer so the status bar doesn't show "(Xs)" during cancellation
        status_bar = self.query_one("#status")
        status_bar.stop_elapsed_timer()
        self.timer_active = False

        # Directly set the status bar text and force a refresh
        # This bypasses set_status() to avoid any guard logic issues
        status_bar.status_text = "Canceling....."
        status_bar.elapsed_active = False
        if not status_bar.spinner_active:
            status_bar.spinner_active = True
            status_bar.timer = status_bar.set_interval(0.1, status_bar.advance_spinner)
        status_bar.refresh(repaint=True)
        
        # Update window title
        self._update_window_title("Canceling.....")

        # Set canceling flag to block any subsequent agent callbacks
        self.is_canceling = True

        conversation = self.query_one("#conversation")

        # 1. Stop the current API completion on the server (run in background thread to avoid UI freeze)
        if self.agent.client and self.agent.session_id and self.agent.parent_message_id:
            session_id = self.agent.session_id
            prev_msg_id = self.agent.previous_message_id
            parent_msg_id = self.agent.parent_message_id
            client = self.agent.client
            asyncio.create_task(self._stop_completion_and_reset_canceling(client, session_id, prev_msg_id, parent_msg_id))

        # 2. Cancel the asyncio task (triggers finally block which sets running=False)
        self.agent.cancel()

        # 3. Hide the tool permission widget if visible
        try:
            permission_widget = self.query_one("#tool_permission")
            permission_widget.remove_class("visible")
        except Exception:
            pass

        # 4. Write cancellation notice to the conversation
        conversation.write("[bold #ff9966]\u2718 Request canceled by user[/bold #ff9966]")

        # 5. Reset all agent-related state
        self.waiting_for_agent_response = False
        self.waiting_for_tool_permission = False
        self.timer_active = False
        self.thinking_title = ""
        # Clean up any in-progress streaming widget
        if self._streaming_widget is not None:
            conversation.write_stream_end(self._streaming_widget, self._stream_buffer)
            self._streaming_widget = None
            self._stream_buffer = ""

        # 6. Reset status to Ready (is_canceling is cleared by _stop_completion_and_reset_canceling)
        self.set_status_with_input("Ready")

        # 7. Focus input box
        input_box = self.query_one("#input_box")
        input_box.focus()

        self.call_after_refresh(self._scroll_to_bottom)

    def handle_permission_command(self, conversation) -> None:
        """Handle the /permission command to toggle between auto and read-only permission modes."""
        if self.agent is None:
            conversation.write("Agent not initialized.")
            return
        
        # Toggle the permission mode
        current_mode = self.agent.permission_mode
        new_mode = "auto" if current_mode == "read-only" else "read-only"
        self.agent.permission_mode = new_mode
        
        conversation.write(f"Permission mode changed from '{current_mode}' to '{new_mode}'")
        
        # Refresh the footer to show the new permission mode
        footer = self.query_one("#footer")
        footer.update(footer.render())

    def handle_me_command(self, conversation) -> None:
        """Handle the /Me command to display current user info."""
        if self.agent is None or self.agent.client is None:
            conversation.write("Agent not initialized.")
            self.set_status_with_input("Ready")
            return

        # Set status to Loading while fetching
        if not self.timer_active:
            status_bar = self.query_one("#status")
            status_bar.start_elapsed_timer("Loading...")
            self.timer_active = True
        input_box = self.query_one("#input_box")
        input_box.disabled = True

        asyncio.create_task(self._fetch_and_display_me_async(conversation))

    async def _fetch_and_display_me_async(self, conversation) -> None:
        """Async worker to fetch user info via Me()."""
        try:
            result = await asyncio.to_thread(self.agent.client.Me)
            self._display_me_result(conversation, result)
        except Exception as e:
            self._display_me_error(conversation, str(e))

    def _display_me_result(self, conversation, data) -> None:
        """Display user info extracted from Me() response."""
        if not isinstance(data, dict):
            conversation.write("Unexpected response format from Me().")
            self._reset_loading_status()
            self.query_one("#input_box").focus()
            return

        user_id = data.get('id', 'N/A')
        name = data.get('name', 'N/A')
        email = data.get('email', 'N/A')

        conversation.write("[bold cyan]User Info:[/bold cyan]")
        conversation.write(f"  [dim]ID:[/dim]    {user_id}")
        conversation.write(f"  [dim]Name:[/dim]  {name}")
        conversation.write(f"  [dim]Email:[/dim] {email}")

        self._reset_loading_status()
        self.query_one("#input_box").focus()

    def _display_me_error(self, conversation, error_msg: str) -> None:
        """Display error for /Me command."""
        conversation.write(f"Error fetching user info: {error_msg}")
        self._reset_loading_status()
        self.query_one("#input_box").focus()

    def handle_change_token_command(self, conversation, token: str) -> None:
        """Handle the /change_token command to update token in the token file and recreate client.

        The new token file format uses a 'tokens' list (for multi-token rotation) instead of a
        single 'token' field.  This handler sets the provided token as the sole active
                token while preserving all other token file fields (e.g. darktech-theme).
        """
        try:
                        # Preserve existing token file fields (darktech-theme, etc.) but switch to tokens list
            from app.agent.configData import load_tokenfile
            existing = load_tokenfile()
            token_data = {k: v for k, v in existing.items() if k not in ('token', 'tokens')}
            token_data['tokens'] = [token]

            # Save token file to JSON
            if save_tokenfile(token_data):
                conversation.write("Token saved to tokenfile.json (tokens list updated)")
            else:
                conversation.write("Failed to save token file.")
                self.set_status_with_input("Ready")
                return

            # Recreate client with new token
            if self.agent:
                self.agent.client = create_client()
                self.agent.session_id = None
                self.agent.parent_message_id = None
                conversation.write("Client recreated with new token. New chat session will be created on next message.")
            else:
                conversation.write("Agent not initialized. Token saved but client not recreated.")

            self.set_status_with_input("Ready")
        except Exception as e:
            conversation.write(f"Error changing token: {e}")
            self.set_status_with_input("Ready")

    def handle_remove_chat_command(self, conversation, chat_identifier: str) -> None:
        """Handle the /remove_chat command to delete a specific chat by ID or index."""
        if self.agent is None or self.agent.client is None:
            conversation.write("Agent not initialized.")
            self.set_status_with_input("Ready")
            return

        # Resolve identifier to actual chat_id
        target_chat_id = chat_identifier
        if chat_identifier.isdigit():
            index = int(chat_identifier)
            if not self.last_chats:
                conversation.write("No chats loaded. Please run /chats first.")
                self.set_status_with_input("Ready")
                return
            if index < 1 or index > len(self.last_chats):
                conversation.write(f"Invalid number. Please enter a number between 1 and {len(self.last_chats)}.")
                self.set_status_with_input("Ready")
                return
            target_chat_id = self.last_chats[index - 1].get('id')
            if not target_chat_id:
                conversation.write("Invalid chat data.")
                self.set_status_with_input("Ready")
                return

        # Prevent removing the currently active chat
        if self.agent.session_id and target_chat_id == self.agent.session_id:
            conversation.write("Cannot remove the current active chat.")
            self.set_status_with_input("Ready")
            return

        # Set status to Loading
        if not self.timer_active:
            status_bar = self.query_one("#status")
            status_bar.start_elapsed_timer("Removing...")
            self.timer_active = True
        input_box = self.query_one("#input_box")
        input_box.disabled = True

        asyncio.create_task(self._remove_chat_async(conversation, target_chat_id))

    async def _remove_chat_async(self, conversation, chat_id: str) -> None:
        """Async worker to delete a single chat."""
        try:
            await asyncio.to_thread(self.agent.client.delete_chat, chat_id)
            # Also remove the local cache file
            cache_file = CACHE_DIR / f"{chat_id}.json"
            if cache_file.exists():
                cache_file.unlink()
            self._display_remove_chat_result(conversation, chat_id)
        except Exception as e:
            self._display_remove_chat_error(conversation, str(e))

    def _display_remove_chat_result(self, conversation, chat_id: str) -> None:
        """Display successful chat removal."""
        conversation.write(f"[green]Chat removed: {chat_id}[/green]")
        # Invalidate cached list so indices stay accurate
        self.last_chats = []
        self._reset_loading_status()
        self.query_one("#input_box").focus()

    def _display_remove_chat_error(self, conversation, error_msg: str) -> None:
        """Display chat removal error."""
        conversation.write(f"[red]Error removing chat: {error_msg}[/red]")
        self._reset_loading_status()
        self.query_one("#input_box").focus()

    def handle_remove_chats_command(self, conversation) -> None:
        """Handle the /remove_chats command to delete all chats except the current one."""
        if self.agent is None or self.agent.client is None:
            conversation.write("Agent not initialized.")
            self.set_status_with_input("Ready")
            return

        # Set status to Loading
        if not self.timer_active:
            status_bar = self.query_one("#status")
            status_bar.start_elapsed_timer("Removing all chats...")
            self.timer_active = True
        input_box = self.query_one("#input_box")
        input_box.disabled = True

        asyncio.create_task(self._remove_all_chats_async(conversation))

    async def _remove_all_chats_async(self, conversation) -> None:
        """Async worker to delete all cached chats except the current one."""
        try:
            current_id = self.agent.session_id
            removed_count = 0
            errors = []
            index = 0

            # Iterate over local cache files
            cache_files = sorted(CACHE_DIR.glob("*.json")) if CACHE_DIR.exists() else []
            for cache_file in cache_files:
                cid = cache_file.stem
                if not cid:
                    continue
                if current_id and cid == current_id:
                    continue
                index += 1
                try:
                    await asyncio.to_thread(self.agent.client.delete_chat, cid)
                    # Remove the local cache file
                    if cache_file.exists():
                        cache_file.unlink()
                    removed_count += 1
                    conversation.write(f"[green]{index}. Removed chat: {cid}[/green]")
                    self._scroll_to_bottom()
                except Exception as e:
                    errors.append(f"{cid}: {e}")
                    conversation.write(f"[red]{index}. Failed to remove chat {cid}: {e}[/red]")
                    self._scroll_to_bottom()
            self._display_remove_chats_result(conversation, removed_count, errors)
        except Exception as e:
            self._display_remove_chats_error(conversation, str(e))

    def _display_remove_chats_result(self, conversation, removed_count: int, errors: list) -> None:
        """Display bulk chat removal result."""
        conversation.write(f"[green]Removed {removed_count} chat(s). Current chat preserved.[/green]")
        if errors:
            for err in errors:
                conversation.write(f"[yellow]Failed: {err}[/yellow]")
        # Invalidate cached list
        self.last_chats = []
        self._reset_loading_status()
        self.query_one("#input_box").focus()

    def _display_remove_chats_error(self, conversation, error_msg: str) -> None:
        """Display bulk chat removal error."""
        conversation.write(f"[red]Error removing chats: {error_msg}[/red]")
        self._reset_loading_status()
        self.query_one("#input_box").focus()

    def handle_mode_command(self, conversation) -> None:
        """Handle the /mode command to toggle between normal and thinking modes."""
        if self.agent is None:
            conversation.write("Agent not initialized.")
            self.set_status_with_input("Ready")
            return

        new_mode = self.agent.toggle_mode()
        conversation.write(f"Mode changed to: '{new_mode}'. New client created with updated mode.")
        footer = self.query_one("#footer")
        footer.update(footer.render())
        self.set_status_with_input("Ready")

    def handle_current_chat_command(self, conversation) -> None:
        """Handle the /current_chat command to display current chat ID, parent message ID, and title from cache."""
        if self.agent is None:
            conversation.write("Agent not initialized.")
            self.set_status_with_input("Ready")
            return

        if not self.agent.session_id:
            conversation.write("No active chat session. Start a conversation first.")
            self.set_status_with_input("Ready")
            return

        chat_id = self.agent.session_id
        parent_message_id = self.agent.parent_message_id

        # Read title from local cache
        title = "N/A"
        cache_file = CACHE_DIR / f"{chat_id}.json"
        if cache_file.exists():
            try:
                payload = json.loads(cache_file.read_text(encoding='utf-8'))
                title = payload.get('title') or 'Untitled'
            except (json.JSONDecodeError, IOError):
                title = "N/A (cache unreadable)"

        conversation.write("[bold cyan]Current Chat:[/bold cyan]")
        conversation.write(f"  [dim]Chat ID:[/dim]           {chat_id}")
        conversation.write(f"  [dim]Parent Message ID:[/dim] {parent_message_id if parent_message_id else 'N/A'}")
        conversation.write(f"  [dim]Title:[/dim]             {title}")
        self.set_status_with_input("Ready")

    def handle_new_command(self, conversation) -> None:
        """Handle the /new command to create a new chat session on the server."""
        if self.agent is None or self.agent.client is None:
            conversation.write("Agent not initialized.")
            self.set_status_with_input("Ready")
            return

        if self.agent.running:
            conversation.write("Agent is currently running. Please wait.")
            self.set_status_with_input("Ready")
            return

        # Set status to Loading while creating new chat
        if not self.timer_active:
            status_bar = self.query_one("#status")
            status_bar.start_elapsed_timer("Creating new chat...")
            self.timer_active = True
        input_box = self.query_one("#input_box")
        input_box.disabled = True

        asyncio.create_task(self._create_new_chat_async(conversation))

    async def _create_new_chat_async(self, conversation) -> None:
        """Async worker to create a new chat session on the server."""
        try:
            new_chat_id = await asyncio.to_thread(self.agent.client.create_chat)
            self.agent.session_id = new_chat_id
            self.agent.parent_message_id = None
            self.agent.total_tokens = 0
            # Initialize a new chat cache for the new session
            self.agent.chat_cache = ChatCache(
                chat_id=new_chat_id,
                model_name=self.agent.model,
                thinking_mode=(self.agent.mode == "thinking"),
            )
            self._display_new_chat_result(conversation, new_chat_id)
        except Exception as e:
            self._display_new_chat_error(conversation, str(e))

    def _display_new_chat_result(self, conversation, chat_id: str) -> None:
        """Display new chat result in the UI thread."""
        conversation.clear()
        conversation.write(f"[green]New chat created: {chat_id}[/green]")
        self._reset_loading_status()
        self.query_one("#input_box").focus()

    def _display_new_chat_error(self, conversation, error_msg: str) -> None:
        """Display new chat error."""
        conversation.write(f"[red]Error creating new chat: {error_msg}[/red]")
        self._reset_loading_status()
        self.query_one("#input_box").focus()

    def handle_upload_chat_command(self, conversation, chat_arg: str) -> None:
        """Handle the /upload_chat command to upload a cached chat to the server.

        Accepts either:
        - An index number (e.g., '1') matching the /cached_chats listing order.
        - A chat_id (e.g., '74f7ff38-fa92-44be-a48a-62c88781ecf1').

        The cache file is looked up from CACHE_DIR as {chat_id}.json and
        uploaded via POST /api/v1/chats/upload-cache.
        """
        if self.agent is None or self.agent.client is None:
            conversation.write("Agent not initialized.")
            self.set_status_with_input("Ready")
            return

        # Resolve the argument to a cache file path
        resolved_path = None

        if chat_arg.isdigit():
            # Treat as an index into the sorted cache file list
            if not CACHE_DIR.exists():
                conversation.write("[red]No cached chats found.[/red]")
                self.set_status_with_input("Ready")
                return
            cache_files = sorted(CACHE_DIR.glob("*.json"))
            index = int(chat_arg)
            if index < 1 or index > len(cache_files):
                conversation.write(f"[red]Invalid index {index}. Use /cached_chats to see available chats (1-{len(cache_files)}).[/red]")
                self.set_status_with_input("Ready")
                return
            resolved_path = cache_files[index - 1]
        else:
            # Treat as a chat_id -> look up CACHE_DIR/{chat_id}.json
            candidate = CACHE_DIR / f"{chat_arg}.json"
            if candidate.is_file():
                resolved_path = candidate
            else:
                conversation.write(f"[red]No cached chat found for index/chat_id: {chat_arg}[/red]")
                conversation.write("[dim]Use /cached_chats to list available chats with their indices.[/dim]")
                self.set_status_with_input("Ready")
                return

        # Load and validate the cache format {chat_id, messages[]}
        try:
            payload = json.loads(resolved_path.read_text(encoding='utf-8'))
        except (json.JSONDecodeError, IOError) as e:
            conversation.write(f"[red]Could not read chat cache file: {e}[/red]")
            self.set_status_with_input("Ready")
            return

        if not isinstance(payload, dict) or not payload.get('chat_id') or not payload.get('messages'):
            conversation.write("[red]Invalid chat cache format: expected {\"chat_id\": ..., \"messages\": [...]}[/red]")
            self.set_status_with_input("Ready")
            return

        # Set status to Loading while uploading
        if not self.timer_active:
            status_bar = self.query_one("#status")
            status_bar.start_elapsed_timer("Uploading...")
            self.timer_active = True
        input_box = self.query_one("#input_box")
        input_box.disabled = True

        asyncio.create_task(self._upload_chat_async(conversation, str(resolved_path), payload))

    async def _upload_chat_async(self, conversation, file_path: str, payload: dict) -> None:
        """Async worker to upload a chat cache payload via /api/v1/chats/upload-cache.

        The upload response is: {"chat_id": "<new_id>", "parent_message_id": "<parent_id>"}
        After a successful upload we:
        1. Update the chat_id inside the local JSON file.
        2. Replace the last message's message_id with the returned parent_message_id.
        3. Rename the file to {new_chat_id}.json.
        """
        try:
            # Repair any missing message ids / parent links before uploading
            normalized = self.agent._normalize_cache_payload(payload)
            result = await asyncio.to_thread(self.agent.client.upload_chat_cache, normalized)

            # Extract new chat_id and parent_message_id from the response
            new_chat_id = result.get('chat_id') if isinstance(result, dict) else None
            new_parent_message_id = result.get('parent_message_id') if isinstance(result, dict) else None

            if new_chat_id:
                old_path = Path(file_path)
                new_path = old_path.parent / f"{new_chat_id}.json"

                # Update chat_id in the payload
                payload['chat_id'] = new_chat_id

                # Replace the last message's message_id with the returned parent_message_id
                if new_parent_message_id and payload.get('messages'):
                    payload['messages'][-1]['message_id'] = new_parent_message_id

                # Write updated JSON and rename the file
                old_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding='utf-8')
                if old_path != new_path:
                    old_path.rename(new_path)
                conversation.write(f"  [dim]Cache file updated: {old_path.name} -> {new_path.name}[/dim]")

            self._display_upload_chat_result(conversation, file_path, result)
        except Exception as e:
            self._display_upload_chat_error(conversation, str(e))

    def _display_upload_chat_result(self, conversation, file_path: str, data) -> None:
        """Display upload chat result in the UI thread."""
        new_chat_id = data.get('chat_id') if isinstance(data, dict) else None
        if new_chat_id:
            conversation.write(f"[green]Chat uploaded successfully from: {os.path.basename(file_path)}[/green]")
            conversation.write(f"  [dim]New chat ID:[/dim] {new_chat_id}")
        else:
            conversation.write(f"[yellow]Upload response: {data}[/yellow]")
        self._reset_loading_status()
        self.query_one("#input_box").focus()

    def _display_upload_chat_error(self, conversation, error_msg: str) -> None:
        """Display upload chat error."""
        conversation.write(f"[red]Error uploading chat: {error_msg}[/red]")
        self._reset_loading_status()
        self.query_one("#input_box").focus()

    def handle_attach_command(self, conversation, image_path: str) -> None:
        """Handle the /attach command to upload an image and queue it for the next prompt."""
        if self.agent is None or self.agent.client is None:
            conversation.write("Agent not initialized.")
            return

        # Resolve path relative to workspace or CWD
        if not os.path.isabs(image_path):
            workspace = getattr(self.state, 'workspace', '.') if hasattr(self, 'state') else '.'
            image_path = os.path.join(workspace, image_path)
        resolved_path = os.path.abspath(image_path)

        if not os.path.isfile(resolved_path):
            conversation.write(f"[red]File not found: {image_path}[/red]")
            return

        # Set status to Uploading
        if not self.timer_active:
            status_bar = self.query_one("#status")
            status_bar.start_elapsed_timer("Uploading image...")
            self.timer_active = True
        input_box = self.query_one("#input_box")
        input_box.disabled = True

        asyncio.create_task(self._attach_image_async(conversation, resolved_path))

    async def _attach_image_async(self, conversation, file_path: str) -> None:
        """Async worker to upload an image and attach it."""
        try:
            file_obj = await asyncio.to_thread(self.agent.attach_file, file_path)
            self._display_attach_result(conversation, file_obj)
        except Exception as e:
            Logger.error(f"Attach failed: {e}")
            Logger.error(traceback.format_exc())
            self._display_attach_error(conversation, str(e))
        finally:
            # Ensure input is re-focused after upload completes
            self.query_one("#input_box").focus()

    def _display_attach_result(self, conversation, file_obj: dict) -> None:
        """Display attach success."""
        name = file_obj.get('name', 'unknown')
        size = file_obj.get('size', 0)
        url = file_obj.get('url', '')
        count = len(self.agent.pending_files) if self.agent else 0
        conversation.write(f"[green]✓ Image attached: {name}[/green]")
        conversation.write(f"  [dim]Size: {size} bytes[/dim]")
        if url:
            conversation.write(f"  [dim]URL: {url}[/dim]")
        conversation.write(f"  [dim]Pending attachments: {count}[/dim]")
        self._reset_loading_status()
        self.query_one("#input_box").focus()

    def _display_attach_error(self, conversation, error_msg: str) -> None:
        """Display attach error."""
        conversation.write(f"[red]Error attaching image: {error_msg}[/red]")
        self._reset_loading_status()
        self.query_one("#input_box").focus()

    def handle_attachments_command(self, conversation) -> None:
        """Handle the /attachments command to list pending attachments."""
        if self.agent is None:
            conversation.write("Agent not initialized.")
            return
        pending = self.agent.pending_files
        if not pending:
            conversation.write("[dim]No pending attachments. Use /attach <path> to add one.[/dim]")
            return
        conversation.write(f"[bold cyan]{len(pending)} pending attachment(s):[/bold cyan]")
        for i, f in enumerate(pending, 1):
            name = f.get('name', 'unknown')
            size = f.get('size', 0)
            conversation.write(f"  [yellow]{i}.[/yellow] {name} [dim]({size} bytes)[/dim]")

    def handle_clear_attachments_command(self, conversation) -> None:
        """Handle the /clear_attachments command to remove all pending attachments."""
        if self.agent is None:
            conversation.write("Agent not initialized.")
            return
        count = len(self.agent.pending_files)
        self.agent.clear_pending_files()
        conversation.write(f"[green]Cleared {count} pending attachment(s).[/green]")

    def handle_cached_chats_command(self, conversation) -> None:
        """Handle the /cached_chats command to list all locally cached chats."""
        if not CACHE_DIR.exists():
            conversation.write("No cached chats found.")
            return

        cache_files = sorted(CACHE_DIR.glob("*.json"))
        if not cache_files:
            conversation.write("No cached chats found.")
            return

        conversation.write(f"Found {len(cache_files)} cached chat(s):")
        conversation.write("")
        for i, cache_file in enumerate(cache_files, 1):
            try:
                payload = json.loads(cache_file.read_text(encoding='utf-8'))
                chat_id = payload.get('chat_id', cache_file.stem)
                title = payload.get('title') or 'Untitled'
                msg_count = len(payload.get('messages', []))
                conversation.write(f"[bold cyan]{i}.[/bold cyan] [yellow]ID:[/yellow] [bold white]{chat_id}[/bold white]")
                conversation.write(f"   [dim]Title:[/dim] {title}  [dim]Messages:[/dim] {msg_count}")
                conversation.write("")
            except (json.JSONDecodeError, IOError) as e:
                conversation.write(f"[bold cyan]{i}.[/bold cyan] [red]{cache_file.name} (unreadable: {e})[/red]")
                conversation.write("")

        conversation.write("[dim]Use /upload_chat <index or chat_id> to upload a cached chat[/dim]")

    async def _stop_completion_and_reset_canceling(self, client, session_id, prev_msg_id, parent_msg_id) -> None:
        """Run StopSendingPrompt in a background thread, then clear the canceling flag."""
        try:
            await asyncio.to_thread(client.StopSendingPrompt, session_id, prev_msg_id)
            await asyncio.to_thread(client.StopSendingPrompt, session_id, parent_msg_id)
        except Exception as e:
            Logger.error(f"Failed to stop completion on server: {e}")
        finally:
            self.is_canceling = False

    def _apply_canceling_status(self) -> None:
        """Apply the canceling status after any pending callbacks have been processed."""
        self.set_status_with_input("Canceling.....")

    def _scroll_to_bottom(self) -> None:
        """Scroll the scrollable area to the bottom."""
        scrollable = self.query_one("#scrollable_area")
        scrollable.scroll_end(animate=False)
