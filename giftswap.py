#!/usr/bin/env python3
"""giftswap: fair gift-exchange draws from one plain-text participant file.

Stdlib only. Every draw is an exact bipartite perfect matching (givers to
receivers), so a draw either succeeds or comes with a Hall-style proof of why
no valid draw exists. Run `python3 giftswap.py --help` for the commands.
"""
from __future__ import annotations

import argparse
import json
import random
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_USAGE = 2

COUNT_NODE_BUDGET = 2_000_000

NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9 .'\-]{0,39}$")
MONEY_RE = re.compile(r"^(\d+)(?:\.(\d{1,2}))?$")
ARROW_RE = re.compile(r"^(.+?)\s*>\s*(.+?)$")
HISTORY_RE = re.compile(r"^(\d{4})\s*:\s*(.+?)\s*>\s*(.+?)$")


@dataclass(frozen=True)
class Issue:
    line: int
    severity: str  # "error" or "warning"
    message: str

    def __str__(self) -> str:
        where = f"line {self.line}: " if self.line else ""
        return f"{where}{self.severity}: {self.message}"


@dataclass
class Person:
    name: str
    household: str = ""
    budget_cents: int | None = None
    note: str = ""
    line: int = 0

    @property
    def key(self) -> str:
        return self.name.casefold()


@dataclass
class Rules:
    people: dict[str, Person]
    excludes: dict[tuple[str, str], int] = field(default_factory=dict)
    history: dict[tuple[str, str], int] = field(default_factory=dict)
    min_year: int | None = None  # history pairs from this year on are blocked
    allow_household: bool = False

    def reason(self, gk: str, rk: str) -> str | None:
        """Return why giver gk may not give to receiver rk, or None if allowed."""
        if gk == rk:
            return "a person cannot give to themselves"
        giver, receiver = self.people[gk], self.people[rk]
        if (
            not self.allow_household
            and giver.household
            and giver.household.casefold() == receiver.household.casefold()
        ):
            return f"same household ({giver.household})"
        if (gk, rk) in self.excludes:
            return f"excluded on line {self.excludes[(gk, rk)]}"
        year = self.history.get((gk, rk))
        if year is not None and self.min_year is not None and year >= self.min_year:
            return f"already drawn in {year}"
        return None

    def candidates(self, gk: str) -> list[str]:
        return [rk for rk in self.people if self.reason(gk, rk) is None]

    def name(self, key: str) -> str:
        return self.people[key].name


def parse_money(text: str) -> int:
    """Turn '25' or '25.5' into integer cents. Rejects anything else."""
    match = MONEY_RE.match(text)
    if not match:
        raise ValueError(f"budget '{text}' is not an amount like 25 or 25.50")
    cents = (match.group(2) or "").ljust(2, "0")
    return int(match.group(1)) * 100 + int(cents)


def format_money(cents: int) -> str:
    return f"${cents // 100}.{cents % 100:02d}"


