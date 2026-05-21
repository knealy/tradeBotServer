#!/usr/bin/env python3
"""Walk-forward survival tournament for ``rsi_switch_15m`` parameter variants.

Each **candidate** is the same strategy id with a distinct env override map (see
``config/perf_sweep/rsi_switch_tournament_seed.json``). Runs the same calendar folds
as ``walkforward_strategy_competition.py``, scores aggregated fitness across symbols,
eliminates the bottom half each round, and breeds mutations from survivors.

Example::

  ENABLE_SIGNALR=false .venv/bin/python scripts/walkforward_rsi_switch_tournament.py \\
    --days 120 --folds 5 --rounds 3 --workers 4 \\
    --seed-manifest config/perf_sweep/rsi_switch_tournament_seed.json \\
    --out-dir docs/perf/rsi_switch_tournament_120d5f

Outputs:
  - ``round_<n>/summary.tsv``, ``round_<n>/leaderboard.md``
  - ``tournament_final.md`` — champion + lineage
  - ``champion.env.json`` — winning env overrides (apply to TOML / next live test)
"""
from __future__ import annotations

import argparse
import copy
import json
import math
import random
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.walkforward_strategy_competition import (  # noqa: E402
    FoldRow,
    _csv_last_date,
    _fold_ranges,
    _run_one,
)

STRATEGY = "rsi_switch_15m"
ENV_PREFIX = "RSI_SWITCH_15M_SIGNAL_"

# Mutable knobs for mutation / crossover (env suffix without prefix)
GENE_KEYS = [
    "RSI_LONG_MAX",
    "RSI_SHORT_MIN",
    "RSI_HOOK_BUFFER",
    "RSI_DEEP_EXTRA",
    "RSI_PROFIT_EXIT_LONG",
    "RSI_PROFIT_EXIT_SHORT",
    "REACTION_TOL_ATR",
    "TP_R_MULTIPLE",
    "STOP_ATR_MULTIPLIER",
    "MIN_BARS_BETWEEN_ENTRIES",
    "MIN_HOLD_BARS",
    "FLAT_MOVE_ATR",
    "LOSS_CUT_ATR",
    "REQUIRE_CONFIRM_CANDLE",
    "BLOCK_COUNTER_TREND",
    "REQUIRE_EMA_REACTION",
]


@dataclass
class Candidate:
    cid: str
    label: str
    env: Dict[str, str] = field(default_factory=dict)
    parent_ids: List[str] = field(default_factory=list)

    def env_key(self, suffix: str) -> str:
        return f"{ENV_PREFIX}{suffix}"

    def get_gene(self, suffix: str, default: Optional[str] = None) -> Optional[str]:
        return self.env.get(self.env_key(suffix), default)


@dataclass
class ScoredCandidate:
    candidate: Candidate
    fitness: float
    sum_pnl: float
    total_trades: int
    win_rate_pct: float
    folds_green: int
    folds_total: int
    mean_sharpe: float
    rows: List[FoldRow]


def _load_seed_manifest(path: Path) -> List[Candidate]:
    data = json.loads(path.read_text(encoding="utf-8"))
    out: List[Candidate] = []
    for c in data.get("candidates") or []:
        out.append(
            Candidate(
                cid=str(c["id"]),
                label=str(c.get("label") or c["id"]),
                env={str(k): str(v) for k, v in (c.get("env") or {}).items()},
            )
        )
    return out


def _float_env(env: Dict[str, str], key: str, default: float) -> float:
    v = env.get(key)
    if v is None:
        return default
    try:
        return float(v)
    except ValueError:
        return default


def compute_fitness(rows: List[FoldRow]) -> Tuple[float, Dict[str, Any]]:
    """Higher is better. Penalize zero/low trade count; reward WR, PnL, green folds."""
    if not rows:
        return -1e6, {}

    sum_pnl = sum(r.total_pnl for r in rows)
    trades = sum(r.total_trades for r in rows)
    if trades <= 0:
        return -1e6, {"sum_pnl": sum_pnl, "total_trades": 0}

    wr_times = sum(float(r.win_rate) * float(r.total_trades) for r in rows)
    wr_pct = wr_times / float(trades)
    folds_green = sum(1 for r in rows if r.total_pnl > 0)
    folds_total = len(rows)
    mean_sharpe = sum(r.sharpe for r in rows) / folds_total
    expectancy = sum_pnl / float(trades)

    # Components (roughly 0–100 scale each)
    wr_score = wr_pct
    pnl_score = max(-50.0, min(50.0, expectancy * 8.0))
    consistency = 100.0 * folds_green / max(1, folds_total)
    sharpe_score = max(0.0, min(30.0, mean_sharpe * 5.0))
    activity = min(30.0, trades * 0.8)

    trade_penalty = 0.0
    if trades < 15:
        trade_penalty = (15 - trades) * 4.0

    fitness = (
        0.38 * wr_score
        + 0.22 * pnl_score
        + 0.20 * consistency
        + 0.10 * sharpe_score
        + 0.10 * activity
        - trade_penalty
    )

    return fitness, {
        "sum_pnl": sum_pnl,
        "total_trades": trades,
        "win_rate_pct": wr_pct,
        "folds_green": folds_green,
        "folds_total": folds_total,
        "mean_sharpe": mean_sharpe,
        "expectancy": expectancy,
    }


