"""The Textual onboarding wizard: one Screen per step, pushed forward as the
user completes each one. All provider/catalog/config logic lives in
`omega/onboarding.py`; this module only renders it and reads the answers back
into `OnboardingApp`'s state."""
from __future__ import annotations

import asyncio
import os
import time
from typing import Any, cast

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option

from ... import config, events, loop, onboarding
from .. import format
from .status import SPINNER_FRAMES

Body = dict[str, Any]

TOTAL_STEPS = 6


class StepIndicator(Static):
    def __init__(self, step: int) -> None:
        dots = " ".join("●" if i < step else "○" for i in range(TOTAL_STEPS))
        super().__init__(f"[dim]{dots}[/dim]")


class _Spinner(Static):
    """The part-A spinner (same frames/tempo as the status bar), reused here
    for key validation and the live probe turn."""

    def __init__(self, label: str) -> None:
        super().__init__("")
        self._label = label
        self._frame = 0
        self._stopped = False
        self._timer: Any = None

    def on_mount(self) -> None:
        self._tick()
        if not self._stopped:
            self._timer = self.set_interval(0.08, self._tick)

    def _tick(self) -> None:
        if self._stopped:
            return
        glyph = SPINNER_FRAMES[self._frame % len(SPINNER_FRAMES)]
        self._frame += 1
        self.update(f"[dim]{glyph} {self._label}[/dim]")

    def stop(self, final: str = "") -> None:
        self._stopped = True
        if self._timer is not None:
            self._timer.stop()
        self.update(final)


def _base_screen_css(title: str) -> str:
    return f"""
    {title} {{ align: center middle; }}
    {title} #panel {{ width: 76; height: auto; padding: 1 2; }}
    """


class WizardScreen(Screen[None]):
    """Shared escape-to-quit behavior for every step."""

    BINDINGS = [Binding("escape", "quit_wizard", show=False)]

    def action_quit_wizard(self) -> None:
        cast(OnboardingApp, self.app).exit(False)


class WelcomeScreen(WizardScreen):
    DEFAULT_CSS = _base_screen_css("WelcomeScreen")
    BINDINGS = [*WizardScreen.BINDINGS, Binding("enter", "continue_step", show=False),
               Binding("q", "quit_wizard", show=False)]

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="panel"):
            yield StepIndicator(1)
            yield Static("[bold]omega[/bold]")
            yield Static("A fast, small coding agent for your terminal. Bring your own models.")
            yield Static("Three quick steps: provider → key → model. "
                         "Then we run one real turn to prove it works.")
            yield Static("[dim]enter to continue · q/esc to quit[/dim]")

    def action_continue_step(self) -> None:
        self.app.push_screen(ProviderScreen())


class ProviderScreen(WizardScreen):
    DEFAULT_CSS = _base_screen_css("ProviderScreen")

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="panel"):
            yield StepIndicator(2)
            yield Static("[bold]Provider[/bold]")
            yield OptionList(*self._options(), id="providers")
            yield Static("[red]base URL must start with http:// or https://[/red]", id="url-error")
            yield Input(placeholder="https://api.example.com/v1", id="base-url")

    def on_mount(self) -> None:
        self.query_one("#url-error", Static).display = False
        self.query_one("#base-url", Input).display = False
        options = self.query_one("#providers", OptionList)
        options.focus()
        options.highlighted = self._env_preselect()

    def _env_preselect(self) -> int:
        for i, (_key, _label, _desc, env) in enumerate(onboarding.PROVIDER_INFO):
            if env and os.environ.get(env):
                return i
        return 0

    def _options(self) -> list[Option]:
        rendered = []
        for key, label, desc, env in onboarding.PROVIDER_INFO:
            found = "  [green]· key found in environment[/green]" if env and os.environ.get(env) else ""
            rendered.append(Option(f"{label}{found}\n[dim]{desc}[/dim]", id=key))
        return rendered

    def on_option_list_option_selected(self, message: OptionList.OptionSelected) -> None:
        key = message.option_id
        if key == "other":
            self.query_one("#base-url", Input).display = True
            self.query_one("#base-url", Input).focus()
            return
        app = cast(OnboardingApp, self.app)
        app.choice = onboarding.choice_for(key or "anthropic")
        app.push_screen(KeyScreen())

    def on_input_submitted(self, message: Input.Submitted) -> None:
        url = message.value.strip()
        if not (url.startswith("http://") or url.startswith("https://")):
            self.query_one("#url-error", Static).display = True
            return
        app = cast(OnboardingApp, self.app)
        app.choice = onboarding.choice_for("other", url)
        app.push_screen(KeyScreen())