def parse_participants(text: str):
    """Parse the participant file. Returns (people, excludes, issues).

    Person lines:   Name | household | budget | note   (trailing fields optional)
    Exclude lines:  exclude Giver > Receiver
    """
    people: dict[str, Person] = {}
    pending_excludes: list[tuple[int, str, str]] = []
    issues: list[Issue] = []

    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.lower().startswith("exclude "):
            match = ARROW_RE.match(line[len("exclude "):].strip())
            if not match:
                issues.append(Issue(lineno, "error", "expected 'exclude Giver > Receiver'"))
                continue
            pending_excludes.append((lineno, match.group(1).strip(), match.group(2).strip()))
            continue

        parts = [part.strip() for part in line.split("|")]
        if len(parts) > 4:
            issues.append(Issue(lineno, "error", "too many fields; use name | household | budget | note"))
            continue
        name = parts[0]
        if not NAME_RE.match(name):
            issues.append(Issue(lineno, "error", f"invalid name '{name}' (letters, digits, spaces, . ' - only)"))
            continue
        if name.casefold() in people:
            first = people[name.casefold()].line
            issues.append(Issue(lineno, "error", f"duplicate name {name} (first on line {first})"))
            continue
        budget = None
        if len(parts) > 2 and parts[2]:
            try:
                budget = parse_money(parts[2])
            except ValueError as exc:
                issues.append(Issue(lineno, "error", str(exc)))
                continue
        household = parts[1] if len(parts) > 1 else ""
        note = parts[3] if len(parts) > 3 else ""
        people[name.casefold()] = Person(name, household, budget, note, lineno)

    excludes: dict[tuple[str, str], int] = {}
    for lineno, gname, rname in pending_excludes:
        gk, rk = gname.casefold(), rname.casefold()
        if gk not in people or rk not in people:
            unknown = gname if gk not in people else rname
            issues.append(Issue(lineno, "error", f"exclude names unknown person {unknown}"))
        elif gk == rk:
            issues.append(Issue(lineno, "error", f"{gname} cannot be excluded from giving to themselves"))
        elif (gk, rk) in excludes:
            issues.append(Issue(lineno, "warning", f"duplicate exclude {gname} > {rname}"))
        else:
            excludes[(gk, rk)] = lineno

    if len(people) < 2 and not any(issue.severity == "error" for issue in issues):
        issues.append(Issue(0, "error", "a gift exchange needs at least two people"))

    issues.sort(key=lambda issue: issue.line)
    return people, excludes, issues


def parse_history(text: str, people: dict[str, Person]):
    """Parse past draws: lines like '2025: Alice > Bob'. Returns (history, issues)."""
    history: dict[tuple[str, str], int] = {}
    issues: list[Issue] = []
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = HISTORY_RE.match(line)
        if not match:
            issues.append(Issue(lineno, "error", "expected 'YEAR: Giver > Receiver'"))
            continue
        year, gname, rname = int(match.group(1)), match.group(2), match.group(3)
        gk, rk = gname.casefold(), rname.casefold()
        if gk not in people or rk not in people:
            issues.append(Issue(lineno, "warning", "history names someone not in the participant file; ignored"))
            continue
        if gk == rk:
            issues.append(Issue(lineno, "error", f"{gname} cannot give to themselves"))
            continue
        history[(gk, rk)] = max(year, history.get((gk, rk), year))
    return history, issues


def load_rules(args) -> tuple[Rules, list[Issue]]:
    people_text = args.people.read_text(encoding="utf-8")
    people, excludes, issues = parse_participants(people_text)
    history: dict[tuple[str, str], int] = {}
    if args.history:
        history, history_issues = parse_history(args.history.read_text(encoding="utf-8"), people)
        issues.extend(history_issues)
    min_year = None
    if history:
        newest = max(history.values())
        ref_year = args.year if args.year is not None else newest + 1
        min_year = ref_year - args.avoid_years
    rules = Rules(people, excludes, history, min_year, args.allow_household)
    return rules, issues


def max_matching(graph: dict[str, list[str]], givers: list[str]):
    """Kuhn's augmenting-path algorithm. Returns (giver->receiver, receiver->giver)."""
    match_g: dict[str, str] = {}
    match_r: dict[str, str] = {}

    def augment(g: str, seen: set[str]) -> bool:
        for r in graph[g]:
            if r in seen:
                continue
            seen.add(r)
            if r not in match_r or augment(match_r[r], seen):
                match_r[r] = g
                match_g[g] = r
                return True
        return False

    for g in givers:
        augment(g, set())
    return match_g, match_r


def hall_witness(graph, givers, match_g, match_r):
    """Find a set of givers that together can only reach fewer receivers.

    Starting from an unmatched giver, follow alternating paths. The givers reached
    can only give to the receivers reached, and there is one fewer receiver.
    """
    free = next(g for g in givers if g not in match_g)
    seen_g = {free}
    seen_r: set[str] = set()
    stack = [free]
    while stack:
        g = stack.pop()
        for r in graph[g]:
            if r in seen_r:
                continue
            seen_r.add(r)
            holder = match_r.get(r)
            if holder is not None and holder not in seen_g:
                seen_g.add(holder)
                stack.append(holder)
    return sorted(seen_g), sorted(seen_r)


