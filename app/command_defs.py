from dataclasses import dataclass

# Available models that can be cycled through via /model toggle
AVAILABLE_MODELS = ["darktech_v3","darktech_v2","darktech_v1" ]

@dataclass
class Command:
    name: str
    description: str
    requires_arguments: bool = False
    
    def __post_init__(self):
        """Validate that command name starts with '/'."""
        if not self.name.startswith('/'):
            raise ValueError(f"Command name must start with '/': {self.name}")

COMMANDS = [
    Command("/clear", "Clear the conversation"),
    Command("/help", "Show available commands"),
    Command("/new", "Start a new chat session on the server"),
    Command("/settings", "Display current settings"),
    Command("/model", "Toggle active model (darktech_v3 -> darktech_v2 -> darktech_v1) or set with /model <name>"),
    Command("/quit", "Exit the application"),
    Command("/chats", "List all chats"),
    Command("/change_chat", "Change to a different chat (use number or ID)", requires_arguments=True),
    Command("/permission", "Toggle permission mode between auto and read-only"),
    Command("/change_token", "Update authentication token and recreate client", requires_arguments=True),
    Command("/me", "Display current user info (id, name, email)"),
    Command("/remove_chat", "Remove a specific chat by ID or index", requires_arguments=True),
    Command("/remove_chats", "Remove all chats except the current one"),
    Command("/mode", "Toggle mode between normal and thinking"),
    Command("/current_chat", "Display current chat ID, parent message ID, and title"),
    Command("/upload_chat", "Upload a cached chat by index or chat_id (e.g., /upload_chat 1 or /upload_chat <chat_id>)", requires_arguments=True),
    Command("/attach", "Attach an image to the next message (e.g., /attach path/to/image.png)", requires_arguments=True),
    Command("/attachments", "List currently pending image attachments"),
    Command("/clear_attachments", "Remove all pending image attachments"),
    Command("/tokens", "Display the number of available tokens"),
    Command("/cached_chats", "List all locally cached chats"),
    Command("/add_token", "Append a token to the tokens list", requires_arguments=True),
]
