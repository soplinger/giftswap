# 🎁 giftswap

**Fair gift-exchange draws from one plain-text file, offline.**

giftswap takes a list of participants, their households, budgets and wishes, plus any
pairs that must not draw each other, and produces a seeded draw in which everyone gives
to exactly one person and receives from exactly one person. If no valid draw exists, it
tells you exactly which people are the problem instead of retrying forever.

It is Python 3 standard library only. There are no servers, accounts, network calls or
API keys.

## Features

- **Plain-text participant file.** One person per line: `Name | household | budget | note`. Only the name is required.
- **Line-numbered errors.** Bad budgets, duplicate names, unknown names in rules and malformed lines are reported as `line N: error: ...`.
- **Household rule.** Members of the same household never draw each other. Switch it off with `--allow-household`.
- **Explicit exclusions.** `exclude Erin > Farid` blocks one direction only.
- **Repeat avoidance.** A history file of past draws (`2025: Alice > Carol`) blocks pairs from the last N years (`--avoid-years`, default 1).
- **Exact, always-correct draws.** Draws are a bipartite perfect matching (Kuhn's algorithm), so a valid draw is found whenever one exists.
- **Proof of impossibility.** When no draw exists, giftswap names the givers who can only reach too few receivers, using a Hall-set witness.
- **Seeded and repeatable.** The same `--seed` always gives the same draw, so you can re-run and audit it.
- **Lint.** `lint` checks the file, flags redundant exclusions and confirms a draw is possible. `--strict` fails on warnings too.
- **Explain.** `explain` lists every giver's possible receivers and why each blocked one is blocked.
- **Count.** `count` counts every valid draw for small groups by exact backtracking, with a limit and a search budget.
- **Check an existing draw.** `check` verifies a `Giver > Receiver` file against every rule and against full coverage.
- **Shareable outputs.** `--out` writes the draw as text, `--json` prints it as JSON, and `--reveals DIR` writes one private reveal note per giver.
- **CI-friendly exit codes.** `0` success, `1` no valid draw or a failed check, `2` bad input.

## Quick start

Save this as `people.txt`:

```text
# Office gift exchange 2026
# name | household | budget | note
Alice   | smith | 25 | likes tea and old paperbacks
Bob     | smith | 25 | size M wool socks
Carol   | jones | 20 | any mug is fine
Dan     | jones | 20 |
Erin    | | 30 | likes plants, no candles
Farid   | lee | 25 |
Grace   | lee | 25 | enjoys board games
exclude Erin > Farid
```

Save last year's draw as `history.txt`:

```text
2025: Alice > Carol
2025: Bob > Erin
2025: Carol > Bob
2025: Dan > Grace
2025: Erin > Dan
2025: Farid > Alice
2025: Grace > Farid
```

Then run:

```sh
python3 giftswap.py lint people.txt --history history.txt
python3 giftswap.py draw people.txt --history history.txt --seed 2026 --out draw.txt --reveals reveals/
python3 giftswap.py check people.txt draw.txt --history history.txt
python3 giftswap.py explain people.txt --history history.txt
python3 giftswap.py count people.txt --history history.txt
```

`draw.txt` will contain lines like `Alice > Grace`, and `reveals/` gets one file per giver,
such as `reveals/alice.txt`, that says who they are buying for, their budget and that
person's note.

## How to run

Requires Python 3. There is nothing to install.

```sh
python3 giftswap.py --help
python3 giftswap.py draw people.txt --json
```

Commands:

| command   | what it does                                              |
|-----------|-----------------------------------------------------------|
| `lint`    | validate the file and confirm a valid draw exists         |
| `draw`    | make a seeded draw (`--json`, `--out`, `--reveals`)       |
| `check`   | verify an existing `Giver > Receiver` file                |
| `explain` | list allowed and blocked receivers for every giver        |
| `count`   | count valid draws (small groups)                          |

Shared options: `--history FILE`, `--year YEAR`, `--avoid-years N`, `--allow-household`.

## How to test

```sh
python3 -m pytest -q tests/
```

The tests cover money parsing, line-numbered parse errors, household and exclusion
reasons, history windows, repeatable draws, Hall witnesses for impossible groups,
derangement counts, assignment checks, reveal-file slugs, and CLI exit codes.

## Input format

- Lines starting with `#` and blank lines are ignored.
- Person lines are `Name | household | budget | note`. Trailing fields are optional. Names may contain letters, digits, spaces, `.`, `'` and `-`, and must start with a letter. Budgets are amounts like `25` or `25.50`. A note may not contain `|`.
- Rule lines are `exclude Giver > Receiver`.
- History lines are `YEAR: Giver > Receiver`. Names not in the participant file are skipped with a warning.
- Assignment files (for `check`) are `Giver > Receiver`, one pair per line.

## Notes and limits

- The draw is exact. The counting command is exhaustive, so it is only practical for small groups. It stops at `--limit` and at a fixed search budget, and says which one it hit.
- Reveal files contain the recipient's note. Share each one only with its giver.
- Names are matched case-insensitively.
