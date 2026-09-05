from textual.widgets import Static

class CustomHeader(Static):
    """Custom header widget that displays the current application status."""
    DEFAULT_CSS = """
    CustomHeader {
        color: #58a6ff;
        text-style: bold;
        padding: 0 1;
    }
    """
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.state = "Ready"

    def render(self) -> str:
        # Get the current status from the app's status bar if available
        try:
            status_bar = self.app.query_one("#status")
            if hasattr(status_bar, 'status_text'):
                self.state = status_bar.status_text
        except Exception:
            pass
        return f"◆ {self.state}"

    def set_state(self, state: str) -> None:
        self.state = state
        self.refresh()
