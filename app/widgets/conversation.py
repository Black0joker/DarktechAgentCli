import re
from textual.widgets import Static, LoadingIndicator
from textual.containers import Container, Horizontal
from rich.text import Text
from rich.markup import render as rich_markup_render, escape
from rich.errors import MarkupError

class ConversationView(Container):
    """Conversation area that displays messages. It is meant to be placed inside a scrollable container."""
    DEFAULT_CSS = """
    .user-message {
    height:auto;
        background: #161b22;
        border-left: solid #58a6ff;
        padding: 1 1;
        margin: 2 0;
        color: #c9d1d9;
        
    }

    .user-message Static {
        color: #c9d1d9;
    }
    .user-message .command-message {
        color: #bc8cff;
    }
    .assistant-message {
        margin: 1 0;
        border-left: solid #bc8cff;
        color: #c9d1d9;
        background: #0d1117;
        text-style: bold;
    }
    .system-message {
        margin: 1 0;
        background: #0d1117;
        color: #8b949e;
    }
    .tool-message {
        height: auto;
        margin: 1 0;
        padding: 0 1;
        background: #0d1117;
        border-left: solid #d29922;
    }
    .tool-message.tool-success { border-left: solid #3fb950; }
    .tool-message.tool-error { border-left: solid #f85149; }
    .tool-message.tool-denied { border-left: solid #f85149; }
    .tool-message .tool-row { height: auto; }
    .tool-message LoadingIndicator {
        width: 3;
        height: 1;
        color: #d29922;
        background: #0d1117;
    }
    .tool-message .tool-label {
        color: #c9d1d9;
        height: auto;
    }
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.stylize = True
    
    def write(self, text: str) -> None:
        """Add a message to the conversation."""
        # Determine if it's a user message (starts with "> ")
        if text.startswith("> "):
            content = text[2:]
            if content.startswith("/"):
                # Command message: apply command-message class for purple color
                container = Container(
                    Static(text, classes="command-message"),
                    classes="user-message"
                )
            
            else:
                # Normal message: gray ">" prefix, white content
                styled_text = Text("> ", style="#C792EA") + Text(content, style="white")
                container = Container(
                    Static(styled_text),
                    classes="user-message"
                )
            self.mount(container)
        

        else:
            # Other messages (system, agent asks, tool info)
            # Detect Rich markup tags anywhere in the text (not just at the start).
            # Matches patterns like [dim], [bold cyan], [#88d8b0], [/dim], [bold #ff9966], etc.
            if re.search(r'\[/?\s*(?:bold|dim|italic|underline|strike|blink|blink2|reverse|conceal|overline|red|green|blue|cyan|magenta|yellow|white|black|bright_\w+|#[0-9a-fA-F]{3,8}|on\s+\S+|link\s*=)(?:\s+(?:\w+|#[0-9a-fA-F]{3,8}))*\s*\]', text):
                # Contains intentional Rich markup from our own code - validate and render it
                try:
                    rich_markup_render(text)
                    self.mount(Static(text, classes="system-message", markup=True))
                except MarkupError:
                    # Markup is malformed (e.g. unbalanced tags from external content)
                    self.mount(Static(text, classes="system-message", markup=False))
            else:
                # No markup detected - display as plain text to prevent injection
                self.mount(Static(text, classes="system-message", markup=False))
        # Schedule scroll after layout is complete
        parent = self.parent
        if parent and hasattr(parent, 'scroll_end'):
            self.call_after_refresh(lambda p=parent: p.scroll_end(animate=False) if p else None)

    def _scroll_to_end(self) -> None:
        parent = self.parent
        if parent and hasattr(parent, 'scroll_end'):
            self.call_after_refresh(lambda p=parent: p.scroll_end(animate=False) if p else None)

    def write_tool_running(self, label) -> "Container":
        """Mount a tool-call card in its running (in-progress) state.

        Returns the container so the caller can finalize it once the tool finishes.
        """
        spinner = LoadingIndicator()
        label_static = Static(label, classes="tool-label")
        row = Horizontal(spinner, label_static, classes="tool-row")
        container = Container(row, classes="tool-message tool-running")
        self.mount(container)
        self._scroll_to_end()
        return container

    def finalize_tool(self, container, markup_lines, state: str) -> None:
        """Replace a running tool card's content with the final result.

        state: 'success' | 'error' | 'denied'
        """
        if container is None:
            return
        try:
            container.remove_class("tool-running")
            container.add_class(f"tool-{state}")
            container.remove_children()
            text = "\n".join(markup_lines)
            container.mount(Static(text, markup=True, classes="tool-label"))
        except Exception:
            for line in markup_lines:
                self.write(line)
        self._scroll_to_end()

    def write_stream_start(self) -> "Static":
        """Create and mount a widget for streaming text output."""
        widget = Static("", classes="system-message", markup=True)
        self.mount(widget)
        self._scroll_to_end()
        return widget

    def write_stream_update(self, widget, text: str) -> None:
        """Update the streaming widget with accumulated text."""
        if widget is not None:
            widget.update(f"[#88d8b0]\u2726 {escape(text)}[/#88d8b0]")

    def write_stream_end(self, widget, text: str) -> None:
        """Finalize the streaming widget."""
        if widget is not None:
            widget.update(f"[#88d8b0]\u2726 {escape(text)}[/#88d8b0]")
        self._scroll_to_end()

    def clear(self) -> None:
        """Clear all messages."""
        self.remove_children()
