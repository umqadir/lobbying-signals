"""Anomaly detection engine for lobbying signals.

Detects:
1. Record-breaking values - all-time highs
2. Spike detection - YoY growth exceeding threshold
3. Concentration shifts - single entity capturing market share
4. New entrants - major players appearing for first time
5. Coordinated surges - multiple unrelated clients on same issue
"""

from collections import defaultdict
from dataclasses import dataclass
from typing import Literal

import pandas as pd

from config import (
    RECORD_MIN_HISTORY,
    SPIKE_YOY_THRESHOLD,
    SPIKE_MIN_BASELINE,
    CONCENTRATION_THRESHOLD,
    CONCENTRATION_PRIOR_MAX,
    NEW_ENTRANT_MIN_SPEND,
    COORDINATED_SURGE_MIN_CLIENTS,
)
from db import get_db, insert_signal, query_to_dicts


@dataclass
class Signal:
    signal_type: Literal["record", "spike", "concentration", "new_entrant", "coordinated"]
    entity_type: str  # "issue", "registrant", "client"
    entity_id: str
    entity_name: str
    metric: str
    current_value: float
    prior_value: float
    growth_rate: float
    historical_pct: float  # percentile rank
    magnitude_score: float
    quarter: int
    year: int
    context: dict = None


def get_quarterly_totals_by_issue() -> pd.DataFrame:
    """Get total income by issue label per quarter."""
    sql = """
        SELECT
            i.issue_label,
            f.year,
            f.quarter,
            SUM(f.income / (SELECT COUNT(*) FROM activities WHERE filing_id = f.id)) as allocated_income
        FROM issues i
        JOIN activities a ON i.activity_id = a.id
        JOIN filings f ON a.filing_id = f.id
        WHERE f.income > 0
        GROUP BY i.issue_label, f.year, f.quarter
        ORDER BY i.issue_label, f.year, f.quarter
    """
    with get_db() as conn:
        rows = query_to_dicts(conn, sql)
    return pd.DataFrame(rows)


def get_quarterly_totals_by_registrant() -> pd.DataFrame:
    """Get total income by registrant per quarter."""
    sql = """
        SELECT
            r.id as registrant_id,
            r.name as registrant_name,
            f.year,
            f.quarter,
            SUM(f.income) as total_income
        FROM filings f
        JOIN registrants r ON f.registrant_id = r.id
        WHERE f.income > 0
        GROUP BY r.id, f.year, f.quarter
        ORDER BY r.id, f.year, f.quarter
    """
    with get_db() as conn:
        rows = query_to_dicts(conn, sql)
    return pd.DataFrame(rows)


def get_quarterly_totals_by_client() -> pd.DataFrame:
    """Get total income (spending) by client per quarter."""
    sql = """
        SELECT
            c.id as client_id,
            c.name as client_name,
            f.year,
            f.quarter,
            SUM(f.income) as total_income
        FROM filings f
        JOIN clients c ON f.client_id = c.id
        WHERE f.income > 0
        GROUP BY c.id, f.year, f.quarter
        ORDER BY c.id, f.year, f.quarter
    """
    with get_db() as conn:
        rows = query_to_dicts(conn, sql)
    return pd.DataFrame(rows)


def get_registrant_issue_shares() -> pd.DataFrame:
    """Get registrant share of each issue per quarter."""
    sql = """
        SELECT
            i.issue_label,
            r.id as registrant_id,
            r.name as registrant_name,
            f.year,
            f.quarter,
            SUM(f.income / (SELECT COUNT(*) FROM activities WHERE filing_id = f.id)) as allocated_income
        FROM issues i
        JOIN activities a ON i.activity_id = a.id
        JOIN filings f ON a.filing_id = f.id
        JOIN registrants r ON f.registrant_id = r.id
        WHERE f.income > 0
        GROUP BY i.issue_label, r.id, f.year, f.quarter
    """
    with get_db() as conn:
        rows = query_to_dicts(conn, sql)
    return pd.DataFrame(rows)


def get_client_issue_history() -> pd.DataFrame:
    """Get client lobbying history by issue."""
    sql = """
        SELECT DISTINCT
            i.issue_label,
            c.id as client_id,
            c.name as client_name,
            f.year,
            f.quarter
        FROM issues i
        JOIN activities a ON i.activity_id = a.id
        JOIN filings f ON a.filing_id = f.id
        JOIN clients c ON f.client_id = c.id
    """
    with get_db() as conn:
        rows = query_to_dicts(conn, sql)
    return pd.DataFrame(rows)


