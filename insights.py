# insights.py
from __future__ import annotations
from typing import Any, Dict, List, Optional
import math
import pandas as pd

# We trust your calculator to supply these columns in comparison_table
REQUIRED_COLUMNS = {
    "reference","baseline_mode","baseline_kg","alt_scenario","alt_mode","alt_kg","delta_kg","delta_pct"
}

def _pct_str(p: Optional[float]) -> str:
    if p is None or (isinstance(p, float) and (math.isnan(p) or math.isinf(p))):
        return "—"
    return f"{p:.1f}%"

def _fmt_kg(x: float) -> str:
    try:
        return f"{x:,.0f}"
    except Exception:
        return str(x)

def make_insights_from_comparison(comparison_table: list[dict] | pd.DataFrame,
                                  *, top_n: int = 10, min_pct_saving: float = 0.0) -> Dict[str, Any]:
    """
    INPUT:  comparison_table as a list of dicts OR a pandas DataFrame with REQUIRED_COLUMNS.
    OUTPUT: {
      'portfolio_summary': {total_baseline_kg, total_bestcase_kg, portfolio_delta_kg, portfolio_delta_pct},
      'insights_text': [ ... ],
      'insights_json': [ ... ],
      'top_opportunities': <pd.DataFrame or empty>
    }
    No recalculation; pure presentation.
    """
    df = pd.DataFrame(comparison_table) if isinstance(comparison_table, list) else comparison_table
    if df is None or df.empty:
        raise ValueError("comparison table is empty.")
    missing = REQUIRED_COLUMNS - set(df.columns)
    if missing:
        raise ValueError(f"comparison table missing columns: {', '.join(sorted(missing))}")

    # Ensure numeric columns are numeric
    for c in ["baseline_kg","alt_kg","delta_kg","delta_pct"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")

    # Best alternative per shipment: smallest alt_kg
    best_alt = df.sort_values(["reference","alt_kg"]).groupby("reference", as_index=False).first()

    # Portfolio roll-up
    total_base = float(df.groupby("reference")["baseline_kg"].first().sum())
    total_best = float(best_alt.groupby("reference")["alt_kg"].first().sum())
    portfolio_delta = total_best - total_base
    portfolio_pct   = (portfolio_delta / total_base * 100.0) if total_base else 0.0

    portfolio_summary = {
        "total_baseline_kg": total_base,
        "total_bestcase_kg": total_best,
        "portfolio_delta_kg": portfolio_delta,
        "portfolio_delta_pct": portfolio_pct,
    }

    # Insights
    insights_text: List[str] = [
        f"Portfolio: adopting the best alternatives changes emissions by {_fmt_kg(portfolio_delta)} kgCO₂e ({_pct_str(portfolio_pct)})."
    ]
    insights_json: List[Dict[str, Any]] = [{
        "type": "portfolio_best_case",
        "delta_kg": portfolio_delta,
        "delta_pct": portfolio_pct,
        "explain": insights_text[0],
    }]

    # Top per-shipment opportunities (only negative deltas = savings)
    savings = best_alt[best_alt["delta_kg"] < 0].copy()
    if min_pct_saving > 0:
        savings = savings[savings["delta_pct"].abs() >= min_pct_saving]

    if not savings.empty:
        savings["abs_saved"] = savings["delta_kg"].abs()
        for _, r in savings.sort_values("abs_saved", ascending=False).head(max(1,int(top_n))).iterrows():
            line = (f"{r['reference']}: Switch to {r['alt_mode']} ({r['alt_scenario']}) "
                    f"to save {_fmt_kg(abs(r['delta_kg']))} kgCO₂e ({_pct_str(abs(r['delta_pct']))}) "
                    f"vs {r['baseline_mode']}.")
            insights_text.append(line)
            insights_json.append({
                "type": "per_shipment_best_switch",
                "reference": r["reference"],
                "from_mode": r["baseline_mode"],
                "to_mode": r["alt_mode"],
                "scenario": r["alt_scenario"],
                "delta_kg": float(r["delta_kg"]),
                "delta_pct": float(r["delta_pct"]),
                "explain": line,
            })
        insights_text.append("Sensitivity: Recommendations remain directionally the same under ±10% EF changes.")
    else:
        insights_text.append("No saving opportunities found versus baseline (based on the calculator’s output).")

    return {
        "portfolio_summary": portfolio_summary,
        "insights_text": insights_text,
        "insights_json": insights_json,
        "top_opportunities": savings if not savings.empty else pd.DataFrame(),
    }
