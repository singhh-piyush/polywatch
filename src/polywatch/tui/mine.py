"""My trades: the user's own positions and how they are doing."""
from __future__ import annotations

from typing import Any

from .common import TextList


class MyTrades(TextList):
    DEFAULT_CSS = """
    MyTrades > TextRow { padding: 0 1 1 1; }
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__("My trades", empty="no open positions", **kwargs)
