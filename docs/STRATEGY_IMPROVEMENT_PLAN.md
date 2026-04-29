# Strategy Improvement Plan + Adaptive Risk Brain

**Status:** Blueprint — implement iteratively  
**Priority order:** Phase 1 → Phase 2 → Side Quest (RL brain)  
**Key constraint:** Every change must be backtestable before going live.

---

## Part 1 — Strategy Hardening

### Phase 1 — Signal Quality Filters (Quick wins, 1–3 days)

These are pure TOML + logic changes. No new infra.

#### 1.1 Enable the existing filters in `overnight_range.toml`

```toml
[filters]
range_size        = true      # was false — enforce range_min/max_pts
gap               = true      # was false — skip anomalous open gaps
volatility        = true      # was false — skip when ATR is extreme
range_min_pts     = 60.0      # tighten from 50 (MNQ: ~$120/contract noise floor)
range_max_pts     = 400.0     # tighten from 500 (huge range = chop, not range)
gap_max_pts       = 150.0     # tighten from 200
atr_min           = 25.0      # tighten from 20
atr_max           = 180.0     # tighten from 200
```

**Bot instruction:**  
```
Run: SYMBOL=MNQ TIMEFRAME=5m DAYS=90 MODE=csv CSV=historical_data/MNQ_5m.csv STRATEGY=overnight_range bash scripts/backtest_symbol.sh
Compare sharpe/win-rate/max-dd before and after enabling each filter individually.
Record results in docs/BACKTEST_RESEARCH.md under a dated section.
```

#### 1.2 Add a day-of-week filter (skip Mondays and Fridays)

Overnight range trades placed Mon open or Fri open have empirically worse fill quality and more reversal risk. Add to strategy config:

```toml
[filters]
skip_weekdays = [0, 4]   # 0=Monday, 4=Friday (Python weekday)
```

In `overnight_range_strategy.py`, at the top of `_scan_for_breakout`:
```python
cfg = self._get_config()
skip_days = cfg.get("filters", {}).get("skip_weekdays", [])
if datetime.now(ET_TZ).weekday() in skip_days:
    logger.info("Skipping scan — day-of-week filter")
    return
```

#### 1.3 Add a pre-news blackout window

Economic data (CPI, NFP, FOMC) destroys predictable breakouts. Add a 30-min blackout around major news:

```toml
[filters]
news_blackout_minutes = 30   # skip if major news within this many minutes of open
```

**Bot instruction:**  
```
Fetch economic calendar from https://www.mql5.com/en/economic-calendar (or hardcode known 2026 dates).
Store in config/news_blackout.toml as [[events]] list with date+time+impact fields.
Load at strategy startup; skip session if any HIGH-impact event is within news_blackout_minutes of 9:30 ET.
```

---

### Phase 2 — Dynamic Sizing + R:R Tuning (3–7 days)

#### 2.1 Volatility-adjusted position sizing

Replace the static `position_size = 1` with a formula:

```
target_risk_usd = account_balance * risk_per_trade_pct / 100
atr_in_dollars  = atr_points * point_value[symbol]
size            = floor(target_risk_usd / (atr_in_dollars * stop_atr_multiplier))
size            = clamp(size, 1, max_position_size)
```

Implementation lives in `strategies/overnight_range_strategy.py` in `_calculate_order_params`:

```python
def _calc_dynamic_size(self, symbol: str, atr: float) -> int:
    cfg = self._get_config()
    balance = self.bot.get_account_balance() or 50_000
    risk_pct = cfg["risk"]["risk_per_trade_pct"] / 100
    target_risk = balance * risk_pct
    pv = POINT_VALUES.get(symbol, 2.0)
    stop_pts = atr * cfg["signal"]["stop_atr_multiplier"]
    raw = target_risk / (stop_pts * pv)
    return max(1, min(int(raw), cfg["risk"]["max_positions"]))
```

**Bot instruction:**  
```
Before implementing: run a backtest grid over stop_atr_multiplier in [0.75, 1.0, 1.25, 1.5, 2.0]
and tp_atr_multiplier in [1.5, 2.0, 2.5, 3.0] (25 combos) on MNQ 5m 180 days.
Filter to combos where OOS sharpe > 0.8 AND OOS win_rate > 45%.
Save full grid output to docs/perf/atr_grid_MNQ_5m.json.
Pick the combo with highest OOS profit_factor.
```

#### 2.2 Trailing stop / breakeven tightening

Current breakeven triggers at 15 pts for MNQ ($30). This is generous. Add a trailing ATR stop:

```toml
[position_management]
trail_enabled         = true
trail_atr_multiplier  = 0.75   # trail stop at 0.75×ATR behind price
trail_activate_at_rr  = 1.0    # activate once 1R profit reached
```

