from textual.widgets import Static
from textual.binding import Binding
from textual.message import Message
from textual.containers import Container
from textual.events import Key
from rich.markup import escape


class ToolPermissionOption(Static):
    """A single permission option that can be selected."""
    DEFAULT_CSS = """
    ToolPermissionOption {
        color: #8b949e;
        padding: 0 1;
        height: 1;
    }
    ToolPermissionOption.selected {
        color: #58a6ff;
        background: #1c2733;
    }
    """

    def __init__(self, label: str, value: str, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.label = label
        self.value = value
        self.selected = False

    def render(self) -> str:
        if self.selected:
            return f"[bold #58a6ff]▸ {self.label}[/bold #58a6ff]"
        else:
            return f"  {self.label}"

    def set_selected(self, selected: bool):
        self.selected = selected
        if selected:
            self.add_class("selected")
        else:
            self.remove_class("selected")
        self.refresh()


class ToolPermissionWidget(Container):
    """Widget for selecting tool permission with arrow keys."""

    BINDINGS = [
        Binding("up", "cursor_up", "Up", show=False),
        Binding("down", "cursor_down", "Down", show=False),
        Binding("enter", "select", "Select"),
    ]

    can_focus = True
    can_focus_children = False

    DEFAULT_CSS = """
    ToolPermissionWidget {
        height: auto;
        background: #161b22;
        border: solid #30363d;
        padding: 0;
    }
    ToolPermissionWidget .permission-header {
        height: 1;
        padding: 0 1;
        background: #1c2128;
        color: #c9d1d9;
    }
    ToolPermissionWidget .permission-options {
        height: auto;
        padding: 0;
    }
    """

    class Selected(Message):
        """Message sent when a permission option is selected."""
        def __init__(self, value: str):
            self.value = value
            super().__init__()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.options = []
        self.current_index = 0
        self._header_static = None

    def compose(self):
        """Create the permission options."""
        self._header_static = Static("", classes="permission-header")
        yield self._header_static
        self.options = [
            ToolPermissionOption("Allow once", "allow_once", classes="permission-option"),
            ToolPermissionOption("Allow session", "allow_session", classes="permission-option"),
            ToolPermissionOption("Deny", "deny", classes="permission-option"),
        ]
        for option in self.options:
            yield option
        # Select first option by default
        if self.options:
            self.options[0].set_selected(True)

    def set_tool_info(self, tool_display: str, arg_summary: str = ""):
        """Set the tool information shown in the header."""
        if self._header_static:
            safe_summary = escape(str(arg_summary)) if arg_summary else ""
            if safe_summary:
                text = f"[bold #58a6ff]{escape(tool_display)}[/bold #58a6ff] [dim]{safe_summary}[/dim]"
            else:
                text = f"[bold #58a6ff]{escape(tool_display)}[/bold #58a6ff]"
            self._header_static.update(text)

    def on_key(self, event: Key) -> None:
        """Handle key events directly to ensure they work."""
        if event.key == "up":
            event.prevent_default()
            event.stop()
            self.action_cursor_up()
        elif event.key == "down":
            event.prevent_default()
            event.stop()
            self.action_cursor_down()
        elif event.key == "enter":
            event.prevent_default()
            event.stop()
            self.action_select()

    def action_cursor_up(self):
        """Move selection up."""
        if self.options and self.current_index > 0:
            self.options[self.current_index].set_selected(False)
            self.current_index -= 1
            self.options[self.current_index].set_selected(True)

    def action_cursor_down(self):
        """Move selection down."""
        if self.options and self.current_index < len(self.options) - 1:
            self.options[self.current_index].set_selected(False)
            self.current_index += 1
            self.options[self.current_index].set_selected(True)

    def action_select(self):
        """Select the current option."""
        if self.options:
            selected_value = self.options[self.current_index].value
            self.post_message(self.Selected(selected_value))

    def reset(self):
        """Reset to first option."""
        if self.options:
            for option in self.options:
                option.set_selected(False)
            self.current_index = 0
            self.options[0].set_selected(True)
