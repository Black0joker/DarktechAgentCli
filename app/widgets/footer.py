from textual.widgets import Static

class CustomFooter(Static):
    """Footer with metadata."""
    DEFAULT_CSS = """
    CustomFooter {
        color: #8b949e;
        background: #0d1117;
    }
    """
    
    def render(self) -> str:
        state = self.app.state if hasattr(self.app, 'state') else None
        agent = self.app.agent if hasattr(self.app, 'agent') else None
        
        # Defaults must match AgentWrapper.__init__ (mode="thinking", permission_mode="auto").
        # Read from the agent as soon as it exists - do NOT wait for agent.client,
        # because mode/permission_mode are set in the wrapper constructor before the
        # client is created asynchronously. Waiting for the client makes the first
        # render fall back to the wrong hardcoded values until a repaint (e.g. hover).
        permission_mode = "auto"
        mode = "thinking"
        if agent:
            permission_mode = getattr(agent, 'permission_mode', 'auto')
            mode = getattr(agent, 'mode', 'thinking')
        
        if state:
            parts = [f"[dim]Workspace:[/dim] {state.workspace}"]
            # Only show Branch field if a git repo was detected
            if state.branch is not None:
                parts.append(f"[dim]Branch:[/dim] {state.branch}")
            parts.append(f"[dim]Model:[/dim] {state.model}")
            parts.append(f"[dim]Mode:[/dim] {mode}")
            parts.append(f"[dim]Permission:[/dim] {permission_mode}")
            return "  ".join(parts)
        parts = ["[dim]Workspace:[/dim] .", "[dim]Model:[/dim] Darktech3.8-Max", f"[dim]Mode:[/dim] {mode}", f"[dim]Permission:[/dim] {permission_mode}"]
        return "  ".join(parts)