def _mutate_value(suffix: str, current: Optional[str], rng: random.Random) -> str:
    """Return new env string value for one gene."""
    if suffix in ("REQUIRE_CONFIRM_CANDLE", "BLOCK_COUNTER_TREND", "REQUIRE_EMA_REACTION"):
        cur = (current or "true").lower() in ("1", "true", "yes")
        if rng.random() < 0.35:
            return "false" if cur else "true"
        return "true" if cur else "false"

    defaults = {
        "RSI_LONG_MAX": 35.0,
        "RSI_SHORT_MIN": 65.0,
        "RSI_HOOK_BUFFER": 3.0,
        "RSI_DEEP_EXTRA": 2.0,
        "RSI_PROFIT_EXIT_LONG": 52.0,
        "RSI_PROFIT_EXIT_SHORT": 48.0,
        "REACTION_TOL_ATR": 0.50,
        "TP_R_MULTIPLE": 1.25,
        "STOP_ATR_MULTIPLIER": 2.0,
        "MIN_BARS_BETWEEN_ENTRIES": 4.0,
        "MIN_HOLD_BARS": 2.0,
        "FLAT_MOVE_ATR": 0.08,
        "LOSS_CUT_ATR": 0.70,
    }
    base = float(current) if current is not None else defaults.get(suffix, 1.0)

    if suffix in ("RSI_LONG_MAX", "RSI_HOOK_BUFFER", "RSI_DEEP_EXTRA", "RSI_PROFIT_EXIT_LONG"):
        delta = rng.choice([-3.0, -2.0, -1.0, 1.0, 2.0, 3.0])
        lo = 20.0 if suffix == "RSI_LONG_MAX" else (0.0 if suffix == "RSI_DEEP_EXTRA" else 0.0)
        hi = 42.0 if suffix == "RSI_LONG_MAX" else (8.0 if suffix == "RSI_HOOK_BUFFER" else 60.0)
        val = max(lo, min(hi, base + delta))
        return f"{val:.1f}".rstrip("0").rstrip(".")
    if suffix in ("RSI_SHORT_MIN", "RSI_PROFIT_EXIT_SHORT"):
        delta = rng.choice([-3.0, -2.0, -1.0, 1.0, 2.0, 3.0])
        val = max(58.0, min(80.0, base + delta))
        return f"{val:.1f}".rstrip("0").rstrip(".")
    if suffix in ("TP_R_MULTIPLE", "REACTION_TOL_ATR", "FLAT_MOVE_ATR", "LOSS_CUT_ATR"):
        factor = rng.choice([0.85, 0.9, 1.0, 1.1, 1.15])
        val = base * factor
        if suffix == "TP_R_MULTIPLE":
            val = max(0.75, min(2.5, val))
        elif suffix == "REACTION_TOL_ATR":
            val = max(0.30, min(0.65, val))
        else:
            val = max(0.04, min(1.0, val))
        return f"{val:.3f}".rstrip("0").rstrip(".")
    if suffix in ("MIN_BARS_BETWEEN_ENTRIES", "MIN_HOLD_BARS", "RSI_DEEP_EXTRA"):
        delta = rng.choice([-1, 0, 1])
        val = int(round(base)) + delta
        lo, hi = (1, 8) if suffix != "RSI_DEEP_EXTRA" else (0, 6)
        val = max(lo, min(hi, val))
        return str(val)

    return str(current) if current is not None else str(base)