def detect_records(df: pd.DataFrame, value_col: str, group_col: str, name_col: str = None) -> list[Signal]:
    """Detect all-time high values for each group."""
    signals = []

    for group_id, group_df in df.groupby(group_col):
        if group_df[value_col].sum() < RECORD_MIN_HISTORY:
            continue

        # Get latest period
        latest = group_df.sort_values(["year", "quarter"]).iloc[-1]
        current_value = latest[value_col]

        # Get historical max (excluding current)
        history = group_df.iloc[:-1] if len(group_df) > 1 else group_df
        historical_max = history[value_col].max()

        if current_value > historical_max and len(group_df) > 4:  # At least 1 year of history
            # Calculate percentile
            all_values = group_df[value_col].values
            pct = (all_values < current_value).mean()

            entity_name = latest[name_col] if name_col else str(group_id)

            signals.append(Signal(
                signal_type="record",
                entity_type="issue" if group_col == "issue_label" else group_col.replace("_id", ""),
                entity_id=str(group_id),
                entity_name=entity_name,
                metric=f"quarterly_{value_col}",
                current_value=current_value,
                prior_value=historical_max,
                growth_rate=(current_value - historical_max) / historical_max if historical_max > 0 else 0,
                historical_pct=pct,
                magnitude_score=current_value / 1_000_000,  # Normalize by $1M
                quarter=int(latest["quarter"]),
                year=int(latest["year"])
            ))

    return signals


def detect_spikes(df: pd.DataFrame, value_col: str, group_col: str, name_col: str = None) -> list[Signal]:
    """Detect YoY spikes exceeding threshold."""
    signals = []

    for group_id, group_df in df.groupby(group_col):
        group_df = group_df.sort_values(["year", "quarter"])

        if len(group_df) < 5:  # Need at least 1+ year history
            continue

        latest = group_df.iloc[-1]
        current_value = latest[value_col]

        # Find same quarter prior year
        prior_year = latest["year"] - 1
        prior_quarter = latest["quarter"]
        prior = group_df[
            (group_df["year"] == prior_year) &
            (group_df["quarter"] == prior_quarter)
        ]

        if prior.empty or prior.iloc[0][value_col] < SPIKE_MIN_BASELINE:
            continue

        prior_value = prior.iloc[0][value_col]
        growth_rate = (current_value - prior_value) / prior_value

        if growth_rate >= SPIKE_YOY_THRESHOLD:
            all_values = group_df[value_col].values
            pct = (all_values < current_value).mean()

            entity_name = latest[name_col] if name_col else str(group_id)

            signals.append(Signal(
                signal_type="spike",
                entity_type="issue" if group_col == "issue_label" else group_col.replace("_id", ""),
                entity_id=str(group_id),
                entity_name=entity_name,
                metric=f"yoy_growth_{value_col}",
                current_value=current_value,
                prior_value=prior_value,
                growth_rate=growth_rate,
                historical_pct=pct,
                magnitude_score=current_value / 1_000_000,
                quarter=int(latest["quarter"]),
                year=int(latest["year"])
            ))

    return signals


def detect_concentration_shifts(issue_shares: pd.DataFrame) -> list[Signal]:
    """Detect when a single registrant captures dominant share of an issue."""
    signals = []

    for issue_label, issue_df in issue_shares.groupby("issue_label"):
        # Get latest period
        latest_period = issue_df.sort_values(["year", "quarter"]).iloc[-1]
        latest_year, latest_quarter = latest_period["year"], latest_period["quarter"]

        current_period = issue_df[
            (issue_df["year"] == latest_year) &
            (issue_df["quarter"] == latest_quarter)
        ]

        if current_period.empty:
            continue

        # Calculate market shares for current period
        total = current_period["allocated_income"].sum()
        if total < SPIKE_MIN_BASELINE:
            continue

        current_period = current_period.copy()
        current_period["share"] = current_period["allocated_income"] / total

        # Find dominant player
        top_player = current_period.loc[current_period["share"].idxmax()]

        if top_player["share"] < CONCENTRATION_THRESHOLD:
            continue

        # Check prior year same quarter
        prior_period = issue_df[
            (issue_df["year"] == latest_year - 1) &
            (issue_df["quarter"] == latest_quarter) &
            (issue_df["registrant_id"] == top_player["registrant_id"])
        ]

        if not prior_period.empty:
            prior_total = issue_df[
                (issue_df["year"] == latest_year - 1) &
                (issue_df["quarter"] == latest_quarter)
            ]["allocated_income"].sum()

            if prior_total > 0:
                prior_share = prior_period.iloc[0]["allocated_income"] / prior_total
                if prior_share > CONCENTRATION_PRIOR_MAX:
                    continue  # Already dominant before

        signals.append(Signal(
            signal_type="concentration",
            entity_type="registrant",
            entity_id=str(top_player["registrant_id"]),
            entity_name=top_player["registrant_name"],
            metric=f"market_share_{issue_label}",
            current_value=top_player["share"],
            prior_value=prior_share if not prior_period.empty else 0,
            growth_rate=top_player["share"] - (prior_share if not prior_period.empty else 0),
            historical_pct=0.99,  # By definition, highest
            magnitude_score=total / 1_000_000,
            quarter=int(latest_quarter),
            year=int(latest_year),
            context={"issue": issue_label}
        ))

    return signals


