"""Syntax highlighter for code display with VSCode-like colors."""
from pygments import highlight
from pygments.lexers import get_lexer_for_filename, TextLexer
from pygments.token import Token
from rich.text import Text
import os


class SyntaxHighlighter:
    """Syntax highlighter for code display in terminal with VSCode-like colors."""

    # VSCode Dark+ color scheme
    COLORS = {
        Token.Keyword: '#569CD6',           # Blue - keywords (if, for, while, etc.)
        Token.Keyword.Type: '#4EC9B0',      # Teal - type keywords (int, str, etc.)
        Token.Keyword.Constant: '#569CD6',  # Blue - constants (True, False, None)
        Token.Name.Class: '#4EC9B0',        # Teal - class names
        Token.Name.Function: '#DCDCAA',     # Yellow - function names
        Token.Name.Decorator: '#DCDCAA',    # Yellow - decorators (@)
        Token.Name.Builtin: '#4FC1FF',      # Light blue - builtins (print, len, etc.)
        Token.Name.Builtin.Pseudo: '#569CD6', # Blue - self, cls
        Token.String: '#CE9178',            # Orange - strings
        Token.String.Doc: '#6A9955',        # Green - docstrings
        Token.String.Escape: '#D7BA7D',     # Light orange - escape sequences
        Token.Number: '#B5CEA8',            # Light green - numbers
        Token.Number.Integer: '#B5CEA8',    # Light green - integers
        Token.Number.Float: '#B5CEA8',      # Light green - floats
        Token.Comment: '#6A9955',           # Green - comments
        Token.Comment.Single: '#6A9955',    # Green - single line comments
        Token.Comment.Multiline: '#6A9955', # Green - multi-line comments
        Token.Operator: '#D4D4D4',          # Light gray - operators
        Token.Operator.Word: '#569CD6',     # Blue - word operators (and, or, not)
        Token.Punctuation: '#D4D4D4',       # Light gray - punctuation
        Token.Name: '#9CDCFE',              # Light blue - variables
        Token.Name.Tag: '#569CD6',          # Blue - HTML tags
        Token.Name.Attribute: '#9CDCFE',    # Light blue - attributes
        Token.Name.Entity: '#B5CEA8',       # Light green - entities
        'default': '#D4D4D4',               # Light gray - default text
    }

    @staticmethod
    def get_lexer(filepath):
        """Get appropriate lexer for file based on extension."""
        try:
            return get_lexer_for_filename(filepath)
        except Exception:
            return TextLexer()

    @staticmethod
    def colorize_line(line, lexer):
        """Colorize a single line of code using Rich Text."""
        text = Text()
        # Tokenize the line
        tokens = list(lexer.get_tokens(line))
        for token_type, value in tokens:
            if not value:
                continue
            # Get color for this token type
            color = None
            # Try exact match first
            if token_type in SyntaxHighlighter.COLORS:
                color = SyntaxHighlighter.COLORS[token_type]
            else:
                # Try parent token types
                current_type = token_type
                while current_type and not color:
                    if current_type in SyntaxHighlighter.COLORS:
                        color = SyntaxHighlighter.COLORS[current_type]
                        break
                    # Move to parent token type
                    if hasattr(current_type, 'parent'):
                        current_type = current_type.parent
                    else:
                        break
            # Use default color if no match
            if not color:
                color = SyntaxHighlighter.COLORS['default']
            # Append colored text
            text.append(value, style=color)
        return text

    @staticmethod
    def highlight_code(code, filepath):
        """Highlight code and return list of Rich Text objects.

        Tokenizes the entire code block at once to preserve multi-line
        construct state (e.g., multi-line comments, strings, docstrings),
        then groups tokens into per-line Rich Text objects.
        """
        lexer = SyntaxHighlighter.get_lexer(filepath)
        # Tokenize the full code block to maintain lexer state across lines
        tokens = list(lexer.get_tokens(code))

        # Group tokens into lines
        highlighted_lines = []
        current_line = Text()

        for token_type, value in tokens:
            if not value:
                continue

            # Get color for this token type
            color = None
            if token_type in SyntaxHighlighter.COLORS:
                color = SyntaxHighlighter.COLORS[token_type]
            else:
                current_type = token_type
                while current_type and not color:
                    if current_type in SyntaxHighlighter.COLORS:
                        color = SyntaxHighlighter.COLORS[current_type]
                        break
                    if hasattr(current_type, 'parent'):
                        current_type = current_type.parent
                    else:
                        break

            if not color:
                color = SyntaxHighlighter.COLORS['default']

            # Split token value by newlines and distribute across lines
            parts = value.split('\n')
            for i, part in enumerate(parts):
                if part:
                    current_line.append(part, style=color)
                if i < len(parts) - 1:
                    highlighted_lines.append(current_line)
                    current_line = Text()

        # Don't forget the last line
        highlighted_lines.append(current_line)

        # Remove trailing empty line from trailing newline in get_tokens
        if highlighted_lines and not highlighted_lines[-1].plain.strip():
            highlighted_lines.pop()

        return highlighted_lines
