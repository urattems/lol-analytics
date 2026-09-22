"""Adversarial, deterministic checks of session scope, evidence and public output."""
from copy import deepcopy
import io
import json
import zipfile

import polars as pl
import pytest

from analytics.session_review import prepare_review_history, build_session_review, session_catalog
from analytics.sessions import assign_sessions
from analytics.session_export import session_export_payload, build_session_package, session_package_filename


START = 1_780_000_000_000


def game(index, *, start=None, **overrides):
    return {"match_id": f"SYNTH_{index:04d}", "game_creation": START + index * 40 * 60_000 if start is None else start,
            "duration": 1800, "champion": "Shyvana", "role": "JUNGLE", "win": index % 2,
            "queue_id": 420, "kills": 4, "deaths": 2, "assists": 6, "cs_total": 180,
            "gold_earned": 12000, "damage_dealt": 18000, "vision_score": 30,
            "timeline_available": True, "gold_diff_15": 300, "team_gold_diff_10": -2000,
            "team_gold_diff_15": 0, "team_gold_diff_20": 2000, "deaths_before_10": 0,
            "gold_state_10": "BEHIND", "gold_state_20": "AHEAD", **overrides}


def review_of(rows, anchor=None):
    return build_session_review(prepare_review_history(pl.from_dicts(rows, infer_schema_length=None)), anchor)


@pytest.mark.parametrize("size", [1, 2, 4, 8])
@pytest.mark.parametrize("coverage", [0, .5, 1])
def test_summary_order_and_actual_coverage(size, coverage):
    rows = [game(i, timeline_available=i < int(size * coverage)) for i in range(size)]
    review = review_of(list(reversed(rows)))
    summary = review["summary"]
    assert summary["games"] == size
    assert summary["wins"] == size // 2
    assert summary["losses"] == size - size // 2
    assert summary["match_ids"] == [r["match_id"] for r in rows]
    assert summary["timeline_games"] == int(size * coverage)
    assert review["metrics"]["kda_mean"] == {"value": 5, "n": size, "aggregation": "mean"}
    assert review["metrics"]["gold_diff_15"]["n"] == int(size * coverage)
    assert review["phases"][0]["median"] == (-2000 if int(size * coverage) else None)
    assert review["phases"][0]["n"] == int(size * coverage)
    assert [r["session_game_number"] for r in review["matches"]] == list(range(1, size + 1))
    assert len(review["highlights"]) >= 2


@pytest.mark.parametrize("result", [0, 1, None])
def test_outcomes_are_counted_without_guessing(result):
    review = review_of([game(i, win=result) for i in range(4)])
    summary = review["summary"]
    assert summary["wins"] == (4 if result == 1 else 0)
    assert summary["losses"] == (4 if result == 0 else 0)
    assert summary["winrate"] == (None if result is None else 100 * result)
    assert summary["unknown_results"] == (4 if result is None else 0)


@pytest.mark.parametrize("prior_size", [0, 4, 5, 12, 20, 27])
def test_baseline_excludes_entire_current_session_future_and_other_role(prior_size):
    prior = [game(i, start=START - (prior_size - i) * 86_400_000) for i in range(prior_size)]
    wrong = [game(100 + i, start=START - (i + 1) * 86_400_000 + 3_600_000, role="UTILITY", kills=50000) for i in range(6)]
    current = [game(200 + i, start=START + i * 40 * 60_000, kills=10000) for i in range(4)]
    future = [game(300, start=START + 2 * 86_400_000, kills=99999)]
    review = review_of(prior + wrong + current + future, current[0]["match_id"])
    comparison = review["comparisons"][0]
    assert comparison["baseline_n"] == min(prior_size, 20)
    assert comparison["session_n"] == 4
    assert comparison["eligible"] == (prior_size >= 5)
    assert set(comparison["baseline_match_ids"]) == {r["match_id"] for r in prior[-20:]}
    assert comparison["baseline"]["kda_mean"]["value"] == (5 if prior_size else None)
    assert comparison["session"]["kda_mean"]["value"] == 5003
    assert not any("50000" in h["text"] for h in review["highlights"])


def test_mixed_champion_roles_and_missing_metric_effective_samples():
    prior = [game(i, start=START - (i + 1) * 86_400_000, gold_diff_15=None if i < 4 else 0) for i in range(8)]
    current = [game(100 + i, start=START + i * 40 * 60_000, gold_diff_15=4000,
                    champion="Leona" if i > 1 else "Shyvana", role="UTILITY" if i > 1 else "JUNGLE") for i in range(4)]
    review = review_of(prior + current)
    jungle = next(c for c in review["comparisons"] if c["role"] == "JUNGLE")
    support = next(c for c in review["comparisons"] if c["role"] == "UTILITY")
    assert jungle["eligible"] and jungle["baseline_n"] == 8
    assert jungle["baseline"]["gold_diff_15"]["n"] == 4
    assert not support["eligible"] and support["baseline_n"] == 0
    assert not any(h["kind"] == "baseline_gold" for h in review["highlights"])
    prior[0]["gold_diff_15"] = 0
    assert any(h["kind"] == "baseline_gold" for h in review_of(prior + current)["highlights"])


