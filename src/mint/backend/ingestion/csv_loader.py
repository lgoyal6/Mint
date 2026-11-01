import csv
from pathlib import Path
from typing import Any

from openpyxl import load_workbook

from mint.backend.ingestion.normalizer import normalize_csv_rows
from mint.backend.models import EnrichmentInput


def load_csv(path: str, column_mapping: dict | None = None) -> list[EnrichmentInput]:
    rows = load_rows(path)
    return normalize_csv_rows(rows, column_mapping)


def load_rows(path: str) -> list[dict[str, Any]]:
    input_path = Path(path)
    suffix = input_path.suffix.lower().lstrip(".")
    if suffix == "csv":
        return _load_delimited(input_path)
    elif suffix == "tsv":
        return _load_delimited(input_path, "\t")
    elif suffix == "xlsx":
        return _load_xlsx(input_path)
    raise ValueError(f"Unsupported input format: {suffix}")


def _load_delimited(path: Path, delimiter: str | None = None) -> list[dict[str, Any]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        if delimiter is None:
            sample = handle.read(16_384)
            handle.seek(0)
            try:
                delimiter = csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
            except csv.Error:
                delimiter = ","
        reader = csv.DictReader(handle, delimiter=delimiter)
        rows: list[dict[str, Any]] = []
        for row in reader:
            extra_fields = row.pop(None, None)
            if extra_fields is not None:
                raise ValueError(f"Delimited input row {reader.line_num} has extra fields: {extra_fields}")
            rows.append(dict(row))
        return rows


def _load_xlsx(path: Path) -> list[dict[str, Any]]:
    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        # Mint MQL exports are multi-tab workbooks with Summary first. When an
        # exported workbook is re-uploaded, ingest the actual lead table.
        worksheet = workbook["MQLs"] if "MQLs" in workbook.sheetnames else workbook.worksheets[0]
        rows = list(worksheet.iter_rows(values_only=True))
        if not rows:
            return []
        headers = [str(cell).strip() if cell is not None else "" for cell in rows[0]]
        output: list[dict[str, Any]] = []
        for raw_row in rows[1:]:
            row = {headers[index]: value for index, value in enumerate(raw_row) if headers[index]}
            if any(value not in (None, "") for value in row.values()):
                output.append(row)
        return output
    finally:
        workbook.close()