def _mutate(parent: Candidate, rng: random.Random, gen: int, ix: int) -> Candidate:
    env = copy.deepcopy(parent.env)
    n_mut = max(1, rng.randint(1, 3))
    keys = rng.sample(GENE_KEYS, k=min(n_mut, len(GENE_KEYS)))
    for suffix in keys:
        ek = f"{ENV_PREFIX}{suffix}"
        env[ek] = _mutate_value(suffix, env.get(ek), rng)
    # Keep long < short
    lk = f"{ENV_PREFIX}RSI_LONG_MAX"
    sk = f"{ENV_PREFIX}RSI_SHORT_MIN"
    if lk in env and sk in env:
        if float(env[lk]) >= float(env[sk]) - 20:
            env[sk] = str(float(env[lk]) + 30)
    cid = f"g{gen}_m{ix}_{parent.cid}"[:64]
    return Candidate(cid=cid, label=f"mutate({parent.cid})", env=env, parent_ids=[parent.cid])


def _crossover(a: Candidate, b: Candidate, rng: random.Random, gen: int, ix: int) -> Candidate:
    env: Dict[str, str] = {}
    for suffix in GENE_KEYS:
        ek = f"{ENV_PREFIX}{suffix}"
        pick = a.env if rng.random() < 0.5 else b.env
        if ek in pick:
            env[ek] = pick[ek]
        elif ek in (a.env if pick is b.env else b.env):
            env[ek] = (a.env if ek in a.env else b.env)[ek]
    cid = f"g{gen}_x{ix}_{a.cid}_{b.cid}"[:80]
    return Candidate(
        cid=cid,
        label=f"cross({a.cid},{b.cid})",
        env=env,
        parent_ids=[a.cid, b.cid],
    )


def _breed_next_generation(
    survivors: List[ScoredCandidate],
    population_target: int,
    gen: int,
    rng: random.Random,
) -> List[Candidate]:
    """Fill population with survivors + mutations + crossovers."""
    out: List[Candidate] = [sc.candidate for sc in survivors]
    seen = {c.cid for c in out}
    ix = 0
    while len(out) < population_target:
        if len(survivors) >= 2 and rng.random() < 0.4:
            a, b = rng.sample(survivors, 2)
            child = _crossover(a.candidate, b.candidate, rng, gen, ix)
        else:
            parent = rng.choice(survivors).candidate
            child = _mutate(parent, rng, gen, ix)
        if child.cid not in seen:
            out.append(child)
            seen.add(child.cid)
        ix += 1
        if ix > population_target * 4:
            break
    return out[:population_target]


@dataclass
class Job:
    candidate_id: str
    symbol: str
    fold: int
    start: date
    end: date
    csv_path: Path
    timeframe: str
    env: Dict[str, str]
    out_json: Path


def _run_job(job: Job) -> Tuple[Job, bool, Dict[str, Any]]:
    ok, d = _run_one(
        strategy=STRATEGY,
        symbol=job.symbol,
        csv_path=job.csv_path,
        start=job.start,
        end=job.end,
        timeframe=job.timeframe,
        out_json=job.out_json,
        extra_env=job.env if job.env else None,
    )
    return job, ok, d


def _evaluate_population(
    candidates: List[Candidate],
    *,
    symbols: List[str],
    csv_paths: Dict[str, Path],
    folds: List[Tuple[date, date, int]],
    timeframe: str,
    out_dir: Path,
    workers: int,
) -> List[ScoredCandidate]:
    jobs: List[Job] = []
    for cand in candidates:
        for sym in symbols:
            for fs, fe, fold_ix in folds:
                tag = f"{cand.cid}_{sym}_fold{fold_ix}_{fs}_{fe}"
                jobs.append(
                    Job(
                        candidate_id=cand.cid,
                        symbol=sym,
                        fold=fold_ix,
                        start=fs,
                        end=fe,
                        csv_path=csv_paths[sym],
                        timeframe=timeframe,
                        env=cand.env,
                        out_json=out_dir / "runs" / tag / "result.json",
                    )
                )

    rows_by_cid: Dict[str, List[FoldRow]] = defaultdict(list)
    cand_by_id = {c.cid: c for c in candidates}

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futs = [pool.submit(_run_job, j) for j in jobs]
        for fut in as_completed(futs):
            job, ok, d = fut.result()
            if not ok:
                err = d.get("error", d)
                print(f"FAIL {job.candidate_id} {job.symbol} fold{job.fold}: {err}", file=sys.stderr)
                r: Dict[str, Any] = {}
            else:
                r = d.get("result") or {}
            rows_by_cid[job.candidate_id].append(
                FoldRow(
                    strategy=job.candidate_id,
                    symbol=job.symbol,
                    fold=job.fold,
                    start=job.start,
                    end=job.end,
                    total_pnl=float(r.get("total_pnl") or 0.0),
                    total_trades=int(r.get("total_trades") or 0),
                    win_rate=float(r.get("win_rate") or 0.0),
                    sharpe=float(r.get("sharpe_ratio") or 0.0),
                    max_dd=float(r.get("max_drawdown") or 0.0),
                )
            )

    scored: List[ScoredCandidate] = []
    for cid, rows in rows_by_cid.items():
        fit, meta = compute_fitness(rows)
        scored.append(
            ScoredCandidate(
                candidate=cand_by_id[cid],
                fitness=fit,
                sum_pnl=float(meta.get("sum_pnl") or 0),
                total_trades=int(meta.get("total_trades") or 0),
                win_rate_pct=float(meta.get("win_rate_pct") or 0),
                folds_green=int(meta.get("folds_green") or 0),
                folds_total=int(meta.get("folds_total") or 0),
                mean_sharpe=float(meta.get("mean_sharpe") or 0),
                rows=rows,
            )
        )
    scored.sort(key=lambda s: s.fitness, reverse=True)
    return scored


