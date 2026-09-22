"""Pure visual matchup models; no Streamlit, network or asset loading."""
from dataclasses import dataclass
import polars as pl
from analytics.matchups import matchup_matrix


def sample_label(n: int) -> str:
    return "Faible" if n < 5 else "Limité" if n < 10 else "Modéré" if n < 25 else "Solide descriptivement"


@dataclass(frozen=True)
class MatchupCard:
    champion: str
    opponent: str
    games: int
    metric: str
    value: float | None
    observed: int
    sample: str


def matchup_cards(games: pl.DataFrame, metric="winrate", sort="games") -> list[MatchupCard]:
    if metric not in ("winrate", "gold_diff_15", "cs_diff_15") or sort not in ("games", "winrate", "gold_diff_15", "cs_diff_15"):
        raise ValueError("Métrique de matchup inconnue.")
    rows = matchup_matrix(games)
    count_column = "win" if metric == "winrate" else metric
    counts = {}
    if rows and count_column in games.columns:
        observed = games.group_by("champion", "opponent_champion").agg(pl.col(count_column).count().alias("observed"))
        counts = {(r["champion"], r["opponent_champion"]): r["observed"] for r in observed.to_dicts()}
    rows.sort(key=lambda r: (r.get(sort) is None, -(r.get(sort) or 0), -r["games"], str(r["champion"]), str(r["opponent_champion"])))
    return [MatchupCard(str(r["champion"]), str(r["opponent_champion"]), int(r["games"]), metric, r.get(metric),
                        counts.get((r["champion"], r["opponent_champion"]), 0), sample_label(int(r["games"]))) for r in rows]
