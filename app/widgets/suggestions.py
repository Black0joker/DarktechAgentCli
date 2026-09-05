from textual.widgets import ListView, ListItem, Static
from textual.containers import Horizontal
from textual.message import Message
from typing import Union

from app.command_defs import Command

class CommandListItem(ListItem):
    """Custom ListItem for displaying a command with name and description."""
    def __init__(self, command: Command, name_width: int = 15):
        self.command = command
        # +1 guarantees at least one column of gap between name and description
        col_width = name_width + 1
        name_text = command.name.ljust(col_width)
        desc_text = command.description
        name_static = Static(name_text, classes="command-name")
        name_static.styles.width = col_width
        desc_static = Static(desc_text, classes="command-desc")
        container = Horizontal(name_static, desc_static, classes="command-row")
        super().__init__(container)

class FileListItem(ListItem):
    """Custom ListItem for displaying a file or folder path."""
    def __init__(self, path: str, is_dir: bool = False):
        self.path = path
        self.is_dir = is_dir
        icon = "📁 " if is_dir else "📄 "
        label = icon + path
        static = Static(label, classes="file-label")
        super().__init__(static)

class SuggestionsList(ListView):
    DEFAULT_CSS = """
    CommandListItem {
        height: 1;
    }
    FileListItem {
        height: 1;
    }
    .command-row {
        height: 1;
        padding: 0 1;
    }
    .command-name {
        color: #3fb950;
        height: 1;
    }
    .command-desc {
        color: #8b949e;
        height: 1;
    }
    .file-label {
        color: #c9d1d9;
        padding: 0 1;
    }
    .highlight .command-name {
        color: #c9d1d9;
    }
    .highlight .command-desc {
        color: #c9d1d9;
    }
    .highlight .file-label {
        color: #ffffff;
    }
    .highlight {
        background: #1f6feb;
    }
    """

    class Selected(Message):
        def __init__(self, list_view: "SuggestionsList", item, index: int):
            self.list_view = list_view
            self.item = item
            self.index = index
            super().__init__()

        @property
        def value(self) -> str:
            """Extract the string value from the selected item."""
            if isinstance(self.item, CommandListItem):
                return self.item.command.name
            elif isinstance(self.item, FileListItem):
                return self.item.path
            return ""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._items = []
        self._highlighted_index = 0

    def show(self) -> None:
        self.add_class("visible")
        self.refresh()

    def hide(self) -> None:
        self.remove_class("visible")
        self.refresh()

    def update_items(self, items: list[Union[Command, dict]]) -> None:
        """Update the list of suggestions with Command objects or file dicts.
        
        Args:
            items: List of Command objects or dicts with 'name' and 'is_dir' keys.
        """
        self.clear()
        self._items = items
        if not items:
            self.hide()
            return
        
        # Check if items are Commands or file entries
        if items and isinstance(items[0], Command):
            max_name_len = max(len(cmd.name) for cmd in items) if items else 15
            for cmd in items:
                self.append(CommandListItem(cmd, max_name_len))
        else:
            # File/folder items
            for item in items:
                self.append(FileListItem(item['name'], item.get('is_dir', False)))
        
        self._highlighted_index = 0
        # Use call_after_refresh to ensure children are mounted before highlighting
        self.call_after_refresh(self._apply_initial_highlight)

    def _apply_initial_highlight(self) -> None:
        """Apply highlight to first item after widgets are mounted."""
        self._highlight_item(0)
        self.show()

    def _highlight_item(self, index: int) -> None:
        """Highlight the item at the given index (0-based)."""
        if not self._items or index < 0 or index >= len(self._items):
            return
        for child in self.children:
            child.remove_class("highlight")
        child = self.children[index]
        child.add_class("highlight")
        self._highlighted_index = index
        self.scroll_to_widget(child, animate=False)

    @property
    def is_visible(self) -> bool:
        """Check if suggestions list is currently visible."""
        return self.has_class("visible")

    def move_up(self) -> None:
        """Move highlight up."""
        if not self.is_visible or not self._items:
            return
        new_index = (self._highlighted_index - 1) % len(self._items)
        self._highlight_item(new_index)

    def move_down(self) -> None:
        """Move highlight down."""
        if not self.is_visible or not self._items:
            return
        new_index = (self._highlighted_index + 1) % len(self._items)
        self._highlight_item(new_index)

    def get_selected_value(self) -> str | None:
        """Return the name/path of the currently highlighted item, or None."""
        if not self._items:
            return None
        item = self._items[self._highlighted_index]
        if isinstance(item, Command):
            return item.name
        elif isinstance(item, dict):
            return item['name']
        return str(item)

    def select_current(self) -> None:
        """Select the currently highlighted item and emit Selected event."""
        if not self._items:
            return
        item = self.children[self._highlighted_index]
        self.post_message(self.Selected(self, item, self._highlighted_index))

    def close(self) -> None:
        """Hide the suggestions list."""
        self.hide()
