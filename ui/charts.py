"""Consistent chart presentation; analytical values are supplied by the engines."""

from __future__ import annotations

import math

import plotly.graph_objects as go

from ui.theme import GOLD, MUTED


def chart_style(figure: go.Figure, height: int = 300) -> go.Figure:
    figure.update_layout(
        height=height, paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font={"family": "Segoe UI, sans-serif", "size": 12, "color": MUTED},
        margin={"l": 12, "r": 18, "t": 25, "b": 35},
        hoverlabel={"bgcolor": "#192433", "bordercolor": GOLD, "font_color": "#edf3fa"},
        legend={"orientation": "h", "y": 1.15, "x": 0},
    )
    figure.update_xaxes(gridcolor="rgba(154,174,194,.07)", zerolinecolor="#44536a", automargin=True)
    figure.update_yaxes(gridcolor="rgba(154,174,194,.10)", zerolinecolor="#44536a", automargin=True)
    return figure


def match_tick_step(count: int) -> int:
    """Keep chronological integer axes readable even for 500 matches."""
    return max(1, math.ceil(count / 12))
