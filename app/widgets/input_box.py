import os
from textual.widgets import TextArea
from textual import events
from textual.message import Message
from textual.binding import Binding
from typing import Any, cast
from app.widgets.suggestions import SuggestionsList


def _list_directory(dir_path: str, workspace: str) -> list[dict]:
    """List direct children (files and subdirectories) of dir_path.

    Returns a list of dicts with 'name' (relative path from workspace) and 'is_dir' keys.
    Directories are listed first (sorted), then files (sorted).
    Skips hidden entries (starting with '.') and __pycache__.
    """
    try:
        subdirs = []
        files = []
        with os.scandir(dir_path) as it:
            for entry in it:
                if entry.name.startswith('.') or entry.name == '__pycache__':
                    continue
                rel = os.path.relpath(entry.path, workspace)
                if entry.is_dir(follow_symlinks=False):
                    subdirs.append({'name': rel, 'is_dir': True})
                else:
                    files.append({'name': rel, 'is_dir': False})
        subdirs.sort(key=lambda x: x['name'].lower())
        files.sort(key=lambda x: x['name'].lower())
        return subdirs + files
    except (OSError, PermissionError):
        return []


class InputBox(TextArea):

    BINDINGS = [
        Binding("enter", "send_or_select", "Send", show=False, priority=True),
        Binding("ctrl+j", "newline", "New Line", show=False),
        Binding("ctrl+a", "select_all", "Select All", show=False),
    ]

    class MessageSent(Message):
        def __init__(self, text: str):
            self.text = text
            super().__init__()

    class TextChanged(Message):
        def __init__(self, text: str):
            self.text = text
            super().__init__()

    class SuggestionSelected(Message):
        def __init__(self, text: str):
            self.text = text
            super().__init__()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.show_line_numbers = False
        self.language = None

    def on_mount(self) -> None:
        self.placeholder = "Type your message or @path/to/file (Ctrl+J for new line)"

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        """Called whenever the TextArea content changes."""
        text = self.text
        if "@" in text:
            self._check_at_mention(text)
        else:
            try:
                suggestions = self.app.query_one("#suggestions", SuggestionsList)
                suggestions.hide()
            except Exception:
                pass
        self._emit_text_changed()

    def action_send_or_select(self) -> None:
        """Handle Enter key: select suggestion if visible, otherwise send message."""
        try:
            suggestions = self.app.query_one("#suggestions", SuggestionsList)
        except Exception:
            suggestions = None

        if suggestions and suggestions.is_visible:
            value = suggestions.get_selected_value()
            if value:
                self.apply_suggestion(value)
                current_text = self.text
                if current_text.endswith("/") and "@" in current_text:
                    self._check_at_mention(current_text)
                    return
                suggestions.close()
                if "@" in self.text:
                    self.cursor_location = self.document.end
                    self.refresh()
                    self.focus()
                    return
                # Command suggestion was applied: check if it requires args before sending
                text = self.text
                if text.strip():
                    parts = text.split()
                    cmd_name = parts[0] if parts else ""
                    requires_args = self.app.command_requires_arguments(cmd_name) if hasattr(self.app, "command_requires_arguments") else False
                    if requires_args and len(parts) == 1:
                        # Command needs arguments - add a space and let user type them
                        self.insert(" ")
                        self.cursor_location = self.document.end
                        self.refresh()
                        self.focus()
                    else:
                        self.clear()
                        self.post_message(self.MessageSent(text.strip()))
                return

        # Send the message
        text = self.text
        if text.startswith("/"):
            parts = text.split()
            cmd_name = parts[0] if parts else ""
            requires_args = self.app.command_requires_arguments(cmd_name) if hasattr(self.app, "command_requires_arguments") else False
            if requires_args and len(parts) == 1:
                # Command needs arguments - add a space and let user type them
                self.insert(" ")
                self.cursor_location = self.document.end
                self.refresh()
                self.focus()
            else:
                self.clear()
                self.post_message(self.MessageSent(text.strip()))
        else:
            if text.strip():
                self.clear()
                self.post_message(self.MessageSent(text.strip()))

    def action_newline(self) -> None:
        """Insert a newline for multi-line input (Ctrl+J)."""
        self.insert("\n")

    def on_key(self, event: events.Key) -> None:
        try:
            suggestions = self.app.query_one("#suggestions", SuggestionsList)
        except Exception:
            suggestions = None

        suggestions = cast(SuggestionsList, suggestions)
        if suggestions and suggestions.is_visible:
            if event.key == "up":
                suggestions.move_up()
                event.prevent_default()
                event.stop()
                return
            elif event.key == "down":
                suggestions.move_down()
                event.prevent_default()
                event.stop()
                return
            elif event.key == "tab":
                value = suggestions.get_selected_value()
                if value:
                    self.apply_suggestion(value)
                    current_text = self.text
                    if current_text.endswith("/") and "@" in current_text:
                        self._check_at_mention(current_text)
                    event.prevent_default()
                    event.stop()
                    return

        if event.key == "escape":
            if suggestions:
                suggestions.close()
            event.prevent_default()
            event.stop()
            return

    def _emit_text_changed(self):
        text = self.text
        self.post_message(self.TextChanged(text))

    def _check_at_mention(self, text: str) -> None:
        """Check if text contains an @ mention and show file suggestions.

        When just '@' is typed (no partial path), lists the direct children
        of the workspace (current path). When a partial path ending with '/'
        is provided, lists the direct children of that subdirectory. When a
        partial path without trailing '/' is provided, lists siblings of the
        partial's parent directory filtered by the partial's basename prefix.
        This enables directory-style browsing via successive selections.
        """
        suggestions = None
        try:
            suggestions = self.app.query_one("#suggestions", SuggestionsList)
        except Exception:
            pass
        if suggestions is None:
            return

        # Find the last @ symbol position
        at_pos = text.rfind("@")
        if at_pos == -1:
            suggestions.hide()
            return

        # Get the partial path after @
        partial = text[at_pos + 1:]

        # If there's a space after @, it means the mention is complete or invalid
        if " " in partial:
            suggestions.hide()
            return

        # Determine workspace root
        workspace = getattr(self.app.state, 'workspace', os.getcwd()) if hasattr(self.app, 'state') else os.getcwd()
        workspace = os.path.abspath(workspace)

        try:
            if partial == "":
                # Just @ typed: list direct children of the workspace (current path)
                entries = _list_directory(workspace, workspace)
            elif partial.endswith("/"):
                # e.g., @folder/ - list direct children of that subdirectory
                target = os.path.normpath(os.path.join(workspace, partial))
                if os.path.isdir(target):
                    entries = _list_directory(target, workspace)
                else:
                    entries = []
            else:
                # Partial path with a basename prefix to filter by
                parent_rel = os.path.dirname(partial)
                prefix = os.path.basename(partial).lower()
                parent_abs = os.path.join(workspace, parent_rel) if parent_rel else workspace
                parent_abs = os.path.normpath(parent_abs)
                if os.path.isdir(parent_abs):
                    all_entries = _list_directory(parent_abs, workspace)
                    entries = [
                        e for e in all_entries
                        if os.path.basename(e['name']).lower().startswith(prefix)
                    ]
                else:
                    entries = []

            if entries:
                suggestions.update_items(entries)
            else:
                suggestions.hide()
        except (OSError, PermissionError):
            suggestions.hide()

    def apply_suggestion(self, suggestion: str) -> None:
        current = self.text
        if "@" in current:
            # Replace the @partial with the selected path
            at_pos = current.rfind("@")
            if at_pos != -1:
                before_at = current[:at_pos]
                try:
                    workspace = self.app.state.workspace
                except Exception:
                    workspace = '.'
                full_path = os.path.join(workspace, suggestion)
                # Add trailing slash for directories to allow further navigation
                if os.path.isdir(full_path):
                    suffix = "/"
                else:
                    suffix = " "
                self.clear()
                self.insert(before_at + "@" + suggestion + suffix)
            else:
                self.clear()
                self.insert(suggestion)
        else:
            self.clear()
            self.insert(suggestion)
        self.cursor_location = self.document.end
        self.refresh()
