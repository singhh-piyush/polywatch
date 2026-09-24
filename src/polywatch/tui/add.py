"""Add-trader modal: pin a trader by wallet address, profile URL or username."""
from __future__ import annotations

import re
from typing import Any

from rich.text import Text
from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Label, OptionList, Static

from ..fmt import short_wallet

WALLET_RE = re.compile(r"0x[0-9a-fA-F]{40}")


def parse_trader_ref(text: str) -> tuple[str, str]:
    text = text.strip()
    if match := WALLET_RE.search(text):
        return "wallet", match.group(0).lower()
    return "name", text.rstrip("/").rsplit("/", 1)[-1].lstrip("@")


class AddTrader(ModalScreen[tuple[str, str] | None]):
    DEFAULT_CSS = """
    AddTrader { align: center middle; }
    #add { width: 70; height: auto; border: thick $primary; background: $surface; padding: 1 2; }
    #matches { height: auto; max-height: 8; display: none; }
    """
    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, gamma: Any, prompt: str = "Pin a trader: wallet address, profile URL or username") -> None:
        super().__init__()
        self._gamma = gamma
        self._prompt = prompt
        self._matches: list[tuple[str, str]] = []

    def compose(self) -> ComposeResult:
        with Vertical(id="add"):
            yield Label(self._prompt)
            yield Input(placeholder="0x…  /  polymarket.com/@name  /  name", id="ref")
            yield OptionList(id="matches")
            yield Static("", id="message")

    def on_mount(self) -> None:
        self.query_one(Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        kind, value = parse_trader_ref(event.value)
        if not value:
            return
        if kind == "wallet":
            self.dismiss((value, ""))
        else:
            self.search(value)

    @work(exclusive=True, exit_on_error=False)
    async def search(self, name: str) -> None:
        message = self.query_one("#message", Static)
        message.update(Text(f"Searching for {name}…"))
        try:
            matches = await self._gamma.search_profiles(name)
        except Exception as exc:
            message.update(Text(f"Search failed: {exc}"))
            return
        if not matches:
            message.update(Text(f"No trader named {name!r}"))
            return
        self._matches = matches
        options = self.query_one("#matches", OptionList)
        options.clear_options()
        options.add_options([Text(f"{n or '(no name)'}  {short_wallet(w)}") for n, w in matches])
        options.display = True
        options.highlighted = 0
        options.focus()
        message.update(Text("Pick a trader and press Enter"))

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        name, wallet = self._matches[event.option_index]
        self.dismiss((wallet, name))

    def action_cancel(self) -> None:
        self.dismiss(None)
