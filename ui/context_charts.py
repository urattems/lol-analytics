"""Small, presentation-only charts for context comparisons and session stories."""

from __future__ import annotations

from html import escape

import plotly.graph_objects as go

from ui.charts import chart_style
from ui.formatting import context_label, decimal_label, duration_label, match_date_label
from ui.theme import GOLD, GREEN, MUTED, RED, TEAL


def comparison_figure(
    rows: list[dict[str, object]], metric: str = "winrate", title: str = "Winrate",
    context: str = "", limit: int = 12,
) -> go.Figure:
    """Show engine-supplied aggregates, keeping missing values and N explicit."""
    visible = [row for row in rows if int(row.get("games", 0)) > 0][:limit]
    labels = [escape(context_label(row.get("label"), context)) for row in visible]
    values = [row.get(metric) for row in visible]
    suffix = " %" if metric == "winrate" else ""
    decimals = 1 if metric in {"winrate", "kda", "cs_per_min"} else 0
    text = [
        f"{decimal_label(value, decimals, suffix)} · {row['games']} parties"
        for value, row in zip(values, visible)
    ]
    figure = go.Figure(go.Bar(
        x=values, y=labels, orientation="h", width=.56,
        marker_color=[TEAL if int(row["games"]) >= 5 else MUTED for row in visible],
        text=text, textposition="outside", cliponaxis=False,
        customdata=[[row["games"], context_label(row.get("label"), context)] for row in visible],
        hovertemplate=f"<b>%{{customdata[1]}}</b><br>{title} : %{{x:.{decimals}f}}{suffix}<br>N = %{{customdata[0]}}<extra></extra>",
    ))
    chart_style(figure, max(190, 70 + len(visible) * 43))
    figure.update_layout(showlegend=False, margin={"l": 12, "r": 120, "t": 12, "b": 35})
    figure.update_yaxes(autorange="reversed", showgrid=False, title=None)
    figure.update_xaxes(title=title, rangemode="tozero")
    if metric == "winrate":
        figure.update_xaxes(range=[0, 100], ticksuffix=" %", dtick=25)
    # A missing average is not a zero-length performance bar.
    for index, value in enumerate(values):
        if value is None:
            figure.add_annotation(x=0, y=labels[index], text=text[index], showarrow=False, xanchor="left")
    return figure


def discovery_figure(rows: list[dict[str, object]], limit: int = 5) -> go.Figure:
    """Dumbbells compare both observed WRs instead of showing the gap alone."""
    figure = go.Figure()
    visible = rows[:limit]
    for index, observation in enumerate(visible):
        context = str(observation["context"])
        high, low = observation["high"], observation["low"]
        figure.add_trace(go.Scatter(
            x=[low["winrate"], high["winrate"]], y=[index, index], mode="lines",
            line={"color": "#44536a", "width": 3}, hoverinfo="skip", showlegend=False,
        ))
        for group, color, symbol in ((low, GOLD, "diamond"), (high, TEAL, "circle")):
            label = context_label(group["label"], context)
            figure.add_trace(go.Scatter(
                x=[group["winrate"]], y=[index], mode="markers", showlegend=False,
                marker={"size": 13, "color": color, "symbol": symbol},
                customdata=[[escape(label), group["games"]]],
                hovertemplate="<b>%{customdata[0]}</b><br>Winrate : %{x:.1f} %<br>N = %{customdata[1]}<extra></extra>",
            ))
    chart_style(figure, max(225, 80 + len(visible) * 53))
    figure.update_xaxes(title="Winrate observé · survolez chaque groupe pour son effectif", range=[-3, 103], ticksuffix=" %", dtick=25)
    figure.update_yaxes(
        tickvals=list(range(len(visible))),
        ticktext=[escape(context_label(row["context"])) for row in visible],
        range=[len(visible) - .45, -.55], showgrid=False,
    )
    return figure


def distribution_figure(row: dict[str, object]) -> go.Figure:
    """An interval describes the middle 50%; it is not a confidence interval."""
    figure = go.Figure()
    if row.get("p25") is not None and row.get("p75") is not None:
        figure.add_trace(go.Scatter(
            x=[row["p25"], row["p75"]], y=[0, 0], mode="lines+markers",
            line={"color": TEAL, "width": 12}, marker={"size": 12, "color": TEAL},
            text=["P25", "P75"], hovertemplate="%{text} : %{x:.2f}<extra></extra>", name="50 % centraux",
        ))
        figure.add_trace(go.Scatter(
            x=[row["median"]], y=[0], mode="markers", name="Médiane",
            marker={"symbol": "diamond", "size": 20, "color": GOLD, "line": {"width": 2, "color": "#131c28"}},
            hovertemplate="Médiane : %{x:.2f}<extra></extra>",
        ))
        figure.add_trace(go.Scatter(
            x=[row["mean"]], y=[.18], mode="markers", name="Moyenne",
            marker={"symbol": "line-ns", "size": 16, "color": MUTED, "line": {"width": 2, "color": MUTED}},
            hovertemplate="Moyenne : %{x:.2f}<extra></extra>",
        ))
    chart_style(figure, 220)
    figure.update_yaxes(visible=False, range=[-.4, .6])
    figure.update_xaxes(title=str(row["metric"]))
    return figure


def session_sequence_figure(rows: list[dict[str, object]]) -> go.Figure:
    """Render every match in an actual session, in its original order."""
    ordered = sorted(rows, key=lambda row: int(row["session_game_number"]))
    positions = [row["session_game_number"] for row in ordered]
    results = ["WIN" if row.get("win") == 1 else "LOSS" if row.get("win") == 0 else "N/A" for row in ordered]
    figure = go.Figure(go.Scatter(
        x=positions, y=[0] * len(ordered), mode="lines+markers+text",
        line={"color": "#44536a", "width": 2},
        marker={"size": 20, "color": [GREEN if result == "WIN" else RED if result == "LOSS" else MUTED for result in results],
                "symbol": ["circle" if result == "WIN" else "x" for result in results]},
        text=[f"{escape(str(row.get('champion') or 'N/A'))}<br>{result}" if len(ordered) <= 10 else result for row, result in zip(ordered, results)],
        textposition="top center",
        customdata=[[
            match_date_label(row.get("game_creation"), include_time=True),
            duration_label(row.get("duration")), result,
            decimal_label(row.get("minutes_since_previous_game"), 1, " min") if int(row["session_game_number"]) > 1 else "Début de session",
            escape(str(row.get("champion") or "N/A")),
        ] for row, result in zip(ordered, results)],
        hovertemplate="<b>Partie %{x} · %{customdata[4]} · %{customdata[2]}</b><br>%{customdata[0]}<br>Durée : %{customdata[1]}<br>Pause : %{customdata[3]}<extra></extra>",
    ))
    chart_style(figure, 205)
    figure.update_layout(showlegend=False)
    figure.update_yaxes(visible=False, range=[-.5, 1])
    figure.update_xaxes(title="Position dans la session", dtick=1, range=[.5, len(ordered) + .5], showgrid=False)
    return figure