def test_checkpoint_missing_values_are_not_zero_or_full_coverage():
    review = review_of([game(0, team_gold_diff_15=None, kills=None), game(1, team_gold_diff_20=None)])
    assert review["phases"] == [{"minute": 10, "median": -2000, "n": 2}, {"minute": 15, "median": 0, "n": 1}, {"minute": 20, "median": 2000, "n": 1}]
    assert review["metrics"]["kda_mean"]["n"] == 1
    assert review["summary"]["timeline_games"] == 2


def test_highlight_unique_comeback_and_ranking_are_explicit():
    rows = [game(0), game(1, gold_state_10="AHEAD"), game(2, gold_state_20="BEHIND"), game(3, gold_state_10=None)]
    review = review_of(rows)
    first = review["highlights"][0]
    assert first["kind"] == "trajectory" and first["n"] == 3
    assert first["match_id"] == rows[0]["match_id"]
    assert "seul passage" in first["text"]
    assert [h["score"] for h in review["highlights"]] == sorted([h["score"] for h in review["highlights"]], reverse=True)
    rows[1]["gold_state_10"] = "BEHIND"
    assert not any(h["kind"] == "trajectory" for h in review_of(rows)["highlights"])
    text = " ".join(h["text"].lower() for h in review["highlights"])
    for forbidden in ("tilt", "fatigue", "confiance", "tu devrais", "vous devriez", "causé", "significatif", "meilleur joueur"):
        assert forbidden not in text


@pytest.mark.parametrize("invalid", [None, -1, 253370764800001])
def test_invalid_dates_do_not_invent_sessions(invalid):
    rows = [game(0), game(1), game(2, game_creation=invalid)]
    prepared = prepare_review_history(pl.from_dicts(rows))
    assert prepared.filter(pl.col("match_id") == rows[-1]["match_id"])["session_id"][0] is None
    assert sum(s["games"] for s in session_catalog(prepared)) == 2
    assert prepared.select("match_id", "session_id").equals(assign_sessions(pl.from_dicts(rows)).select("match_id", "session_id"))


@pytest.mark.parametrize("duration", [None, -1, float("nan"), float("inf"), 10**15])
def test_unknown_end_cannot_silently_join_next_game(duration):
    prepared = prepare_review_history(pl.from_dicts([game(0, duration=duration), game(1)], strict=False))
    assert len(session_catalog(prepared)) == 2
    review = build_session_review(prepared, "SYNTH_0000")
    assert review["summary"]["end_ms"] is None
    assert session_export_payload(review, "QA#TEST")["matches"][0]["duration_seconds"] is None


def test_empty_single_gap_change_and_anchor_survive_backfill():
    assert build_session_review(prepare_review_history(pl.DataFrame())) is None
    rows = [game(0), game(1, start=START + 80 * 60_000)]  # 50 min pause.
    assert len(session_catalog(prepare_review_history(pl.from_dicts(rows), 45))) == 2
    assert len(session_catalog(prepare_review_history(pl.from_dicts(rows), 60))) == 1
    original = review_of(rows)
    older = game(100, start=START - 10 * 86_400_000)
    revised = review_of([older] + rows, original["summary"]["anchor_match_id"])
    assert revised["summary"]["match_ids"] == original["summary"]["match_ids"]
    assert revised["summary"]["session_id"] != original["summary"]["session_id"]
    assert review_of(rows, "deleted")["summary"]["match_ids"] == original["summary"]["match_ids"]


@pytest.mark.parametrize("size", [1, 8])
def test_session_zip_exact_order_scope_and_no_external_identity(size):
    rows = [game(i, puuid="PRIVATE-PLAYER", opponent_puuid="PRIVATE-MATE", riot_id="PRIVATE-NAME",
                 extra={"secret": "PRIVATE-OPAQUE"}, gold_diff_15=float("nan") if i == 0 else 200) for i in range(size)]
    rows.append(game(999, start=START - 7 * 86_400_000))
    review = review_of(rows)
    archive = zipfile.ZipFile(io.BytesIO(build_session_package(review, "<script>alert(1)</script>#DEMO")))
    assert archive.namelist() == ["session_review.md", "session_matches.jsonl"]
    assert archive.testzip() is None
    contents = "\n".join(archive.read(name).decode() for name in archive.namelist())
    for forbidden in ("PRIVATE-", "NaN", "Infinity", "<script>"):
        assert forbidden not in contents
    exported = [json.loads(line) for line in archive.read("session_matches.jsonl").splitlines()]
    assert [r["match_id"] for r in exported] == [r["match_id"] for r in rows[:size]]
    assert all(r["session_game_number"] == i + 1 for i, r in enumerate(exported))
    payload = session_export_payload(review, "Main#DEMO")
    assert payload["session"]["games"] == size
    assert payload["metrics"]["gold_diff_15"]["n"] == size - 1
    assert exported[0]["checkpoints"][1]["gold_diff"] is None
    filename = session_package_filename("../../CON\\" + "🦄" * 300, size)
    assert filename.endswith(".zip") and "/" not in filename and "\\" not in filename and len(filename) < 140
