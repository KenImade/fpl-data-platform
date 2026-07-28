# tests/test_element.py

import json
from pathlib import Path

import polars as pl
import pytest
from fpl_ingestion.schemas import KNOWN_UNMAPPED, Element, polars_schema
from pydantic import ValidationError

CASSETTES = Path(__file__).parent / "cassettes"
CASSETTE = CASSETTES / "bootstrap-static.json"


@pytest.fixture(scope="module")
def bootstrap_cassette() -> dict:
    if not CASSETTE.exists():
        pytest.skip(f"no cassette at {CASSETTE} — run `just record-fixtures`")
    return json.loads(CASSETTE.read_text())


@pytest.fixture
def valid_payload():
    return {
        "id": 1,
        "code": 12345,
        "web_name": "Salah",
        "first_name": "Mohamed",
        "second_name": "Salah",
        "element_type": 3,
        "team": 11,
        "team_code": 14,
        "now_cost": 125,
        "status": "a",
        "news": "",
        "chance_of_playing_next_round": 100,
        "chance_of_playing_this_round": 100,
        "total_points": 250,
        "event_points": 8,
        "minutes": 3000,
        "selected_by_percent": "45.6",
        "ep_next": "7.2",
        "ep_this": "6.8",
    }


def test_valid_payload(valid_payload):
    element = Element.model_validate(valid_payload)

    assert element.id == 1
    assert element.web_name == "Salah"
    assert element.chance_of_playing_next_round == 100


def test_unknown_field_is_allowed(valid_payload):
    payload = valid_payload | {"unexpected_field": "hello"}

    element = Element.model_validate(payload)

    # Extra fields are retained because extra="allow"
    assert element.unexpected_field == "hello"


@pytest.mark.parametrize(
    "missing_field",
    [
        "id",
        "web_name",
        "team",
        "selected_by_percent",
    ],
)
def test_missing_required_field_raises(valid_payload, missing_field):
    payload = valid_payload.copy()
    payload.pop(missing_field)

    with pytest.raises(ValidationError):
        Element.model_validate(payload)


@pytest.mark.parametrize(
    "field,value",
    [
        ("id", "one"),
        ("team", "arsenal"),
        ("now_cost", "abc"),
        ("minutes", object()),
    ],
)
def test_wrong_type_raises(valid_payload, field, value):
    payload = valid_payload.copy()
    payload[field] = value

    with pytest.raises(ValidationError):
        Element.model_validate(payload)


@pytest.mark.parametrize("value", [None, 75])
def test_chance_of_playing_next_round_accepts_none_or_int(valid_payload, value):
    payload = valid_payload.copy()
    payload["chance_of_playing_next_round"] = value

    element = Element.model_validate(payload)

    assert element.chance_of_playing_next_round == value


def test_chance_of_playing_next_round_has_same_schema_for_none_and_int(valid_payload):
    payload_with_none = valid_payload.copy()
    payload_with_none["chance_of_playing_next_round"] = None

    payload_with_int = valid_payload.copy()
    payload_with_int["chance_of_playing_next_round"] = 75

    element_none = Element.model_validate(payload_with_none)
    element_int = Element.model_validate(payload_with_int)

    # Same model type => same downstream schema (e.g. Parquet)
    assert element_none.model_json_schema() == element_int.model_json_schema()


def test_null_and_int_produce_identical_parquet_schema(valid_payload):
    """The bug this model exists to prevent: polars infers Null for an
    all-null column, so a pre-season partition and an in-season one type
    differently and a union across them fails."""
    schema = polars_schema(Element)
    fields = set(Element.model_fields)

    none_row = valid_payload | {"chance_of_playing_next_round": None}
    int_row = valid_payload | {"chance_of_playing_next_round": 75}

    a = pl.DataFrame([Element.model_validate(none_row).model_dump(include=fields)], schema=schema)
    b = pl.DataFrame([Element.model_validate(int_row).model_dump(include=fields)], schema=schema)

    assert a.schema == b.schema
    assert a.schema["chance_of_playing_next_round"] == pl.Int64


def test_extra_fields_are_discoverable(valid_payload):
    el = Element.model_validate(valid_payload | {"brand_new_stat": 42})
    assert set(el.model_extra) == {"brand_new_stat"}


def test_known_unmapped_matches_cassette(bootstrap_cassette):
    """If this fails, FPL changed the payload. Investigate before updating —
    a new field may be one worth modelling."""
    seen: set[str] = set()
    for e in bootstrap_cassette["elements"]:
        seen |= set(e)

    assert seen - set(Element.model_fields) == KNOWN_UNMAPPED
