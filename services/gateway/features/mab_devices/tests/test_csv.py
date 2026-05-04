"""Tests for the CSV import / export helpers in service.py.

Two layers:

1. `_parse_csv_to_bulk_items` — pure CSV → Pydantic logic, no DB.
   Cover the edge cases that bite operators: missing header row,
   missing required column, blank lines, MAC formats, mixed-case
   headers, unknown columns, mid-file row errors not aborting the
   batch.

2. `import_csv` orchestration — mocks `bulk_import` and asserts the
   summary shape (created/skipped/total/parse_errors).

`export_csv` is exercised in the integration test suite (it needs a
real Postgres for the SELECT).
"""
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from features.mab_devices import service


# ---------------------------------------------------------------------------
# _parse_csv_to_bulk_items
# ---------------------------------------------------------------------------

class TestParseCsv:
    def test_minimal_csv_one_row(self):
        items, errors = service._parse_csv_to_bulk_items(
            "mac_address\naa:bb:cc:dd:ee:01\n"
        )
        assert errors == []
        assert len(items) == 1
        assert items[0].mac_address == "aa:bb:cc:dd:ee:01"
        assert items[0].name is None
        assert items[0].assigned_vlan_id is None

    def test_full_csv_with_all_columns(self):
        csv = (
            "mac_address,name,description,device_type,assigned_vlan_id,expiry_date\n"
            "aa:bb:cc:dd:ee:01,Printer-Lobby,Brother HL-L2310D,printer,30,2027-01-01T00:00:00Z\n"
        )
        items, errors = service._parse_csv_to_bulk_items(csv)
        assert errors == []
        assert items[0].name == "Printer-Lobby"
        assert items[0].description == "Brother HL-L2310D"
        assert items[0].device_type == "printer"
        assert items[0].assigned_vlan_id == 30
        assert items[0].expiry_date == datetime(2027, 1, 1, tzinfo=timezone.utc)

    def test_no_header_row(self):
        items, errors = service._parse_csv_to_bulk_items("")
        assert items == []
        assert errors and "header" in errors[0]["error"].lower()

    def test_missing_required_column(self):
        items, errors = service._parse_csv_to_bulk_items(
            "name,device_type\nfoo,printer\n"
        )
        assert items == []
        assert errors and "mac_address" in errors[0]["error"]

    def test_mixed_case_headers_normalised(self):
        items, errors = service._parse_csv_to_bulk_items(
            "MAC_Address,Name\naa:bb:cc:dd:ee:02,Mixed\n"
        )
        assert errors == []
        assert items[0].name == "Mixed"

    def test_unknown_columns_silently_ignored(self):
        """Operators carry extra columns (asset_tag, owner) without
        the import failing — treat unknowns as drop-on-floor."""
        csv = (
            "mac_address,asset_tag,owner_email,name\n"
            "aa:bb:cc:dd:ee:03,A-1234,john@mds.local,Laptop-John\n"
        )
        items, errors = service._parse_csv_to_bulk_items(csv)
        assert errors == []
        assert items[0].name == "Laptop-John"

    def test_blank_lines_skipped(self):
        csv = (
            "mac_address\n"
            "aa:bb:cc:dd:ee:04\n"
            "\n"
            "\n"
            "aa:bb:cc:dd:ee:05\n"
        )
        items, errors = service._parse_csv_to_bulk_items(csv)
        assert errors == []
        assert len(items) == 2

    def test_blank_optional_value_uses_default(self):
        """`name=` (empty) should pass None to Pydantic, not "" — so
        the model default kicks in. Otherwise an int field like
        assigned_vlan_id would fail to parse "" → int."""
        csv = (
            "mac_address,name,assigned_vlan_id\n"
            "aa:bb:cc:dd:ee:06,,\n"
        )
        items, errors = service._parse_csv_to_bulk_items(csv)
        assert errors == []
        assert items[0].name is None
        assert items[0].assigned_vlan_id is None

    @pytest.mark.parametrize("mac", [
        "aa:bb:cc:dd:ee:ff",
        "AA-BB-CC-DD-EE-FF",
        "aabbccddeeff",
        "aabb.ccdd.eeff",
        "aa:bb:cc:dd:ee:FF",  # mixed case
    ])
    def test_mac_format_variants_all_normalised(self, mac):
        items, errors = service._parse_csv_to_bulk_items(
            f"mac_address\n{mac}\n"
        )
        assert errors == []
        assert items[0].mac_address == "aa:bb:cc:dd:ee:ff"

    def test_invalid_mac_reported_per_row_does_not_abort_batch(self):
        """One bad row in the middle must not stop the rest. The
        operator's most common case: 50 rows, 2 typos — they want the
        47 good ones in plus a clear list of which 3 to fix."""
        csv = (
            "mac_address\n"
            "aa:bb:cc:dd:ee:10\n"
            "not-a-mac\n"
            "aa:bb:cc:dd:ee:11\n"
            "zz:zz:zz:zz:zz:zz\n"
            "aa:bb:cc:dd:ee:12\n"
        )
        items, errors = service._parse_csv_to_bulk_items(csv)
        assert len(items) == 3
        assert len(errors) == 2
        assert errors[0]["row"] == 3  # 1=header, 2=first data row
        assert errors[1]["row"] == 5

    def test_invalid_vlan_reported(self):
        csv = (
            "mac_address,assigned_vlan_id\n"
            "aa:bb:cc:dd:ee:13,not-a-number\n"
        )
        items, errors = service._parse_csv_to_bulk_items(csv)
        assert items == []
        assert len(errors) == 1
        assert errors[0]["row"] == 2

    def test_quoted_field_with_comma_in_description(self):
        """csv module handles quoting natively — verify our parser
        respects it (e.g. description with embedded comma)."""
        csv = (
            'mac_address,description\n'
            'aa:bb:cc:dd:ee:14,"Brother, lobby printer"\n'
        )
        items, errors = service._parse_csv_to_bulk_items(csv)
        assert errors == []
        assert items[0].description == "Brother, lobby printer"


