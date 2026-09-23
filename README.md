# polywatch

Find Polymarket traders who win consistently, with market-making bots and one-shot accounts
filtered out, and follow their bets live in your terminal.

## Install

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync                  # development checkout
uv run polywatch

uv tool install .        # or install the `polywatch` command globally
```

No API keys are needed. polywatch only reads Polymarket's public data.

## Usage

`polywatch` opens the terminal app. The first run scans the leaderboards (a few minutes), then
keeps a live feed of the top traders' bets.

| Key | Action |
|---|---|
| `Enter` | trader details (traders pane) / open the market (feed pane) |
| `o` | open the trader profile or market in your browser |
| `p` / `b` | pin / ban the selected trader |
| `a` | add a trader by wallet, profile URL or username |
| `f` | show or hide flagged traders |
| `n` | desktop alerts on/off |
| `d` | rescan |
| `Tab` | switch pane |
| `q` | quit |

Feed rows look like this:

```
14:03:12  BUY  alice  #1 · 71% win · ★
          Chelsea vs Brentford — Chelsea
          @ 58¢ → pays 1.72x   $4,210 🔥3.4x   now 59¢ (+1¢)
          polymarket.com/event/epl-che-bre/epl-che-bre-che
```

`@ 58¢` is the trader's average price (also the implied probability). `pays 1.72x` is what a win returns per
dollar. 🔥 marks a bet at least 3x the trader's usual size. `now` is the current price, so you can see
whether you've missed the move.

Dimmed rows are buys that aren't worth copying: a price of 95¢ or more (at most a few cents to gain),
or one side of a trade that bought both outcomes of a market. They never trigger an alert.

`polywatch discover [--limit N] [--top N] [--show-excluded]` runs the same scan without the UI
and prints the ranking (handy for cron or for checking why someone was excluded).

## How traders are ranked

Candidates come from the monthly, weekly and all-time profit leaderboards. For each one, polywatch
looks at bets resolved in the last 90 days. That includes losing positions the trader never cashed
out; the leaderboard's own history hides those. Bets on voided markets (resolved 50/50 after a
retirement, forfeit or cancellation) are left out, because they pay back the stake whatever was
predicted.

A trader is **excluded** if they:

- haven't traded in 14 days
- have fewer than 15 resolved bets, or aren't profitable
- made more than half their profit on a single bet
- have an account younger than 30 days
- look like a bot or market maker (tiny margin on huge volume, maker rebates above 0.5% of what they
  bet, tens of thousands of markets, or mostly 5/15-minute crypto markets)
- trade too fast to copy by hand: half or more of their recent buys are at 95¢ or more, sold again
  within 10 minutes, or placed on both sides of a market
- mostly bet on markets that end up voided (buying at 49¢ once a match is known to be void)

A high win rate on its own never excludes anyone.

Borderline cases stay in the list with a flag. Apart from `24/7`, which is only a badge, flagged
traders are not followed automatically:

| Flag | Meaning |
|---|---|
| `CONC` | one bet is 40–50% of profit |
| `NEW` | account younger than 60 days |
| `24/7` | no daily quiet period, which suggests a bot (badge only) |
| `MM?` | some market-maker signals: heavy maker rebates, thin margins, or tiny ROI across hundreds of bets |
| `FAST` | 25–50% of recent buys are too fast to copy (see above) |

The score (0–100) blends each trader's percentile within the group:

| Component | Weight | What it measures |
|---|---|---|
| Edge | 55% | how much more often they win than the odds they paid imply, discounted for small samples |
| ROI | 25% | return on the money they bet |
| Win rate | 10% | share of resolved bets that won |
| Profit | 10% | total profit, as a percentile, so whales don't dominate |

Edge carries the most weight because, when backtested, it was the part of the score that predicted
how a trader did over the following month.

The live feed follows the top 50 traders who aren't flagged and have a positive edge, plus anyone
you pin. It watches Polymarket's public trade stream and also polls each trader's activity every
20 seconds, which catches limit-order fills the stream doesn't carry.

## Configuration

Optional: `~/.config/polywatch/config.toml`. Any field of `Settings` in
`src/polywatch/config.py` can be overridden, for example:

```toml
watchlist_size = 30
feed_min_usd = 250
conviction_multiple = 4.0
window_days = 60
candidate_depths = [["MONTH", 2000], ["WEEK", 250]]
alerts = false
```

Data lives in `~/.local/share/polywatch/polywatch.db` and logs in `~/.local/state/polywatch/polywatch.log`.

## Development

```bash
uv run pytest
```