def draw_pairs(rules: Rules, seed: int):
    """Return (pairs, witness). pairs maps giver key -> receiver key, or None.

    The seed fixes the order of givers and options, so a seed always gives the
    same draw. Exactly one of pairs or witness is set.
    """
    rng = random.Random(seed)
    givers = list(rules.people)
    rng.shuffle(givers)
    graph: dict[str, list[str]] = {}
    for g in givers:
        options = rules.candidates(g)
        rng.shuffle(options)
        graph[g] = options
    match_g, match_r = max_matching(graph, givers)
    if len(match_g) == len(givers):
        return {g: match_g[g] for g in rules.people}, None
    return None, hall_witness(graph, givers, match_g, match_r)


def describe_witness(rules: Rules, witness) -> str:
    givers, receivers = witness
    giver_names = ", ".join(rules.name(k) for k in givers)
    receiver_names = ", ".join(rules.name(k) for k in receivers) or "nobody"
    return (
        f"no valid draw exists: {len(givers)} giver(s) ({giver_names}) can only give to "
        f"{len(receivers)} receiver(s) ({receiver_names})"
    )


def count_draws(rules: Rules, limit: int):
    """Count valid draws by backtracking. Returns (count, status).

    status is "exact" when the full count was found, "at least" when it
    stopped at limit, and "gave up" when the search budget ran out.
    """
    cand = {g: rules.candidates(g) for g in rules.people}
    order = sorted(rules.people, key=lambda g: len(cand[g]))
    used: set[str] = set()
    count = 0
    nodes = 0
    aborted = False

    def walk(i: int) -> None:
        nonlocal count, nodes, aborted
        nodes += 1
        if nodes > COUNT_NODE_BUDGET:
            aborted = True
        if aborted or count >= limit:
            return
        if i == len(order):
            count += 1
            return
        for r in cand[order[i]]:
            if r in used:
                continue
            used.add(r)
            walk(i + 1)
            used.discard(r)
            if aborted or count >= limit:
                return

    walk(0)
    if aborted:
        return count, "gave up"
    return count, "exact" if count < limit else "at least"


def parse_assignment(text: str):
    """Parse 'Giver > Receiver' lines. Returns (entries, issues)."""
    entries: list[tuple[int, str, str]] = []
    issues: list[Issue] = []
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = ARROW_RE.match(line)
        if not match:
            issues.append(Issue(lineno, "error", "expected 'Giver > Receiver'"))
            continue
        entries.append((lineno, match.group(1).strip(), match.group(2).strip()))
    return entries, issues


def check_pairs(rules: Rules, entries) -> list[Issue]:
    issues: list[Issue] = []
    seen_givers: dict[str, int] = {}
    seen_receivers: dict[str, int] = {}
    for lineno, gname, rname in entries:
        gk, rk = gname.casefold(), rname.casefold()
        if gk not in rules.people or rk not in rules.people:
            unknown = gname if gk not in rules.people else rname
            issues.append(Issue(lineno, "error", f"unknown name {unknown}"))
            continue
        if gk in seen_givers:
            issues.append(Issue(lineno, "error", f"{gname} already gives on line {seen_givers[gk]}"))
        else:
            seen_givers[gk] = lineno
        if rk in seen_receivers:
            issues.append(Issue(lineno, "error", f"{rname} already receives on line {seen_receivers[rk]}"))
        else:
            seen_receivers[rk] = lineno
        reason = rules.reason(gk, rk)
        if reason:
            issues.append(Issue(lineno, "error", f"{gname} > {rname} breaks a rule: {reason}"))
    for key in rules.people:
        if key not in seen_givers:
            issues.append(Issue(0, "error", f"{rules.name(key)} has no giver line"))
        if key not in seen_receivers:
            issues.append(Issue(0, "error", f"{rules.name(key)} is not received by anyone"))
    return issues


