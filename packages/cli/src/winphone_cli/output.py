from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

import typer
from pydantic import BaseModel
from rich.console import Console
from rich.table import Table
from winphone_core.errors import WinphoneError


def to_jsonable(data: Any) -> Any:
    if isinstance(data, BaseModel):
        return data.model_dump(mode="json")
    if isinstance(data, list):
        return [to_jsonable(x) for x in data]
    return data


def emit_json(data: Any) -> None:
    typer.echo(json.dumps(to_jsonable(data), indent=2))


def table(columns: Sequence[str], rows: Sequence[Sequence[Any]]) -> None:
    t = Table(*columns, box=None, header_style="bold")
    for row in rows:
        t.add_row(*("" if v is None else str(v) for v in row))
    Console().print(t)


def say(text: str) -> None:
    Console().print(text, highlight=False)


def error(e: WinphoneError, as_json: bool) -> None:
    if as_json:
        emit_json(e.to_dict())
        return
    err = Console(stderr=True)
    err.print(f"[red]error:[/red] {e.message}", highlight=False)
    if e.hint:
        err.print(f"hint: {e.hint}", highlight=False)
