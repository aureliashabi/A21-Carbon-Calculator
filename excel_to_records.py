# excel_to_records.py
from __future__ import annotations
from io import BytesIO
from typing import List, Dict, Any, Optional, Tuple
import pandas as pd

# Your sheet has up to 3 sector blocks with these subcolumns
SECTOR_BLOCKS = [
    ("1st sector", ["1st sector Flight Date","1st sector Flight Number","1st sector From","1st sector To"]),
    ("2nd Sector", ["2nd Sector Flight Date","2nd Sector Flight Number","2nd Sector From","2nd Sector To"]),
    ("3rd Sector", ["3rd Sector Flight Date","3rd Sector Flight Number","3rd Sector From","3rd Sector To"]),
]

def _flatten_header(xl_bytes: bytes, sheet: Optional[str]) -> pd.DataFrame:
    """
    Locate the two header rows (band + column names) by finding the row that contains 'Ref No'.
    Merge them into a single header row like '1st sector Flight Number'.
    """
    raw = pd.ExcelFile(BytesIO(xl_bytes)).parse(sheet or 0, header=None, dtype=object)

    # Find the row that contains 'Ref No' (lower header row)
    ref_rows = raw.index[(raw == "Ref No").any(axis=1)].tolist()
    if not ref_rows:
        raise ValueError("Could not find 'Ref No' in header rows.")
    row2 = ref_rows[0]               # lower header row with 'Ref No'
    row1 = max(0, row2 - 1)          # band row: '1st sector', '2nd Sector', ...

    bands = raw.iloc[row1].ffill().fillna("")
    cols  = raw.iloc[row2].fillna("")
    combined = []
    for b, c in zip(bands, cols):
        b = str(b).strip()
        c = str(c).strip()
        if b and c and ("sector" in b.lower()):
            combined.append(f"{b} {c}")   # e.g. "1st sector Flight Number"
        else:
            combined.append(c or b)

    df = raw.iloc[row2+1:].reset_index(drop=True)
    df.columns = combined

    # Drop Excel’s unnamed filler columns if present
    drop_cols = [c for c in df.columns if str(c).lower().startswith("unnamed")]
    if drop_cols:
        df = df.drop(columns=drop_cols)
    return df

def _collect_sector_segments(row: pd.Series) -> List[Dict[str, Any]]:
    """
    Build a list of sector segments with endpoints and optional flight metadata.
    We only keep segments that have BOTH 'from' and 'to' filled.
    """
    segs: List[Dict[str, Any]] = []
    for _, (_, cols) in enumerate(SECTOR_BLOCKS):
        c_date, c_fn, c_from, c_to = cols
        seg = {
            "flight_date": row.get(c_date),
            "flight_number": row.get(c_fn),
            "from": (str(row.get(c_from) or "").strip() or None),
            "to":   (str(row.get(c_to) or "").strip() or None),
        }
        if seg["from"] and seg["to"]:
            segs.append(seg)
    return segs

def read_manifest_to_records(excel_bytes: bytes, sheet: Optional[str]=None) -> Dict[str, Any]:
    """
    INPUT:  Excel bytes in your July-style format (2-row sector header).
    OUTPUT: {
      'records': [ {reference, scenario='baseline', mode='air', origin, destination, segments[], notes?}, ... ],
      'count': <int>,
      'warnings': [ ... ],
      'errors': [ ... ]
    }

    Notes:
    - We purposefully do NOT compute distance, EF, or emissions here.
    - Each row becomes a 'baseline' air record; your calc team can add alternatives/emissions.
    """
    warnings: List[str] = []
    errors: List[str] = []

    df = _flatten_header(excel_bytes, sheet)

    # Validate required headers present
    required = ["Ref No", "Origin", "Destination"]
    for r in required:
        if r not in df.columns:
            errors.append(f"Missing required column '{r}'.")
    if errors:
        return {"records": [], "count": 0, "warnings": warnings, "errors": errors}

    out: List[Dict[str, Any]] = []
    for _, row in df.iterrows():
        ref = str(row.get("Ref No") or "").strip()
        if not ref:
            continue  # skip blank rows

        origin = (str(row.get("Origin") or "").strip() or None)
        dest   = (str(row.get("Destination") or "").strip() or None)
        delivery_to = (str(row.get("Delivery To") or "").strip() or None)
        segments = _collect_sector_segments(row)

        rec: Dict[str, Any] = {
            "reference": ref,
            "scenario": "baseline",   # your manifest is air by definition
            "mode": "air",
            "origin": origin,
            "destination": dest,
            "segments": segments,     # [{from,to,flight_date?,flight_number?}, ...]
        }
        if delivery_to:
            rec["notes"] = delivery_to

        out.append(rec)

    return {"records": out, "count": len(out), "warnings": warnings, "errors": errors}