class KeyScreen(WizardScreen):
    DEFAULT_CSS = _base_screen_css("KeyScreen")

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="panel"):
            yield StepIndicator(3)
            yield Static("[bold]API key[/bold]")
            yield Static("", id="key-hint")
            yield Input(password=True, id="key-input")
            yield Static("", id="key-result")

    def on_mount(self) -> None:
        app = cast(OnboardingApp, self.app)
        choice = app.choice
        assert choice is not None
        env = onboarding.env_var_for(choice.provider_key)
        env_val = os.environ.get(env) if env else ""
        self._env_key = env_val or None
        if self._env_key:
            self.query_one("#key-hint", Static).update(
                f"[dim]•••• from ${env} — enter to use, or paste a different one[/dim]")
        self.query_one("#key-input", Input).focus()

    def on_input_submitted(self, message: Input.Submitted) -> None:
        key = message.value.strip() or (self._env_key or "")
        if not key:
            self.query_one("#key-result", Static).update("[red]enter a key[/red]")
            return
        self.query_one("#key-result", Static).update("")
        self.run_worker(self._validate(key), exclusive=True, thread=False)

    async def _validate(self, key: str) -> None:
        app = cast(OnboardingApp, self.app)
        result = self.query_one("#key-result", Static)
        spinner = _Spinner("waiting")
        await result.remove()
        panel = self.query_one("#panel", VerticalScroll)
        panel.mount(spinner)
        assert app.choice is not None
        ok, msg = await onboarding.validate_key(app.choice, key)
        if ok:
            spinner.stop("[green]✓ connected[/green]")
            app.choice.provider["apiKey"] = key
            app.push_screen(ModelScreen())
        else:
            spinner.stop(f"[red]✗ {msg[:120]}[/red]")
            panel.mount(Static("", id="key-result"))


class ModelScreen(WizardScreen):
    DEFAULT_CSS = _base_screen_css("ModelScreen")

    def compose(self) -> ComposeResult:
        app = cast(OnboardingApp, self.app)
        choice = app.choice
        assert choice is not None
        with VerticalScroll(id="panel"):
            yield StepIndicator(4)
            yield Static("[bold]Model[/bold]")
            if choice.catalog:
                yield OptionList(*self._options(choice), id="models")
                yield Static(f"[dim]Background roles (subagents, compaction, memory) will use "
                            f"{choice.cheap_alias} automatically — change any of this later "
                            f"with /model or `omega setup`.[/dim]")
            else:
                yield Input(placeholder="model id served by this endpoint", id="model-id")

    def on_mount(self) -> None:
        app = cast(OnboardingApp, self.app)
        choice = app.choice
        assert choice is not None
        if choice.catalog:
            options = self.query_one("#models", OptionList)
            options.focus()
            aliases = sorted(choice.catalog)
            options.highlighted = aliases.index(choice.default_alias)
        else:
            self.query_one("#model-id", Input).focus()

    def _options(self, choice: onboarding._Choice) -> list[Option]:
        rendered = []
        for alias in sorted(choice.catalog):
            m = choice.catalog[alias]
            price_in, price_out = onboarding.PRICES.get(alias, (0.0, 0.0))
            purpose = onboarding.PURPOSES.get(alias, "")
            text = (f"{alias:<8} {m['model']:<26} {m['context']:>10,}  "
                   f"${price_in:g}/${price_out:g} per M\n[dim]{purpose}[/dim]")
            rendered.append(Option(text, id=alias))
        return rendered

    def on_option_list_option_selected(self, message: OptionList.OptionSelected) -> None:
        app = cast(OnboardingApp, self.app)
        assert app.choice is not None
        app.main_alias = message.option_id or app.choice.default_alias
        app.cheap_alias = app.choice.cheap_alias
        app.models = dict(app.choice.catalog)
        app.push_screen(ProveScreen())

    def on_input_submitted(self, message: Input.Submitted) -> None:
        model_id = message.value.strip()
        if not model_id:
            return
        app = cast(OnboardingApp, self.app)
        assert app.choice is not None
        app.main_alias = app.cheap_alias = "main-model"
        app.models = {"main-model": {"model": model_id, "provider": app.choice.provider_key,
                                     "context": 128000}}
        app.push_screen(ProveScreen())


