"""CSV ingestion: English and Chinese headers, validation errors with row
numbers, and a fingerprint that changes with the data."""
from __future__ import annotations

import pytest

from metrology.ingest import (
    IngestError,
    fingerprint,
    parse_csv,
    rows_to_csv,
)
from metrology.variance import DesignError, clean_rows

EN = """lab,device,date,value
L01,D1,2026-03-02,50.12
L01,D1,2026-03-02,50.31
L02,D2,2026-03-03,49.87
"""

ZH = """实验室,设备,日期,测量值
L01,D1,2026-03-02,50.12
L01,D1,2026-03-02,50.31
L02,D2,2026-03-03,49.87
"""


def test_parse_english_and_chinese_headers_equivalently():
    assert parse_csv(EN) == parse_csv(ZH)


def test_round_trip_csv():
    rows = clean_rows(parse_csv(EN))
    again = parse_csv(rows_to_csv(rows))
    assert clean_rows(again) == rows


def test_missing_column_is_reported_by_name():
    with pytest.raises(IngestError) as exc:
        parse_csv("lab,device,date\nL01,D1,2026-03-02\n")
    assert "测量值" in str(exc.value)


def test_empty_csv_is_rejected():
    with pytest.raises(IngestError):
        parse_csv("")


def test_header_only_csv_is_rejected():
    with pytest.raises(IngestError):
        parse_csv("lab,device,date,value\n")


def test_non_numeric_value_names_the_row():
    with pytest.raises(DesignError) as exc:
        clean_rows(parse_csv(EN.replace("50.31", "abc")))
    assert "第 3 行" in str(exc.value)


def test_non_finite_value_is_rejected():
    with pytest.raises(DesignError):
        clean_rows(parse_csv(EN.replace("50.31", "nan")))


def test_missing_field_is_rejected():
    with pytest.raises(DesignError):
        clean_rows(parse_csv(EN.replace("L02,D2", "L02,")))


def test_fingerprint_is_stable_and_sensitive():
    a = clean_rows(parse_csv(EN))
    b = clean_rows(parse_csv(EN))
    assert fingerprint(a) == fingerprint(b)
    assert len(fingerprint(a)) == 16
    c = clean_rows(parse_csv(EN.replace("49.87", "49.88")))
    assert fingerprint(a) != fingerprint(c)


def test_colour_alias_header_is_accepted():
    rows = parse_csv("Lab ID,Instrument,Run Date,Result\nL1,X1,2026-01-01,10\n")
    assert rows[0]["value"] == "10"