# ---------------------------------------------------------------------------
# import_csv orchestration
# ---------------------------------------------------------------------------

@pytest.fixture
def actor():
    return {"sub": str(uuid4()), "tenant_id": str(uuid4())}


class TestImportCsv:
    @pytest.mark.asyncio
    async def test_summary_includes_parse_errors_and_bulk_counts(self, actor):
        """End-to-end: 3 valid + 1 invalid → bulk_import called with
        the 3 valid items; summary surfaces the 1 parse error."""
        csv = (
            "mac_address\n"
            "aa:bb:cc:dd:ee:20\n"
            "aa:bb:cc:dd:ee:21\n"
            "broken\n"
            "aa:bb:cc:dd:ee:22\n"
        )
        with patch.object(
            service, "bulk_import",
            AsyncMock(return_value={"created": 3, "skipped": 0, "total": 3}),
        ) as bulk:
            summary = await service.import_csv(
                AsyncMock(), actor, csv_text=csv, client_ip="10.0.0.1",
            )
        bulk.assert_awaited_once()
        # bulk_import got the 3 parsed items, NOT the broken row
        assert len(bulk.await_args.kwargs["devices"]) == 3
        assert summary["created"] == 3
        assert summary["skipped"] == 0
        assert summary["total"] == 3
        assert len(summary["parse_errors"]) == 1
        assert summary["parse_errors"][0]["row"] == 4

    @pytest.mark.asyncio
    async def test_all_rows_invalid_skips_bulk_import(self, actor):
        """If parsing yields zero valid items, don't call bulk_import
        (and therefore don't audit-log a no-op)."""
        csv = "mac_address\nbroken-1\nbroken-2\n"
        with patch.object(service, "bulk_import", AsyncMock()) as bulk:
            summary = await service.import_csv(
                AsyncMock(), actor, csv_text=csv, client_ip=None,
            )
        bulk.assert_not_awaited()
        assert summary == {
            "created": 0, "skipped": 0, "total": 0,
            "parse_errors": summary["parse_errors"],
        }
        assert len(summary["parse_errors"]) == 2
