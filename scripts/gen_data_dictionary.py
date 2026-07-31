"""Generate the data dictionary page from the API's response models.

Hand-written data dictionaries are correct on the day they are written and
wrong two model changes later — and a wrong dictionary is worse than none,
because people trust it.

This reads the Pydantic models the API actually returns, so the page cannot
describe a field that does not exist or miss one that does. Prose lives in
Field(description=...) beside the definition, where it gets updated when the
field does.

Run from the repo root:
    uv run python scripts/gen_data_dictionary.py

Writes docs/src/content/docs/data/dictionary.md. Run it in CI alongside the
OpenAPI export so the page cannot drift.
"""

from __future__ import annotations

import datetime
import enum
import types
import typing
from pathlib import Path

from fpl_api.schemas.fixtures import Fixture
from fpl_api.schemas.gameweeks import Gameweek
from fpl_api.schemas.players import Player, PlayerPage
from fpl_api.schemas.teams import Team
from pydantic import BaseModel
from pydantic.fields import FieldInfo

OUT = Path("docs/src/content/docs/data/dictionary.md")

MODELS: list[tuple[type[BaseModel], str, str]] = [
    (Team, "Team", "`GET /v1/teams`, `GET /v1/teams/{team_code}`"),
    (Gameweek, "Gameweek", "`GET /v1/gameweeks`, `/current`, `/next`, `/{gameweek}`"),
    (Player, "Player", "`GET /v1/players`, `GET /v1/players/{player_id}`"),
    (PlayerPage, "Player page", "`GET /v1/players` — the pagination envelope"),
    (Fixture, "Fixture", "`GET /v1/fixtures`, `GET /v1/fixtures/{match_id}`"),
]


PREAMBLE = """---
title: Field reference
description: Every field on every response — what it means, its type, and what null means.
sidebar:
  order: 3
---

Generated from the response models the API actually returns.

Field descriptions are maintained beside the Pydantic definitions and
generated into this reference automatically. If a field exists in the API,
it appears here.

## Conventions

A few patterns run through every response. Knowing them makes most fields
self-explanatory.

### Identifiers

| Suffix | Scope | Use for |
|---|---|---|
| `_code` | **Permanent** — survives seasons | Anything historical |
| `_id` | **One season** — reassigned each August | Requests within a single season |

:::caution[Do not join on IDs across seasons]
`player_id` and `team_id` are reassigned each season. Use `player_code` and
`team_code` for historical joins.
:::

### The `gw_` prefix

On per-fixture data, columns prefixed `gw_` hold gameweek-level values.
They are populated on the first fixture of a gameweek only and null on later
fixtures, preventing double counting in double gameweeks.

Everything else describes fixture grain.

### Money

Prices are returned as decimal millions: `10.1` means £10.1m. Internally they
are stored as integer tenths, so rounding error does not accumulate.

### Times

Every timestamp is UTC and ISO 8601 with an explicit offset.

### Null semantics

Null is rarely "missing". Usually it carries information:

| Field | Null means |
|---|---|
| `opponent_code` | Opposition is not a Premier League club |
| `days_since_last_match` | Kickoff time is unknown, or this is the first match |
| `chance_of_playing_next` | No availability flag; player is presumed available |
| `result`, `goals_for` | Fixture has not been played |

The field description defines ambiguous cases.

### Provenance

Values come from:

- **Our captures** — price, ownership, injury news, availability.
- **Core Insights** — match statistics, xG, defensive actions, Elo.
- **Derived** — calculated values such as rest days and season totals.

Season totals inherit the known [~2% coverage gap](/data/quality/), so they
will not always match FPL's official totals exactly.

"""


def _escape_md(value: str) -> str:
    """Escape markdown table characters."""
    return value.replace("|", "\\|")


def _type_name(annotation: object) -> str:
    """Human-readable type."""
    origin = typing.get_origin(annotation)

    if origin in (typing.Union, types.UnionType):
        args = typing.get_args(annotation)
        inner = [a for a in args if a is not type(None)]

        if len(inner) == 1:
            return _type_name(inner[0])

        return " or ".join(_type_name(a) for a in inner)

    if origin in (list, list):
        (item,) = typing.get_args(annotation) or (object,)
        return f"array of {_type_name(item)}"

    if origin is typing.Literal:
        values = typing.get_args(annotation)
        return "enum: " + " | ".join(str(v) for v in values)

    if origin is typing.Annotated:
        return _type_name(typing.get_args(annotation)[0])

    if isinstance(annotation, type):
        if issubclass(annotation, BaseModel):
            return annotation.__name__

        if issubclass(annotation, enum.Enum):
            return "enum"

        return {
            int: "integer",
            float: "number",
            str: "string",
            bool: "boolean",
            datetime.datetime: "datetime",
        }.get(annotation, annotation.__name__)

    return getattr(annotation, "__name__", str(annotation))


def _is_nullable(annotation: object) -> bool:
    return type(None) in typing.get_args(annotation)


def _anchor(model: type[BaseModel], field: str) -> str:
    return f"{model.__name__.lower()}-{field}"


def _describe(name: str, field: FieldInfo) -> str:
    if field.description:
        return field.description

    return f"_No description. Add `Field(description=...)` to `{name}`._"


def _field_index() -> str:
    lines = ["\n## Field index\n"]

    for model, heading, _ in MODELS:
        lines.append(f"\n### {heading}\n")

        for name in model.model_fields:
            lines.append(f"- [`{name}`](#{_anchor(model, name)})")

    return "\n".join(lines)


def _table(model: type[BaseModel]) -> str:
    rows = [
        "| Field | Type | Nullable | Description |",
        "|---|---|---|---|",
    ]

    for name, field in model.model_fields.items():
        description = _escape_md(_describe(name, field))

        field_name = f'<a id="{_anchor(model, name)}"></a>`{name}`'

        rows.append(
            "| {} | {} | {} | {} |".format(
                field_name,
                _type_name(field.annotation),
                "Yes" if _is_nullable(field.annotation) else "No",
                description,
            )
        )

    return "\n".join(rows)


def main() -> int:
    parts = [PREAMBLE, _field_index()]

    undocumented = 0

    for model, heading, endpoints in MODELS:
        parts.append(f"\n## {heading}\n")
        parts.append(f"Returned by {endpoints}.\n")

        if model.__doc__:
            parts.append(f"{model.__doc__.strip()}\n")

        parts.append(_table(model))
        parts.append("")

        undocumented += sum(1 for field in model.model_fields.values() if not field.description)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(parts) + "\n")

    total = sum(len(model.model_fields) for model, _, _ in MODELS)

    print(f"wrote {OUT}")
    print(f"{total - undocumented}/{total} fields documented")

    if undocumented:
        print(f"\n{undocumented} fields have no description.")
        print("Add Field(description=...) to document them.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
