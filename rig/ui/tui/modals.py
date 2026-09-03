"""Modal screens: the permission-confirm prompt and the ask_user dialog."""
from __future__ import annotations

from typing import Any

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Button, Input, OptionList, Static
from textual.widgets.option_list import Option

from ... import events, permissions

_DIALOG_CSS = """
Vertical#dialog {
    width: 70%;
    max-width: 90;
    height: auto;
    max-height: 80%;
    padding: 1 2;
    border: round $accent;
    background: $surface;
}
"""


class ConfirmScreen(ModalScreen[bool]):
    """Mirrors `ui.plain.confirm`: y/n/a, default deny."""

    DEFAULT_CSS = _DIALOG_CSS + """
    ConfirmScreen { align: center middle; }
    ConfirmScreen #buttons { margin-top: 1; height: 3; align: center middle; }
    ConfirmScreen #buttons Button { margin: 0 1; }
    """
    BINDINGS = [
        Binding("y", "answer(True)", "Allow", show=True),
        Binding("n", "answer(False)", "Deny", show=True),
        Binding("a", "answer_always", "Always", show=True),
        Binding("escape", "answer(False)", "Deny", show=False),
    ]

    def __init__(self, name: str, args: dict[str, Any], why: str) -> None:
        super().__init__()
        self.tool_name = name
        self.tool_args = args
        self.why = why
        self.detail = str(args.get("command") or args.get("path") or args.get("name") or "")[:200]

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static(f"[yellow]⏸  {self.tool_name}[/yellow] [dim]{self.why}[/dim]")
            if self.detail:
                yield Static(f"[bold]{self.detail}[/bold]")
            with Horizontal(id="buttons"):
                yield Button("Allow (y)", id="yes", variant="success")
                yield Button("Deny (n)", id="no", variant="error")
                yield Button("Always (a)", id="always", variant="primary")

    def action_answer(self, allow: bool) -> None:
        self.dismiss(allow)

    def action_answer_always(self) -> None:
        permissions.remember(permissions.rule_for(self.tool_name, self.tool_args), permissions.ALLOW)
        self.dismiss(True)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "yes":
            self.action_answer(True)
        elif event.button.id == "no":
            self.action_answer(False)
        elif event.button.id == "always":
            self.action_answer_always()


class OptionToggled(Message):
    """A row in an `_AskOptionList` was toggled with space (multi_select only)."""

    def __init__(self, index: int) -> None:
        self.index = index
        super().__init__()


class _AskOptionList(OptionList):
    BINDINGS = [*OptionList.BINDINGS, Binding("space", "toggle_option", show=False)]

    def action_toggle_option(self) -> None:
        if self.highlighted is not None:
            self.post_message(OptionToggled(self.highlighted))


class AskUserScreen(ModalScreen[str]):
    """Mirrors `ui.plain.ask_user`'s contract: returns the selected label(s)
    joined by ", ", free text, or "(no answer)"."""

    DEFAULT_CSS = _DIALOG_CSS + """
    AskUserScreen { align: center middle; }
    AskUserScreen #options { height: auto; max-height: 12; margin-top: 1; }
    AskUserScreen #freetext { margin-top: 1; }
    """
    BINDINGS = [Binding("escape", "cancel", show=False)]

    def __init__(self, question: str, options: list[events.Option], multi_select: bool) -> None:
        super().__init__()
        self.question = question
        self.options = options
        self.multi_select = multi_select
        self.selected: set[int] = set()

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static(f"[yellow]?[/yellow] {self.question}")
            yield _AskOptionList(*self._render_options(), id="options")
            yield Input(placeholder="or type a free-text answer…", id="freetext")

    def on_mount(self) -> None:
        if self.options:
            self.query_one("#options", _AskOptionList).focus()
        else:
            self.query_one("#freetext", Input).focus()

    def _render_options(self) -> list[Option]:
        rendered = []
        for i, opt in enumerate(self.options):
            label = opt.get("label", "")
            desc = opt.get("description", "")
            mark = "✓ " if i in self.selected else "  "
            text = f"{mark}{label}" + (f" — {desc}" if desc else "")
            rendered.append(Option(text, id=f"opt-{i}"))
        return rendered

    def _refresh_marks(self) -> None:
        options = self.query_one("#options", _AskOptionList)
        for i in range(len(self.options)):
            label = self.options[i].get("label", "")
            desc = self.options[i].get("description", "")
            mark = "✓ " if i in self.selected else "  "
            text = f"{mark}{label}" + (f" — {desc}" if desc else "")
            options.replace_option_prompt(f"opt-{i}", text)

    def on_option_toggled(self, message: OptionToggled) -> None:
        if not self.multi_select:
            return
        if message.index in self.selected:
            self.selected.discard(message.index)
        else:
            self.selected.add(message.index)
        self._refresh_marks()

    def on_option_list_option_selected(self, message: OptionList.OptionSelected) -> None:
        if self.multi_select and self.selected:
            labels = [self.options[i].get("label", "") for i in sorted(self.selected)]
            self.dismiss(", ".join(labels))
        else:
            self.dismiss(self.options[message.option_index].get("label", ""))

    def on_input_submitted(self, message: Input.Submitted) -> None:
        self.dismiss(message.value or "(no answer)")

    def action_cancel(self) -> None:
        self.dismiss("(no answer)")