**Implementation:** Wire into the breakout monitor loop — after entry fills, start polling position P&L every 15s and move stop up if trail condition met.

#### 2.3 Session-level circuit breaker

If daily loss exceeds `X * single_trade_risk`, stop trading for the session:

```toml
[risk]
daily_loss_circuit_breaker_r = 2.0   # stop after 2R daily loss
```

Track in `overnight_range_strategy.py`:
```python
self._session_pnl_r: Dict[str, float] = {}   # symbol -> realized R today
```

On each fill close event, compute R and add to `_session_pnl_r`. If total < `-circuit_breaker_r`, skip breakout monitor for that symbol for rest of session.

---

### Phase 3 — Walk-Forward Validation (Ongoing, automated)

**Bot instruction:**  
```
Every Sunday 6 PM, run:
  bash scripts/backtest_thorough_symbol.sh  (MODE=api, DAYS=90, all 3 symbols)
Compare OOS sharpe week-over-week.
If OOS sharpe drops below 0.5 for any symbol, send Discord alert and pause that symbol in overnight_range.toml (enabled=false for [symbols.SYMBOL]).
Commit the auto-updated TOML with message "auto: disable SYMBOL — OOS sharpe below threshold".
```

Add `scripts/weekly_validation.sh`:
```bash
#!/usr/bin/env bash
for SYMBOL in MNQ MES MGC; do
  MODE=api SYMBOL=$SYMBOL DAYS=90 TIMEFRAME=5m bash scripts/backtest_thorough_symbol.sh \
    | python3 scripts/check_oos_sharpe.py --symbol $SYMBOL --threshold 0.5
done
```

---

## Part 2 — Side Quest: Adaptive Risk Brain

### Concept

A learning agent that sits between `strategy.evaluate()` and order placement. It scores each signal, decides whether to trade and at what size, then learns from outcomes. Profitable decisions unlock more compute for better models.

```
strategy.evaluate()
    ↓ raw SignalEvent (direction, symbol, entry, sl, tp, features)
    ↓
RiskBrain.score(signal)
    ↓ (confidence: 0.0–1.0, size_multiplier: 0.0–2.0)
    ↓
if confidence > threshold: place_order()
    ↓
on_trade_closed(trade_id, realized_pnl, realized_r)
    ↓
RiskBrain.learn(features, action, reward=realized_r)
```

### Architecture

```
core/
  risk_brain/
    __init__.py
    brain.py          ← RiskBrain: main class (tier dispatch, inference, learn)
    features.py       ← FeatureExtractor: builds numpy vector from signal + market state
    models/
      tier1.py        ← LogisticRegression (always available, fast)
      tier2.py        ← RandomForest / LightGBM (unlocked at Tier 2)
      tier3.py        ← PPO RL agent via stable-baselines3 (unlocked at Tier 3)
    store.py          ← TradeMemory: Postgres read/write for experiences
    compute_budget.py ← ComputeBudget: tier thresholds, lock/unlock logic
```

### Feature Vector (per signal)

| # | Feature | Source |
|---|---------|--------|
| 0 | Overnight range size (normalized to ATR) | strategy state |
| 1 | ATR percentile vs. 30-day history | bar data |
| 2 | Gap size at open (normalized to ATR) | price at 9:30 vs prior close |
| 3 | Time since range formed (hours) | strategy state |
| 4 | Breakout strength (price beyond range / ATR) | current price |
| 5 | Distance to opposite range wall (cushion) | strategy state |
| 6 | Session P&L in R so far today | risk brain store |
| 7 | Win rate last 10 signals (same symbol) | risk brain store |
| 8 | Day of week (one-hot, 5 dims) | datetime |
| 9 | VIX-equivalent proxy (ATR / 14-day avg ATR) | bar data |
| 10 | Prior session result (win/loss/flat) | risk brain store |
| 11 | Hour of entry (normalized 0–1) | datetime |

### Compute Tier System

The brain tracks a **rolling 30-day realized R** (R = multiples of single-trade risk).

| Tier | Unlock condition | Model | Max inference time | Features used |
|------|-----------------|-------|--------------------|---------------|
| 1 | Always | Logistic regression (sklearn) | < 5ms | All 12 |
| 2 | 30d realized R > 10 (≈ 10 avg wins) | LightGBM ensemble | < 20ms | All 12 + 4 engineered |
| 3 | 30d realized R > 30 AND Sharpe > 1.0 | PPO RL agent (SB3) | < 100ms | All 12 + 4 engineered + sequence |

