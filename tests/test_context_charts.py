"""Protect analytical values at the chart presentation boundary."""

from copy import deepcopy

from ui.context_charts import comparison_figure, session_sequence_figure


def test_comparison_preserves_missing_values_distinct_from_zero() -> None:
    rows = [
        {"label": "AHEAD", "games": 12, "gold_diff_15": 0},
        {"label": "EVEN", "games": 3, "gold_diff_15": None},
        {"label": "BEHIND", "games": 0, "gold_diff_15": 0},
    ]
    original = deepcopy(rows)
    figure = comparison_figure(rows, "gold_diff_15", "Gold Diff @15")
    assert list(figure.data[0].x) == [0, None]
    assert [row[0] for row in figure.data[0].customdata] == [12, 3]
    assert any("N/A" in annotation.text for annotation in figure.layout.annotations)
    assert rows == original


def test_session_story_keeps_every_match_and_chronological_result() -> None:
    rows = [
        {"session_game_number": position, "champion": "Ashe", "win": position % 2,
         "game_creation": 1_750_000_000_000 + position * 1_800_000, "duration": 1500,
         "minutes_since_previous_game": 5}
        for position in range(18, 0, -1)
    ]
    figure = session_sequence_figure(rows)
    assert list(figure.data[0].x) == list(range(1, 19))
    assert [row[2] for row in figure.data[0].customdata] == ["WIN", "LOSS"] * 9
    assert figure.data[0].customdata[0][3] == "Début de session"
    assert len(figure.data[0].text) == 18
