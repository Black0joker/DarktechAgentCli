'''Logo widget for the terminal UI.'''

from textual.widgets import Static
from rich.text import Text


class Logo(Static):
    '''A widget that displays a terminal logo with a gradient from blue to purple.
    
    The logo is inspired by the Gemini CLI logo and uses Unicode block characters
    to create a modern, minimal, premium visual identity.
    '''
    
    def render(self) -> Text:
        '''Render the logo with a gradient from #58a6ff to #bc8cff (Modern Dark Theme).'''
        # Asymmetrical arrow/ribbon design built with block characters
        # Width ranges from 3 to 8 characters, height is 7 lines
        lines = [
            "[#58a6ff]   ▄▄▄[/]",
            "[#6b9eff]  ▄████▄[/]",
            "[#7e96ff] ▄██████▄[/]",
            "[#918eff]████████[/]",
            "[#a486ff] ██████▀[/]",
            "[#b080ff]  ████▀[/]",
            "[#bc8cff]   ██▀[/]"
        ]
        return Text.from_markup("\n".join(lines))