Tier downgrade: if 7-day realized R drops below 0, step down one tier and reset model weights (don't trust an overfit model during drawdown).

**Key rule:** Model is always trained on PAST data. Signal features at time T use only information available at T (no lookahead). Reward is only assigned once the position is fully closed.

### Implementation Plan

#### Step 1 — Feature extractor + store (day 1–2)

```python
# core/risk_brain/features.py
from dataclasses import dataclass
import numpy as np

@dataclass
class SignalFeatures:
    symbol: str
    side: str
    signal_id: str        # uuid, links to trade outcome later
    vector: np.ndarray    # shape (12,) normalized float32

class FeatureExtractor:
    def extract(self, signal, strategy_state, bar_data, brain_store) -> SignalFeatures: ...
```

Store schema (new Postgres table — add via Alembic):
```sql
CREATE TABLE risk_brain_experiences (
    id          SERIAL PRIMARY KEY,
    signal_id   UUID NOT NULL,
    symbol      VARCHAR(10),
    side        VARCHAR(4),
    features    JSONB,           -- feature vector as list
    action      FLOAT,           -- size_multiplier applied (0 = skipped)
    confidence  FLOAT,
    reward_r    FLOAT,           -- realized R, NULL until trade closes
    closed_at   TIMESTAMPTZ,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX ON risk_brain_experiences (symbol, created_at);
CREATE INDEX ON risk_brain_experiences (closed_at) WHERE closed_at IS NOT NULL;
```

```python
# core/risk_brain/store.py
class TradeMemory:
    async def save_experience(self, feat: SignalFeatures, action: float, confidence: float) -> None: ...
    async def close_experience(self, signal_id: str, reward_r: float) -> None: ...
    async def recent_experiences(self, symbol: str, n: int) -> list[dict]: ...
    async def rolling_30d_r(self) -> float: ...
    async def rolling_7d_r(self) -> float: ...
    async def sharpe_30d(self) -> float: ...
```

#### Step 2 — Tier 1 model + brain scaffold (day 2–3)

```python
# core/risk_brain/brain.py
class RiskBrain:
    def __init__(self, store: TradeMemory, compute_budget: ComputeBudget):
        self._store = store
        self._budget = compute_budget
        self._models = {1: Tier1Model(), 2: None, 3: None}
        self._extractor = FeatureExtractor()

    async def score(self, signal, strategy_state, bar_data) -> tuple[float, float]:
        """Returns (confidence: 0-1, size_multiplier: 0-2)."""
        features = self._extractor.extract(signal, strategy_state, bar_data, self._store)
        tier = self._budget.current_tier()
        model = self._models[tier]
        confidence = model.predict_proba(features.vector)
        size_mult = self._size_from_confidence(confidence, tier)
        await self._store.save_experience(features, size_mult, confidence)
        return confidence, size_mult

    async def learn(self, signal_id: str, realized_r: float) -> None:
        """Called when a trade closes. Updates model online."""
        await self._store.close_experience(signal_id, realized_r)
        tier = self._budget.current_tier()
        experiences = await self._store.recent_experiences(symbol=None, n=500)
        self._models[tier].fit_incremental(experiences)
        await self._budget.recheck()  # may unlock/lock tiers

    def _size_from_confidence(self, conf: float, tier: int) -> float:
        # Tier 1: binary (trade or skip)
        if tier == 1:
            return 1.0 if conf > 0.55 else 0.0
        # Tier 2+: graduated sizing
        if conf < 0.50: return 0.0
        if conf < 0.65: return 0.75
        if conf < 0.80: return 1.0
        return 1.5  # high-conviction: 1.5x size
```

#### Step 3 — Hook into overnight_range_strategy (day 3–4)

In `_ensure_breakout_order`, after the fast-path cache check but before `risk_manager.check_order_allowed`:

```python
# Only if brain is enabled (config flag)
if self._risk_brain and cfg.get("brain", {}).get("enabled", False):
    confidence, size_mult = await self._risk_brain.score(
        signal=dict(symbol=symbol, side=side, entry=entry_price, sl=stop_price, tp=tp_price),
        strategy_state=self._get_brain_state(symbol),
        bar_data=self._recent_bars.get(symbol, []),
    )
    if size_mult == 0.0:
        logger.info(f"RiskBrain skipped {symbol} {side} (confidence={confidence:.2f})")
        return
    quantity = max(1, round(base_qty * size_mult))
```

Add to `overnight_range.toml`:
```toml
[brain]
enabled     = false       # flip true when Tier 1 model has ≥ 50 experiences
min_confidence = 0.55
```

#### Step 4 — Backtesting the brain

The research runner (`core/research/runner.py`) needs to replay brain decisions:

```bash
# Replay with brain (uses stored experiences from paper trading)
python core/backtest_executor.py --strategy=overnight_range --symbol=MNQ \
  --days=90 --replay --brain --brain-threshold=0.55
```

The replay engine passes signal features through the brain's Tier 1 model in inference mode, logs which trades it would have skipped, and computes before/after metrics.

#### Step 5 — Tier 2 unlock (LightGBM, when Tier 1 is profitable)

```python
# core/risk_brain/models/tier2.py
import lightgbm as lgb

class Tier2Model:
    def fit(self, X, y_r): ...           # y_r = realized R labels
    def predict_proba(self, x): ...      # returns win probability
    def fit_incremental(self, experiences): ...
```

LightGBM trains overnight (cron at 2 AM) on all closed experiences. Tier 1 stays hot for real-time inference during the day; Tier 2 model is hot-swapped in after training completes.

#### Step 6 — Tier 3 (PPO RL, when Tier 2 is consistently profitable)

```python
# core/risk_brain/models/tier3.py
from stable_baselines3 import PPO
import gymnasium as gym

class TradingEnv(gym.Env):
    """Single-step episode: observe features → act (size 0–2) → receive R reward."""
    observation_space = gym.spaces.Box(low=-5, high=5, shape=(16,))
    action_space      = gym.spaces.Box(low=0.0, high=2.0, shape=(1,))

    def step(self, action):
        reward = self._pending_r  # set externally when trade closes
        ...
```

PPO trains offline on batches of experiences. The policy network has 2 hidden layers (64 units each). Entropy coefficient decays as more data accumulates (start explorative, become exploitative as win rate stabilizes).

---

## Implementation Sequence (recommended order)

```
Week 1:
  [x] Enable range/gap/volatility filters in TOML (30 min)
  [ ] Add day-of-week filter to strategy (1 hr)
  [ ] Run full ATR grid backtest, pick best params (2 hr)
  [ ] Implement dynamic position sizing (2 hr)

Week 2:
  [ ] Add session circuit breaker (2 hr)
  [ ] Implement trailing ATR stop (3 hr)
  [ ] Set up weekly_validation.sh cron (1 hr)

Week 3 (Side Quest begins):
  [ ] Create Alembic migration for risk_brain_experiences table
  [ ] Implement FeatureExtractor + TradeMemory (store.py)
  [ ] Tier 1 LogisticRegression model (cold start: all trades = positive class)
  [ ] Wire into overnight_range_strategy with brain.enabled=false

Week 4:
  [ ] Paper-trade with brain logging (brain.enabled=false, but scoring every signal)
  [ ] After 50+ experiences: flip brain.enabled=true, monitor live
  [ ] Backtest brain decisions vs. naive (did it actually help?)

Month 2+:
  [ ] Unlock Tier 2 (LightGBM) once 30d R > 10
  [ ] Unlock Tier 3 (PPO) once 30d R > 30 AND Sharpe > 1.0
```

---

## Validation Gates (never skip)

Before any change goes live:

1. `python3 -c "import ast; ast.parse(open('strategies/overnight_range_strategy.py').read())"` — syntax
2. `.venv/bin/python -m pytest` — full test suite
3. Backtest on MNQ 5m 90-day real data: OOS Sharpe must be > current baseline
4. Paper-trade 5 sessions minimum before enabling any new filter or brain feature
5. Document result in `docs/BACKTEST_RESEARCH.md` with date, params, and metrics table

---

## Bot Instructions (copy-paste for future sessions)

```
You are working on tradeBotServer, a live futures trading bot. When I ask you to 
improve strategy performance:

1. ALWAYS backtest before touching live config. Use:
   MODE=csv CSV=historical_data/<SYMBOL>_5m.csv STRATEGY=overnight_range bash scripts/backtest_symbol.sh

2. For parameter search, run:
   SYMBOL=MNQ TIMEFRAME=5m DAYS=90 bash scripts/backtest_thorough_symbol.sh

3. Config changes go in config/strategies/overnight_range.toml ONLY — never os.getenv.

4. After any code change: python3 -m pytest (using .venv/bin/python if pytest not in PATH).

5. The risk brain lives in core/risk_brain/. New Postgres tables require an Alembic migration.
   Add to migrations/ and run: DATABASE_URL=$DATABASE_URL alembic upgrade head

6. Tier unlock thresholds live in core/risk_brain/compute_budget.py. 
   Never manually unlock a tier — let the metrics do it.

7. Log at DEBUG for brain scoring in hot paths. Never INFO inside score() loop.

8. The brain is always OPTIONAL. overnight_range.toml [brain] enabled=false is safe fallback.
```

---

## Quick Reference — Key Metrics to Track

| Metric | Target | Current (fill in from backtest) |
|--------|--------|--------------------------------|
| Win rate (OOS) | > 50% | — |
| Profit factor (OOS) | > 1.5 | — |
| Sharpe ratio (OOS) | > 1.0 | — |
| Max drawdown | < 15% | — |
| Avg R per trade | > 0.5 | — |
| Brain skip rate | 20–40% | — |
| Brain accuracy (skipped = losers) | > 60% | — |
| 30-day rolling R | > 0 | — |
