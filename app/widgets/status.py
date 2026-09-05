from textual.widgets import Static
from textual.timer import Timer
from rich.text import Text

class StatusBar(Static):
    """Status line with animated spinner and elapsed timer."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.status_text = "Ready"
        self.spinner_chars = ["\u280b", "\u2819", "\u2839", "\u2838", "\u283c", "\u2834", "\u2826", "\u2827", "\u2807", "\u280f"]
        self.spinner_index = 0
        self.timer: Timer | None = None
        self.spinner_active = False
        # Elapsed timer attributes
        self.elapsed_active = False
        self.elapsed_seconds = 0
        self.elapsed_timer: Timer | None = None
        self.tokens: int | None = None

    def on_mount(self) -> None:
        self.render()

    def render(self) -> Text:
        # Style for status text based on state
        if self.status_text == "Ready":
            status_style = "bold #3fb950"  # Green for ready
        elif self.status_text.startswith("Thinking") or self.status_text.startswith("Loading"):
            status_style = "bold #58a6ff"  # Blue for thinking/loading
        elif self.status_text.startswith("Error"):
            status_style = "bold #f85149"  # Red for errors
        else:
            status_style = "bold #d29922"  # Amber for other states

        tokens_part = Text(f" · {self.tokens:,} tokens", style="#8b949e") if self.tokens is not None else Text("")
        if self.elapsed_active:
            spinner = self.spinner_chars[self.spinner_index] if self.spinner_active else ""
            # Build status with elapsed seconds
            status_part = Text(self.status_text, style=status_style)
            elapsed_part = Text(f" ({self.elapsed_seconds}s)", style="#8b949e")
            return Text(spinner + " ", style="#58a6ff") + status_part + elapsed_part + tokens_part
        elif self.spinner_active and self.status_text != "Ready":
            spinner = self.spinner_chars[self.spinner_index]
            status_part = Text(self.status_text, style=status_style)
            return Text(spinner + " ", style="#58a6ff") + status_part + tokens_part
        else:
            return Text(self.status_text, style=status_style) + tokens_part

    def set_status(self, text: str) -> None:
        """Update the status text and stop any running timer if status becomes Ready."""
        # During cancellation, only allow "Canceling....." and "Ready" through
        app = self.app if hasattr(self, 'app') else None
        if app and hasattr(app, 'is_canceling') and app.is_canceling:
            if text not in ("Canceling.....", "Ready"):
                return
        
        self.status_text = text
        if text == "Ready":
            self.stop_elapsed_timer()
            self.stop_spinner()
        else:
            self.start_spinner()
        self.refresh()

    def clear_tokens(self) -> None:
        """Clear the token count display."""
        self.tokens = None
        self.refresh()

    def set_tokens(self, tokens: int) -> None:
        """Update the token count displayed in the status bar."""
        self.tokens = tokens
        self.refresh()

    def start_elapsed_timer(self, text: str) -> None:
        """Start a stopwatch timer that counts up seconds and displays elapsed time.
        
        Args:
            text: Status text to display (e.g., "Thinking...")
        """
        # Block timer starts during cancellation
        app = self.app if hasattr(self, 'app') else None
        if app and hasattr(app, 'is_canceling') and app.is_canceling:
            return
        
        # Stop any existing timer and spinner
        self.stop_elapsed_timer()
        self.stop_spinner()
        
        self.status_text = text
        self.elapsed_seconds = 1
        self.elapsed_active = True
        self.start_spinner()
        
        # Start the timer that increments every second
        self.elapsed_timer = self.set_interval(1.0, self._update_elapsed)
        self.refresh()

    def update_status_text(self, text: str) -> None:
        """Update the status text without resetting the elapsed timer.
        
        Args:
            text: New status text to display
        """
        # Block text updates during cancellation
        app = self.app if hasattr(self, 'app') else None
        if app and hasattr(app, 'is_canceling') and app.is_canceling:
            return
        
        self.status_text = text
        self.refresh()

    def _update_elapsed(self) -> None:
        """Increment elapsed seconds and refresh the display."""
        if not self.elapsed_active:
            return
        self.elapsed_seconds += 1
        self.refresh()

    def stop_elapsed_timer(self) -> None:
        """Stop the elapsed timer and reset state."""
        if self.elapsed_timer:
            self.elapsed_timer.stop()
            self.elapsed_timer = None
        self.elapsed_active = False
        self.elapsed_seconds = 0
        self.refresh()

    def start_spinner(self) -> None:
        """Start the spinner animation if not already running."""
        if not self.spinner_active:
            self.spinner_active = True
            self.timer = self.set_interval(0.1, self.advance_spinner)

    def stop_spinner(self) -> None:
        """Stop the spinner animation."""
        if self.spinner_active:
            self.spinner_active = False
            if self.timer:
                self.timer.stop()
                self.timer = None

    def advance_spinner(self) -> None:
        """Advance to the next spinner character and refresh."""
        if self.spinner_active:
            self.spinner_index = (self.spinner_index + 1) % len(self.spinner_chars)
            self.refresh()

    def on_unmount(self) -> None:
        """Stop all timers when widget is unmounted."""
        self.stop_spinner()
        self.stop_elapsed_timer()
