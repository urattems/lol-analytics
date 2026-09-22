"""Team color is about the actual side ahead, not the sign of player delta."""
from copy import deepcopy

import pytest

from pages.timeline import _team_gold_figure
from ui.team_advantage import advantage_regions, advantage_label


def make_frames(differences, own_side="blue"):
    participants = [dict(puuid=f"synthetic-{i}", side="blue" if i < 5 else "red") for i in range(10)]
    frames = []
    for minute, diff in enumerate(differences):
        for i, participant in enumerate(participants):
            gold = None if diff is None else 10000 + (diff if participant['side'] == own_side else 0) / 5
            frames.append(dict(puuid=participant['puuid'], participant_id=i+1, timestamp_ms=minute*60000, total_gold=gold))
    return frames, participants


@pytest.mark.parametrize("side", ["blue", "red"])
def test_sign_side_hover_and_visual_crossing(side):
    values = [1000, 2000, -1000, -3000]
    frames, participants = make_frames(values, side)
    before = deepcopy(frames)
    figure = _team_gold_figure(frames, participants, side)
    assert list(figure.data[0].y) == values
    assert list(figure.data[0].x) == [0, 1, 2, 3]
    assert figure.data[0].text[0] == f"{side.title()} Side +1 000 g"
    other = "red" if side == "blue" else "blue"
    assert figure.data[0].text[-1] == f"{other.title()} Side +3 000 g"
    regions = advantage_regions([0, 1, 2, 3], values, side)
    for region in regions.values():
        assert any(x is not None and x == pytest.approx(1+2/3) for x in region['x'])
    for name, region in regions.items():
        assert all(y >= 0 if name == side else y <= 0 for y in region['y'] if y is not None)
    assert frames == before
    assert {t.name for t in figure.data[1:]} == {"Blue Side devant", "Red Side devant"}


@pytest.mark.parametrize("values,expected", [([1000, 2000], {'blue'}), ([-1000, -2000], {'red'}),
    ([0, 0, 0], set()), ([1000, 0, -1000], {'blue', 'red'}), ([1000, None, -1000], set())])
def test_zero_single_color_and_missing_intervals(values, expected):
    frames, participants = make_frames(values)
    fig = _team_gold_figure(frames, participants, "blue")
    assert list(fig.data[0].y) == values
    regions = advantage_regions(list(range(len(values))), values, "blue")
    assert {s for s, r in regions.items() if r['x']} == expected
    assert all(t.connectgaps is False for t in fig.data)
    for value, label in zip(values, fig.data[0].text):
        if value == 0: assert label == "Égalité"
        if value is None: assert label == "Avantage indisponible"


def test_partial_team_and_unknown_side_are_not_zero():
    frames, participants = make_frames([1000, 2000, -1000])
    frames[15]['total_gold'] = None
    assert list(_team_gold_figure(frames, participants, 'blue').data[0].y) == [1000, None, -1000]
    unknown = _team_gold_figure(frames, participants, 'unknown')
    assert all(y is None for y in unknown.data[0].y)
    assert advantage_label(1000, 'unknown') == 'Avantage indisponible'