def detect_new_entrants(client_history: pd.DataFrame) -> list[Signal]:
    """Detect major clients appearing in an issue for the first time."""
    signals = []

    # Get latest period
    latest = client_history.sort_values(["year", "quarter"]).iloc[-1]
    latest_year, latest_quarter = latest["year"], latest["quarter"]

    for issue_label, issue_df in client_history.groupby("issue_label"):
        # Clients in current period
        current = issue_df[
            (issue_df["year"] == latest_year) &
            (issue_df["quarter"] == latest_quarter)
        ]

        # Clients in any prior period
        prior = issue_df[
            (issue_df["year"] < latest_year) |
            ((issue_df["year"] == latest_year) & (issue_df["quarter"] < latest_quarter))
        ]
        prior_clients = set(prior["client_id"].unique())

        # New clients
        for _, row in current.iterrows():
            if row["client_id"] not in prior_clients:
                signals.append(Signal(
                    signal_type="new_entrant",
                    entity_type="client",
                    entity_id=str(row["client_id"]),
                    entity_name=row["client_name"],
                    metric=f"first_appearance_{issue_label}",
                    current_value=1,
                    prior_value=0,
                    growth_rate=0,
                    historical_pct=0,
                    magnitude_score=0.5,  # Will be weighted later
                    quarter=int(latest_quarter),
                    year=int(latest_year),
                    context={"issue": issue_label}
                ))

    return signals


def detect_coordinated_surges(client_history: pd.DataFrame) -> list[Signal]:
    """Detect multiple unrelated clients suddenly lobbying same issue."""
    signals = []

    # Get latest period
    latest = client_history.sort_values(["year", "quarter"]).iloc[-1]
    latest_year, latest_quarter = latest["year"], latest["quarter"]

    for issue_label, issue_df in client_history.groupby("issue_label"):
        # Count new clients this period
        current = issue_df[
            (issue_df["year"] == latest_year) &
            (issue_df["quarter"] == latest_quarter)
        ]

        prior = issue_df[
            (issue_df["year"] < latest_year) |
            ((issue_df["year"] == latest_year) & (issue_df["quarter"] < latest_quarter))
        ]
        prior_clients = set(prior["client_id"].unique())

        new_clients = current[~current["client_id"].isin(prior_clients)]

        if len(new_clients) >= COORDINATED_SURGE_MIN_CLIENTS:
            signals.append(Signal(
                signal_type="coordinated",
                entity_type="issue",
                entity_id=issue_label,
                entity_name=issue_label,
                metric="new_client_count",
                current_value=len(new_clients),
                prior_value=0,
                growth_rate=0,
                historical_pct=0.95,
                magnitude_score=len(new_clients) / 10,
                quarter=int(latest_quarter),
                year=int(latest_year),
                context={
                    "new_clients": new_clients["client_name"].tolist()[:10]
                }
            ))

    return signals


def run_detection() -> list[Signal]:
    """Run all detection algorithms."""
    print("Loading data...")
    issue_totals = get_quarterly_totals_by_issue()
    registrant_totals = get_quarterly_totals_by_registrant()
    client_totals = get_quarterly_totals_by_client()
    issue_shares = get_registrant_issue_shares()
    client_history = get_client_issue_history()

    all_signals = []

    if not issue_totals.empty:
        print("Detecting issue records...")
        all_signals.extend(detect_records(issue_totals, "allocated_income", "issue_label"))

        print("Detecting issue spikes...")
        all_signals.extend(detect_spikes(issue_totals, "allocated_income", "issue_label"))

    if not registrant_totals.empty:
        print("Detecting registrant records...")
        all_signals.extend(detect_records(registrant_totals, "total_income", "registrant_id", "registrant_name"))

        print("Detecting registrant spikes...")
        all_signals.extend(detect_spikes(registrant_totals, "total_income", "registrant_id", "registrant_name"))

    if not issue_shares.empty:
        print("Detecting concentration shifts...")
        all_signals.extend(detect_concentration_shifts(issue_shares))

    if not client_history.empty:
        print("Detecting new entrants...")
        all_signals.extend(detect_new_entrants(client_history))

        print("Detecting coordinated surges...")
        all_signals.extend(detect_coordinated_surges(client_history))

    # Sort by magnitude
    all_signals.sort(key=lambda s: s.magnitude_score, reverse=True)

    print(f"Detected {len(all_signals)} signals")
    return all_signals


def save_signals(signals: list[Signal]):
    """Save detected signals to database."""
    with get_db() as conn:
        for signal in signals:
            insert_signal(
                conn,
                signal.signal_type,
                signal.entity_type,
                signal.entity_id,
                signal.entity_name,
                signal.metric,
                signal.current_value,
                signal.prior_value,
                signal.growth_rate,
                signal.historical_pct,
                signal.magnitude_score,
                signal.quarter,
                signal.year,
                None  # Narrative generated later
            )


if __name__ == "__main__":
    signals = run_detection()

    print("\nTop signals:")
    for signal in signals[:20]:
        print(f"  [{signal.signal_type}] {signal.entity_name}: {signal.metric}")
        print(f"    Current: ${signal.current_value:,.0f}, Growth: {signal.growth_rate:.1%}")
        print()

    save_signals(signals)
    print(f"Saved {len(signals)} signals to database")