class ProveScreen(WizardScreen):
    DEFAULT_CSS = _base_screen_css("ProveScreen") + """
    ProveScreen #panel { width: 90; }
    ProveScreen #prove-log { height: auto; max-height: 16; }
    """
    BINDINGS = [*WizardScreen.BINDINGS, Binding("r", "retry", show=False),
               Binding("enter", "continue_anyway", show=False)]

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="panel"):
            yield StepIndicator(5)
            yield Static("[bold]Prove it[/bold]")
            yield VerticalScroll(id="prove-log")
            yield Static("", id="prove-hint")

    def on_mount(self) -> None:
        self._start()

    def _start(self) -> None:
        self.query_one("#prove-hint", Static).update("")
        log = self.query_one("#prove-log", VerticalScroll)
        log.remove_children()
        app = cast(OnboardingApp, self.app)
        assert app.choice is not None
        raw = onboarding.build_config(app.choice, app.main_alias, app.cheap_alias, app.models)
        onboarding.save_config(raw)
        self.run_worker(self._probe(), exclusive=True, thread=False)

    async def _probe(self) -> None:
        log = self.query_one("#prove-log", VerticalScroll)
        spinner = _Spinner("waiting")
        log.mount(spinner)
        started = time.monotonic()
        tool_calls = 0
        tokens_used = 0
        state: dict[str, Any] = {"text_widget": None, "live_text": ""}

        def emit(ev: events.Event) -> None:
            nonlocal tool_calls, tokens_used
            if isinstance(ev, events.Phase):
                spinner._label = ev.state
            elif isinstance(ev, events.ToolStart):
                tool_calls += 1
                log.mount(Static(format.tool_start(ev)))
            elif isinstance(ev, events.TextDelta):
                if state["text_widget"] is None:
                    widget = Static("")
                    log.mount(widget)
                    state["text_widget"] = widget
                state["live_text"] += ev.text
                cast(Static, state["text_widget"]).update(cast(str, state["live_text"]))
            elif isinstance(ev, events.Usage):
                tokens_used = ev.used

        try:
            cfg = config.load()
            history: list[dict[str, Any]] = [{"role": "user", "content": onboarding.PROBE_PROMPT}]
            await loop.run_agent(cfg, "main", loop.BUILD_SYSTEM, history, emit=emit)
            elapsed = time.monotonic() - started
            spinner.stop(f"[green]✓ working — {tool_calls} tool calls · "
                        f"{tokens_used} tokens · {elapsed:.0f}s[/green]")
            await asyncio.sleep(0.6)
            cast(OnboardingApp, self.app).push_screen(DoneScreen())
        except Exception as e:
            spinner.stop(f"[red]✗ {type(e).__name__}: {e}[/red]"[:200])
            self.query_one("#prove-hint", Static).update(
                "[dim]config is saved; press r to retry or enter to continue anyway[/dim]")

    def action_retry(self) -> None:
        self._start()

    def action_continue_anyway(self) -> None:
        cast(OnboardingApp, self.app).push_screen(DoneScreen())


class DoneScreen(WizardScreen):
    DEFAULT_CSS = _base_screen_css("DoneScreen")
    BINDINGS = [*WizardScreen.BINDINGS, Binding("enter", "finish", show=False)]

    CHEAT_SHEET = (
        "omega                    open the TUI\n"
        "omega \"fix the test\"     one-shot\n"
        "/model  or ctrl+o      switch models          /plan · /build   switch modes\n"
        "omega setup              full browser setup: more providers, roles, MCP servers")

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="panel"):
            yield StepIndicator(6)
            yield Static("[bold]You're set up[/bold]")
            yield Static(self.CHEAT_SHEET)
            yield Static("[dim]enter to start[/dim]")

    def action_finish(self) -> None:
        cast(OnboardingApp, self.app).exit(True)


class OnboardingApp(App[bool]):
    def __init__(self) -> None:
        super().__init__()
        self.choice: onboarding._Choice | None = None
        self.main_alias = ""
        self.cheap_alias = ""
        self.models: Body = {}

    def on_mount(self) -> None:
        self.push_screen(WelcomeScreen())