def _write_round_outputs(
    scored: List[ScoredCandidate],
    out_dir: Path,
    *,
    round_ix: int,
    days: int,
    folds_n: int,
    anchor_end: date,
    symbols: List[str],
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    tsv = out_dir / "summary.tsv"
    with tsv.open("w", encoding="utf-8") as f:
        f.write(
            "candidate_id\tsymbol\tfold\tstart\tend\ttotal_pnl\ttotal_trades\t"
            "win_rate\tsharpe_ratio\tmax_drawdown\n"
        )
        for sc in scored:
            for r in sc.rows:
                f.write(
                    f"{sc.candidate.cid}\t{r.symbol}\t{r.fold}\t{r.start}\t{r.end}\t"
                    f"{r.total_pnl:.2f}\t{r.total_trades}\t{r.win_rate:.4f}\t"
                    f"{r.sharpe:.4f}\t{r.max_dd:.2f}\n"
                )

    md = out_dir / "leaderboard.md"
    with md.open("w", encoding="utf-8") as f:
        f.write(f"# RSI switch tournament — round {round_ix}\n\n")
        f.write(f"- **Span:** {days}d ending {anchor_end}, **{folds_n}** folds\n")
        f.write(f"- **Symbols:** {', '.join(symbols)}\n\n")
        f.write("## Fitness ranking\n\n")
        f.write(
            "| rank | candidate | fitness | sum_pnl | trades | win_rate_pct | "
            "folds_green | mean_sharpe | label |\n"
        )
        f.write("|---:|---|---:|---:|---:|---:|---:|---:|---|\n")
        for i, sc in enumerate(scored, 1):
            f.write(
                f"| {i} | `{sc.candidate.cid}` | {sc.fitness:.2f} | {sc.sum_pnl:.2f} | "
                f"{sc.total_trades} | {sc.win_rate_pct:.2f} | {sc.folds_green}/{sc.folds_total} | "
                f"{sc.mean_sharpe:.3f} | {sc.candidate.label} |\n"
            )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, default=120)
    ap.add_argument("--folds", type=int, default=5)
    ap.add_argument("--symbols", type=str, default="MNQ,MES,MGC")
    ap.add_argument("--timeframe", type=str, default="5m")
    ap.add_argument("--rounds", type=int, default=3, help="Survival rounds after seed round")
    ap.add_argument(
        "--seed-manifest",
        type=Path,
        default=ROOT / "config" / "perf_sweep" / "rsi_switch_tournament_seed.json",
    )
    ap.add_argument("--out-dir", type=Path, default=ROOT / "docs" / "perf" / "rsi_switch_tournament")
    ap.add_argument("--workers", type=int, default=4, help="Parallel replay workers")
    ap.add_argument(
        "--survivor-fraction",
        type=float,
        default=0.5,
        help="Fraction of population surviving each round (0–1)",
    )
    ap.add_argument("--population", type=int, default=12, help="Target population size per bred round")
    ap.add_argument("--seed", type=int, default=42, help="RNG seed for breeding")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    rng = random.Random(args.seed)
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    csv_dir = ROOT / "historical_data" / "price"
    csv_paths: Dict[str, Path] = {}
    anchor_dates: Dict[str, date] = {}
    for sym in symbols:
        p = csv_dir / f"{sym.lower()}_5m_databento.csv"
        if not p.is_file():
            print(f"error: missing {p}", file=sys.stderr)
            return 2
        csv_paths[sym] = p
        anchor_dates[sym] = _csv_last_date(p)

    anchor_end = min(anchor_dates.values())
    folds = _fold_ranges(anchor_end, total_days=args.days, folds=args.folds)
    out_root: Path = args.out_dir
    out_root.mkdir(parents=True, exist_ok=True)

    population = _load_seed_manifest(args.seed_manifest)
    if not population:
        print("error: empty seed manifest", file=sys.stderr)
        return 2

    print(
        f"tournament: {len(population)} seeds, {args.rounds} bred rounds, "
        f"{len(symbols)} symbols, {len(folds)} folds, workers={args.workers}",
        file=sys.stderr,
    )

    if args.dry_run:
        for r in range(args.rounds + 1):
            print(f"round {r}: would evaluate {len(population)} candidates", file=sys.stderr)
        return 0

    lineage: List[Dict[str, Any]] = []
    champion: Optional[ScoredCandidate] = None

    for round_ix in range(args.rounds + 1):
        round_dir = out_root / f"round_{round_ix}"
        print(f"=== round {round_ix}: {len(population)} candidates ===", file=sys.stderr)
        scored = _evaluate_population(
            population,
            symbols=symbols,
            csv_paths=csv_paths,
            folds=folds,
            timeframe=args.timeframe,
            out_dir=round_dir,
            workers=args.workers,
        )
        _write_round_outputs(
            scored,
            round_dir,
            round_ix=round_ix,
            days=args.days,
            folds_n=args.folds,
            anchor_end=anchor_end,
            symbols=symbols,
        )
        champion = scored[0]
        lineage.append(
            {
                "round": round_ix,
                "champion_id": champion.candidate.cid,
                "fitness": champion.fitness,
                "win_rate_pct": champion.win_rate_pct,
                "sum_pnl": champion.sum_pnl,
                "trades": champion.total_trades,
            }
        )

        if round_ix >= args.rounds:
            break

        n_survive = max(2, int(math.ceil(len(scored) * args.survivor_fraction)))
        survivors = scored[:n_survive]
        print(
            f"round {round_ix}: top survivor {survivors[0].candidate.cid} "
            f"fitness={survivors[0].fitness:.2f} wr={survivors[0].win_rate_pct:.1f}%",
            file=sys.stderr,
        )
        population = _breed_next_generation(
            survivors,
            population_target=args.population,
            gen=round_ix + 1,
            rng=rng,
        )

    assert champion is not None
    champ_path = out_root / "champion.env.json"
    champ_path.write_text(
        json.dumps(
            {
                "candidate_id": champion.candidate.cid,
                "label": champion.candidate.label,
                "fitness": champion.fitness,
                "env": champion.candidate.env,
                "metrics": {
                    "sum_pnl": champion.sum_pnl,
                    "total_trades": champion.total_trades,
                    "win_rate_pct": champion.win_rate_pct,
                    "folds_green": champion.folds_green,
                    "folds_total": champion.folds_total,
                },
                "parent_ids": champion.candidate.parent_ids,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    final_md = out_root / "tournament_final.md"
    with final_md.open("w", encoding="utf-8") as f:
        f.write("# RSI switch survival tournament — final\n\n")
        f.write(f"- **Champion:** `{champion.candidate.cid}` — {champion.candidate.label}\n")
        f.write(f"- **Fitness:** {champion.fitness:.2f}\n")
        f.write(
            f"- **Metrics:** PnL {champion.sum_pnl:.2f}, {champion.total_trades} trades, "
            f"WR {champion.win_rate_pct:.2f}%, green folds {champion.folds_green}/{champion.folds_total}\n\n"
        )
        if champion.candidate.env:
            f.write("## Champion env overrides\n\n```\n")
            for k, v in sorted(champion.candidate.env.items()):
                f.write(f"{k}={v}\n")
            f.write("```\n\n")
        else:
            f.write("## Champion env overrides\n\n(none — matches `config/strategies/rsi_switch_15m.toml`)\n\n")
        f.write("## Lineage by round\n\n")
        f.write("| round | champion_id | fitness | win_rate_pct | sum_pnl | trades |\n")
        f.write("|---:|---|---:|---:|---:|---:|\n")
        for row in lineage:
            f.write(
                f"| {row['round']} | `{row['champion_id']}` | {row['fitness']:.2f} | "
                f"{row['win_rate_pct']:.2f} | {row['sum_pnl']:.2f} | {row['trades']} |\n"
            )
        f.write("\nApply winning keys to `config/strategies/rsi_switch_15m.toml` after review.\n")

    print(f"wrote {final_md} and {champ_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