def write_reveals(rules: Rules, pairs: dict[str, str], directory: Path, label: str) -> list[Path]:
    directory.mkdir(parents=True, exist_ok=True)
    used_slugs: set[str] = set()
    paths = []
    for gk in rules.people:
        giver = rules.people[gk]
        base = re.sub(r"[^a-z0-9]+", "-", gk).strip("-") or "person"
        slug, n = base, 2
        while slug in used_slugs:
            slug = f"{base}-{n}"
            n += 1
        used_slugs.add(slug)
        receiver = rules.people[pairs[gk]]
        budget = format_money(giver.budget_cents) if giver.budget_cents is not None else "not set"
        lines = [
            f"Hello {giver.name},",
            "",
            f"You are the gift giver for: {receiver.name}",
            f"Your budget: {budget}",
        ]
        if receiver.note:
            lines.append(f"{receiver.name}'s note: {receiver.note}")
        lines += ["", f"({label})", ""]
        path = directory / f"{slug}.txt"
        path.write_text("\n".join(lines), encoding="utf-8")
        paths.append(path)
    return paths


def render_pairs(rules: Rules, pairs: dict[str, str], seed: int) -> str:
    out = [f"Draw seed {seed}: {len(pairs)} pairs"]
    for gk in rules.people:
        out.append(f"  {rules.name(gk)} -> {rules.name(pairs[gk])}")
    return "\n".join(out)


def pairs_as_text(rules: Rules, pairs: dict[str, str], seed: int) -> str:
    lines = [f"# giftswap draw, seed {seed}"]
    lines += [f"{rules.name(gk)} > {rules.name(pairs[gk])}" for gk in rules.people]
    return "\n".join(lines) + "\n"


def print_issues(issues: list[Issue]) -> bool:
    """Print issues to stderr. Returns True if any is an error."""
    for issue in issues:
        print(issue, file=sys.stderr)
    return any(issue.severity == "error" for issue in issues)


def cmd_lint(args, rules: Rules, issues: list[Issue]) -> int:
    for gk, rk in rules.excludes:
        giver, receiver = rules.people[gk], rules.people[rk]
        if (
            not rules.allow_household
            and giver.household
            and giver.household.casefold() == receiver.household.casefold()
        ):
            issues.append(
                Issue(rules.excludes[(gk, rk)], "warning", "exclude is redundant: same-household rule already blocks it")
            )
    issues.sort(key=lambda issue: issue.line)
    has_errors = print_issues(issues)
    warnings = [issue for issue in issues if issue.severity == "warning"]
    if not has_errors:
        pairs, witness = draw_pairs(rules, 0)
        if witness:
            print(f"error: {describe_witness(rules, witness)}", file=sys.stderr)
            has_errors = True
    if has_errors:
        return EXIT_FAIL
    if args.strict and warnings:
        return EXIT_FAIL
    print(f"OK: {len(rules.people)} people, {len(rules.excludes)} exclusions, "
          f"{len(rules.history)} history pairs, {len(warnings)} warning(s); a valid draw exists")
    return EXIT_OK


def cmd_draw(args, rules: Rules, issues: list[Issue]) -> int:
    if print_issues(issues):
        return EXIT_USAGE
    pairs, witness = draw_pairs(rules, args.seed)
    if witness:
        print(f"giftswap: {describe_witness(rules, witness)}", file=sys.stderr)
        return EXIT_FAIL
    if args.json:
        payload = {
            "seed": args.seed,
            "pairs": [
                {"giver": rules.name(gk), "receiver": rules.name(pairs[gk])} for gk in rules.people
            ],
        }
        print(json.dumps(payload, indent=2))
    else:
        print(render_pairs(rules, pairs, args.seed))
    if args.out:
        args.out.write_text(pairs_as_text(rules, pairs, args.seed), encoding="utf-8")
    if args.reveals:
        label = f"draw seed {args.seed}"
        write_reveals(rules, pairs, args.reveals, label)
        if not args.json:
            print(f"Wrote {len(rules.people)} reveal files to {args.reveals}")
    return EXIT_OK


