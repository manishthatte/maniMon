"""
Pango markup helpers shared by every panel section.

One key-and-value idiom, defined once. Both panels had their own copy.
"""

from .window import DIM, WHITE, FS, FXS


def kv(k, v, vcol=WHITE, kf=None, vf=None):
    """A dim label followed by a bright value — the panels' basic row."""
    return (f'<span font="{kf or FXS}" foreground="{DIM}">{k}</span>'
            f'<span font="{vf or FS}" foreground="{vcol}">  {v}</span>')
