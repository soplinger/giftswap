"""Tests for giftswap. Run with: python3 -m pytest -q tests/"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import giftswap as gs  # noqa: E402


def make_rules(people_text, history_text=None, min_year=None, allow_household=False):
    people, excludes, issues = gs.parse_participants(people_text)
    assert not any(i.severity == "error" for i in issues), issues
    history = {}
    if history_text is not None:
        history, _ = gs.parse_history(history_text, people)
    return gs.Rules(people, excludes, history, min_year, allow_household)


def test_parse_money_converts_to_exact_cents():
    assert gs.parse_money("25") == 2500
    assert gs.parse_money("25.5") == 2550
    assert gs.parse_money("0.05") == 5
    assert gs.format_money(2550) == "$25.50"


@pytest.mark.parametrize("bad", ["abc", "1.234", "-5", "", "1,000"])
def test_parse_money_rejects_bad_amounts(bad):
    with pytest.raises(ValueError):
        gs.parse_money(bad)


def test_participant_errors_carry_line_numbers():
    text = "Alice | a | 10\nBob\nCarol | | ten\nAlice\n"
    _, _, issues = gs.parse_participants(text)
    by_line = {i.line: i for i in issues}
    assert by_line[3].severity == "error" and "budget" in by_line[3].message
    assert by_line[4].severity == "error" and "duplicate name Alice (first on line 1)" in by_line[4].message


def test_exclude_naming_unknown_person_points_at_its_line():
    _, _, issues = gs.parse_participants("A\nB\nexclude A > Zed\n")
    assert [(i.line, i.severity) for i in issues] == [(3, "error")]
    assert "unknown person Zed" in issues[0].message


def test_household_and_exclusion_reasons_are_reported():
    rules = make_rules("Alice | smith\nBob | smith\nCarol | jones\nexclude Alice > Carol\n")
    assert rules.reason("alice", "bob") == "same household (smith)"
    assert rules.reason("alice", "carol") == "excluded on line 4"
    assert rules.reason("alice", "alice") == "a person cannot give to themselves"
    assert rules.reason("bob", "carol") is None


def test_allow_household_lifts_the_household_rule():
    rules = make_rules("Alice | smith\nBob | smith\nCarol\n", allow_household=True)
    assert rules.reason("alice", "bob") is None


def test_history_blocks_only_pairs_inside_the_window():
    people_text = "Alice\nBob\nCarol\n"
    history_text = "2024: Alice > Bob\n2025: Alice > Carol\n"
    rules = make_rules(people_text, history_text, min_year=2025)
    assert rules.reason("alice", "bob") is None
    assert rules.reason("alice", "carol") == "already drawn in 2025"


def test_draw_is_valid_and_repeatable_for_a_seed():
    text = "Alice | smith\nBob | smith\nCarol | jones\nDan | jones\nErin\nFarid | lee\nGrace | lee\n"
    rules = make_rules(text)
    pairs_a, witness_a = gs.draw_pairs(rules, 7)
    pairs_b, _ = gs.draw_pairs(rules, 7)
    assert witness_a is None
    assert pairs_a == pairs_b
    entries = [(n, rules.name(g), rules.name(r)) for n, (g, r) in enumerate(pairs_a.items(), start=1)]
    assert gs.check_pairs(rules, entries) == []


def test_impossible_draw_reports_a_hall_witness_for_every_seed():
    rules = make_rules("A | x\nB | x\nC | y\n")
    for seed in range(4):
        pairs, witness = gs.draw_pairs(rules, seed)
        assert pairs is None
        givers, receivers = witness
        assert givers == ["a", "b"]
        assert receivers == ["c"]
    assert gs.describe_witness(rules, witness).startswith("no valid draw exists: 2 giver(s)")


def test_giver_with_no_options_is_named_in_the_witness():
    rules = make_rules("A\nB\nC\nexclude A > B\nexclude A > C\n")
    pairs, witness = gs.draw_pairs(rules, 0)
    assert pairs is None
    givers, receivers = witness
    assert givers == ["a"]
    assert receivers == []
    assert "can only give to 0 receiver(s) (nobody)" in gs.describe_witness(rules, witness)


def test_count_small_groups_matches_derangements():
    assert gs.count_draws(make_rules("A\nB\nC\n"), 1000) == (2, "exact")
    assert gs.count_draws(make_rules("A\nB\nC\nD\n"), 1000) == (9, "exact")


def test_count_stops_at_limit_and_says_so():
    assert gs.count_draws(make_rules("A\nB\nC\nD\n"), 5) == (5, "at least")


def test_check_pairs_flags_each_rule_break_on_its_line():
    rules = make_rules("A | h1\nB | h1\nC | h2\nD | h2\n")
    entries = [(1, "A", "B"), (2, "B", "A"), (3, "C", "D"), (4, "D", "C")]
    issues = gs.check_pairs(rules, entries)
    breaks = sorted(i.line for i in issues if "breaks a rule" in i.message)
    assert breaks == [1, 2, 3, 4]


def test_check_pairs_reports_duplicates_and_missing_people():
    rules = make_rules("A\nB\nC\n")
    entries = [(1, "A", "B"), (2, "A", "C"), (3, "C", "B")]
    messages = [i.message for i in gs.check_pairs(rules, entries)]
    assert "A already gives on line 1" in messages
    assert "B already receives on line 1" in messages
    assert "B has no giver line" in messages
    assert "A is not received by anyone" in messages


def test_assignment_parser_rejects_malformed_lines():
    entries, issues = gs.parse_assignment("# header\nAlice > Bob\nAlice - Carol\n")
    assert entries == [(2, "Alice", "Bob")]
    assert [(i.line, i.severity) for i in issues] == [(3, "error")]


def test_history_names_outside_the_file_are_ignored_with_warning():
    people, _, _ = gs.parse_participants("Alice\nBob\n")
    history, issues = gs.parse_history("2025: Alice > Zed\n2025: Bob > Alice\n", people)
    assert history == {("bob", "alice"): 2025}
    assert [(i.line, i.severity) for i in issues] == [(1, "warning")]


def test_reveal_files_get_unique_slugs(tmp_path):
    rules = make_rules("Ann Lee\nAnn-Lee\nBo\n")
    pairs, _ = gs.draw_pairs(rules, 1)
    paths = gs.write_reveals(rules, pairs, tmp_path, "test")
    names = sorted(p.name for p in paths)
    assert names == ["ann-lee-2.txt", "ann-lee.txt", "bo.txt"]
    assert "gift giver for" in paths[0].read_text(encoding="utf-8")


def test_cli_draw_writes_assignment_and_reveals(tmp_path):
    people = tmp_path / "people.txt"
    people.write_text("Alice | smith | 25 | tea\nBob | smith | 25\nCarol | jones\nDan | jones | 20.5\n", encoding="utf-8")
    out = tmp_path / "draw.txt"
    reveals = tmp_path / "reveals"
    code = gs.main(["draw", str(people), "--seed", "3", "--out", str(out), "--reveals", str(reveals)])
    assert code == gs.EXIT_OK
    assert len(out.read_text(encoding="utf-8").strip().splitlines()) == 5  # header + 4 pairs
    assert len(list(reveals.glob("*.txt"))) == 4
    assert gs.main(["check", str(people), str(out)]) == gs.EXIT_OK


def test_cli_exit_codes_for_lint_and_check(tmp_path):
    impossible = tmp_path / "bad.txt"
    impossible.write_text("A | x\nB | x\nC | y\n", encoding="utf-8")
    assert gs.main(["lint", str(impossible)]) == gs.EXIT_FAIL

    warned = tmp_path / "warned.txt"
    warned.write_text("A\nB\nC\nexclude A > B\nexclude A > B\n", encoding="utf-8")
    assert gs.main(["lint", str(warned)]) == gs.EXIT_OK
    assert gs.main(["lint", str(warned), "--strict"]) == gs.EXIT_FAIL

    broken = tmp_path / "broken.txt"
    broken.write_text("A\nB\nC | x | nope\n", encoding="utf-8")
    assert gs.main(["draw", str(broken)]) == gs.EXIT_USAGE
    assert gs.main(["draw", str(tmp_path / "missing.txt")]) == gs.EXIT_USAGE