def cmd_check(args, rules: Rules, issues: list[Issue]) -> int:
    if print_issues(issues):
        return EXIT_USAGE
    entries, parse_issues = parse_assignment(args.assignment.read_text(encoding="utf-8"))
    if print_issues(parse_issues):
        return EXIT_USAGE
    problems = check_pairs(rules, entries)
    if problems:
        for issue in problems:
            print(issue)
        print(f"FAIL: {len(problems)} problem(s) in {args.assignment}")
        return EXIT_FAIL
    print(f"OK: {args.assignment} is a valid draw for {len(rules.people)} people")
    return EXIT_OK


def cmd_explain(args, rules: Rules, issues: list[Issue]) -> int:
    if print_issues(issues):
        return EXIT_USAGE
    for gk in rules.people:
        allowed, blocked = [], []
        for rk in rules.people:
            if rk == gk:
                continue
            reason = rules.reason(gk, rk)
            if reason is None:
                allowed.append(rules.name(rk))
            else:
                blocked.append(f"{rules.name(rk)} ({reason})")
        print(f"{rules.name(gk)}: {len(allowed)} of {len(allowed) + len(blocked)} possible")
        for entry in blocked:
            print(f"    blocked: {entry}")
    return EXIT_OK


def cmd_count(args, rules: Rules, issues: list[Issue]) -> int:
    if print_issues(issues):
        return EXIT_USAGE
    count, status = count_draws(rules, args.limit)
    if status == "gave up":
        print(f"gave up after {COUNT_NODE_BUDGET} search steps; try fewer people or more exclusions")
        return EXIT_FAIL
    prefix = "" if status == "exact" else "at least "
    print(f"{prefix}{count} valid draw(s) ({status})")
    return EXIT_OK


HANDLERS = {
    "lint": cmd_lint,
    "draw": cmd_draw,
    "check": cmd_check,
    "explain": cmd_explain,
    "count": cmd_count,
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="giftswap",
        description="Fair gift-exchange draws from a plain-text participant file.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    shared = argparse.ArgumentParser(add_help=False)
    shared.add_argument("people", type=Path, help="participant file")
    shared.add_argument("--history", type=Path, help="past draws, one 'YEAR: Giver > Receiver' per line")
    shared.add_argument("--year", type=int, help="draw year (default: one after the newest history year)")
    shared.add_argument("--avoid-years", type=int, default=1, metavar="N",
                        help="block pairs drawn in the last N years (default 1)")
    shared.add_argument("--allow-household", action="store_true",
                        help="let members of the same household draw each other")

    lint = sub.add_parser("lint", parents=[shared], help="check the participant file and confirm a draw exists")
    lint.add_argument("--strict", action="store_true", help="treat warnings as failures")

    draw = sub.add_parser("draw", parents=[shared], help="make a seeded, valid draw")
    draw.add_argument("--seed", type=int, default=1, help="seed for a repeatable draw (default 1)")
    draw.add_argument("--json", action="store_true", help="print the draw as JSON")
    draw.add_argument("--out", type=Path, help="write the draw as 'Giver > Receiver' lines")
    draw.add_argument("--reveals", type=Path, metavar="DIR", help="write one reveal text file per giver")

    check = sub.add_parser("check", parents=[shared], help="verify an existing 'Giver > Receiver' file")
    check.add_argument("assignment", type=Path, help="assignment file to verify")

    sub.add_parser("explain", parents=[shared], help="list each giver's allowed and blocked receivers")

    count = sub.add_parser("count", parents=[shared], help="count every valid draw for small groups")
    count.add_argument("--limit", type=int, default=100_000, help="stop counting at this many (default 100000)")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        rules, issues = load_rules(args)
    except OSError as exc:
        print(f"giftswap: {exc}", file=sys.stderr)
        return EXIT_USAGE
    return HANDLERS[args.command](args, rules, issues)


if __name__ == "__main__":
    sys.exit(main())
