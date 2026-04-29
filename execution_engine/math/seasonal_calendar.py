"""Commodity seasonal edge calendar.

Well-documented seasonal patterns in commodity futures with 20-30 years
of empirical backing. Used by the commodity_seasonal strategy to emit
signals when a seasonal window opens.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass


@dataclass(frozen=True)
class SeasonalEdge:
    """A single seasonal trading edge for a commodity."""

    entry_month: int
    entry_week: int  # 1-4
    direction: str  # "LONG" or "SHORT"
    exit_month: int
    exit_week: int
    historical_win_rate: float
    avg_return_pct: float
    years_data: int


COMMODITY_SEASONAL_EDGES: dict[str, list[SeasonalEdge]] = {
    "NG": [  # Natural Gas — winter heating demand
        SeasonalEdge(10, 1, "LONG", 2, 2, 0.72, 8.5, 30),
    ],
    "ZC": [  # Corn — spring planting to pre-harvest
        SeasonalEdge(3, 2, "LONG", 6, 3, 0.68, 6.2, 30),
    ],
    "GC": [  # Gold — summer rally (Indian wedding season + fall demand)
        SeasonalEdge(7, 3, "LONG", 10, 1, 0.71, 5.8, 30),
    ],
    "SI": [  # Silver — tracks gold with industrial overlay
        SeasonalEdge(7, 3, "LONG", 10, 1, 0.66, 7.2, 25),
    ],
    "HG": [  # Copper — spring construction demand
        SeasonalEdge(1, 2, "LONG", 4, 3, 0.64, 5.5, 25),
    ],
    "ZS": [  # Soybeans — spring planting premium
        SeasonalEdge(10, 3, "LONG", 3, 2, 0.67, 5.8, 30),
    ],
    "ZW": [  # Wheat — winter wheat planting concerns
        SeasonalEdge(6, 2, "LONG", 9, 1, 0.63, 4.5, 25),
    ],
    "CL": [  # Crude Oil — summer driving season
        SeasonalEdge(2, 2, "LONG", 5, 3, 0.65, 6.0, 30),
    ],
    "KC": [  # Coffee — Brazilian frost risk window
        SeasonalEdge(5, 1, "LONG", 8, 2, 0.62, 7.5, 20),
    ],
}


def get_active_seasonal_edges(
    current_date: dt.date,
) -> list[tuple[str, SeasonalEdge]]:
    """Return all seasonal edges that are currently in their entry window.

    An edge is "active" if current_date falls in the entry week ± 1 week.
    """
    month = current_date.month
    # Approximate week of month (1-based)
    week = min((current_date.day - 1) // 7 + 1, 4)

    active: list[tuple[str, SeasonalEdge]] = []
    for symbol, edges in COMMODITY_SEASONAL_EDGES.items():
        for edge in edges:
            if edge.entry_month == month and abs(edge.entry_week - week) <= 1:
                active.append((symbol, edge))
    return active


def is_in_seasonal_window(
    symbol: str, current_date: dt.date
) -> SeasonalEdge | None:
    """Check if a specific commodity has an active seasonal edge today."""
    edges = COMMODITY_SEASONAL_EDGES.get(symbol, [])
    month = current_date.month
    week = min((current_date.day - 1) // 7 + 1, 4)
    for edge in edges:
        if edge.entry_month == month and abs(edge.entry_week - week) <= 1:
            return edge
    return None
