"""The TUI's look, in three flavours picked with `/theme` and remembered in
~/.omega/ui.json.

`system` (the default) paints no colours of its own: every surface is the
terminal's own background, text is its foreground, and the accents are its
16-colour palette, so omega looks like any other program in that terminal --
light in a light terminal, dark in a dark one, and always in the user's own
palette. `light` and `dark` are Textual's painted palettes for anyone whose
terminal disagrees with how they want omega to look."""
from __future__ import annotations

from textual.theme import Theme

CHOICES = ("system", "light", "dark")
DEFAULT = "system"

SYSTEM_THEME = Theme(
    name="omega-system",
    ansi=True,
    primary="ansi_blue",
    secondary="ansi_cyan",
    warning="ansi_yellow",
    error="ansi_red",
    success="ansi_green",
    accent="ansi_blue",
    foreground="ansi_default",
    background="ansi_default",
    surface="ansi_default",
    panel="ansi_default",
    boost="ansi_default",
    dark=True,
    variables={
        "ansi-background": "ansi_default",
        "ansi-foreground": "ansi_default",
        "border": "ansi_blue",
        "border-blurred": "ansi_bright_black",
        "block-cursor-foreground": "ansi_bright_white",
        "block-cursor-background": "ansi_blue",
        "block-cursor-blurred-foreground": "ansi_default",
        "block-cursor-blurred-background": "ansi_bright_black",
        "input-cursor-background": "ansi_default",
        "input-cursor-foreground": "ansi_default",
        "input-cursor-text-style": "reverse",
        "input-selection-background": "ansi_blue",
        "input-selection-foreground": "ansi_bright_white",
        "screen-selection-background": "ansi_blue",
        "screen-selection-foreground": "ansi_bright_white",
        "scrollbar": "ansi_bright_black",
        "scrollbar-hover": "ansi_bright_black",
        "scrollbar-active": "ansi_blue",
        "scrollbar-background": "ansi_default",
        "scrollbar-background-hover": "ansi_default",
        "scrollbar-background-active": "ansi_default",
        "scrollbar-corner-color": "ansi_default",
    },
)

_TEXTUAL_THEME = {"system": SYSTEM_THEME.name, "light": "textual-light", "dark": "textual-dark"}

# Hairlines: the sidebar divider and the idle input border. The system
# flavour uses the palette's "bright black" (the grey every terminal reserves
# for exactly this); the painted ones use a faint foreground tint that reads
# the same on either background.
_RULE = {"system": "ansi_bright_black", "light": "#00000026", "dark": "#ffffff26"}

# Pygments styles for rich's `Markdown` code fences and `Syntax` blocks. The
# system flavour's `ansi_dark` is rich's palette-only style: no painted block
# background, so a code fence sits flat on the terminal like everything else.
_CODE = {"system": "ansi_dark", "light": "default", "dark": "monokai"}


def normalize(choice: object) -> str:
    return choice if isinstance(choice, str) and choice in CHOICES else DEFAULT


def textual_theme(choice: str) -> str:
    return _TEXTUAL_THEME[normalize(choice)]


def rule_color(choice: str) -> str:
    return _RULE[normalize(choice)]


def code_theme(choice: str) -> str:
    return _CODE[normalize(choice)]


def current_code_theme(app: object) -> str:
    """The code theme for whichever app a widget is mounted in -- the
    onboarding wizard and any test harness app fall back to the system look."""
    return code_theme(str(getattr(app, "theme_choice", DEFAULT)))
