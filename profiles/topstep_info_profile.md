
## 🧠 **(B) AI Knowledge Context Document**

*(Dense, descriptive, for reasoning and contextual understanding by your model)*

**Topstep / TopstepX Prop Firm Overview**
TopstepX is a futures proprietary trading platform offering simulation, evaluation, and live funding stages. Traders start in the “Trading Combine” (evaluation), can advance to “Express Funded” (simulated funding), and finally to “Live Funded” (real capital). Each stage has strict quantitative rules designed to enforce risk discipline.

**Key Account Concepts**

* **Starting Balance**: Account seed value (e.g., $50K / $100K / $150K).
* **Daily Loss Limit (DLL)**: Maximum allowable intraday drawdown in P&L (realised + unrealised). If breached, trading halts or the account fails.
* **Maximum Loss Limit (MLL)**: A trailing balance threshold that moves upward only when new end-of-day highs occur. Breach = account closure.
* **Profit Target**: The cumulative profit goal to complete a Combine (e.g., $6,000 for $100K).
* **Consistency Rule**: The best trading day’s profit must be < 50% of the total profit.
* **Highest End-of-Day Balance (H)**: Highest balance achieved at any day’s close; used for MLL computation.
* **Trailing Drawdown Formula**: `Threshold = H - MLL_value`.  Account must not close below threshold.
* **Daily Loss Violation**: Intraday `netPnL <= -dailyLossLimit`.
* **Path-to-Reduction**: Live accounts facing large drawdowns may face reduced contract size.

**Account Phases Summary**

1. **Trading Combine (Evaluation):**

   * Must reach profit target, meet consistency, and avoid rule breaches.
   * MLL computed from highest EOD balance minus fixed value (e.g., 3K).
   * DLL computed intraday from realised + unrealised P&L.

2. **Express Funded:**

   * Simulated funded phase.
   * MLL still based on EOD high, not intraday.
   * Payouts allowed after benchmark trading days (e.g., 5+).
   * Same loss limits as Combine.

3. **Live Funded:**

   * Real capital with stricter intraday monitoring.
   * Protective stops mandatory.
   * Payouts: 100% of profits up to $10K lifetime, then 90/10 split.
   * After first payout, MLL resets to zero.

**Data Tracking and Bot Integration**

* **Critical Variables:**
  `current_balance`, `highest_EOD_balance`, `starting_balance`, `MLL_threshold`, `daily_loss_limit`, `realised_PnL`, `unrealised_PnL`, `timestamp_last_update`.
* **Realtime Computation:**
  `netPnL = (realised + unrealised) - (commissions + fees)`
  Update `highest_EOD_balance` if new daily close exceeds previous high.
  `drawdown_threshold = highest_EOD_balance - MLL_value`.
  Violation occurs if `balance <= drawdown_threshold`.

**Example:**

* Account type: Evaluation ($150K)
* Highest EOD balance: $152,250
* MLL: $4,500
  → Threshold = 152,250 – 4,500 = **$147,750**
  If balance falls below $147,750, account fails.

**Bot Implementation Tips**

* Always maintain a **state cache** for: balance, realised/unrealised PnL, open positions, timestamp, and high-water marks.
* If SDK or REST API lacks live endpoints, derive live PnL from position * price deltas using quote subscriptions.
* Recompute EOD metrics automatically based on trading session close times.
* Integrate compliance guards:

  * Auto-flatten when approaching DLL.
  * Pause trading on threshold breach.
  * Report MLL compliance each EOD.
* Use **dynamic position sizing** tied to account balance (e.g., 0.25% per trade).
* Track statistics: win rate, expectancy, profit factor, max drawdown.
* Persist session data to disk to allow continuity after restarts.

**SDK Integration**
From the `project-x-py` SDK:

* `client.get_accounts()` → list of active accounts
* `client.get_balance(account_id)` → returns current balance (cached or live)
* `client.get_positions(account_id)` → retrieve open positions for real-time unrealised PnL
* `client.get_order_history(account_id, start, end)` → retrieve trades
* `client.get_historical_data(symbol, timeframe, bars)` → candles for backfill
* **Custom Extension**: Track missing live metrics by combining `get_positions` + live `subscribe_quotes(symbol)` to compute PnL locally:

  ```
  unrealised_PnL = (current_price - entry_price) * tick_value * contracts
  current_balance = starting_balance + realised_PnL + unrealised_PnL
  ```
* Implement **bypass cache** for 1–5 bar requests (1m, 5m) for near-realtime accuracy.
* Maintain **EOD updates**: at 21:00 UTC (CME close) recompute and persist `highest_EOD_balance`.

**Practical Sample Data**

| Metric           | Value       | Explanation                    |
| ---------------- | ----------- | ------------------------------ |
| Starting Balance | 150,000     | Initial account funding        |
| Current Balance  | 151,875.50  | Live balance after trades      |
| Highest EOD      | 152,250.75  | Highest historical EOD balance |
| MLL Value        | 4,500       | From rulebook                  |
| Threshold        | 147,750.75  | Derived trailing floor         |
| DLL              | 3,000       | Daily loss cap                 |
| Realised PnL     | 340.25      | From closed trades             |
| Unrealised PnL   | 127.50      | From open trades               |
| Net PnL          | 451.25      | (R + U) - (comm + fees)        |
| Status           | ✅ Compliant | Above MLL threshold           |

---

Would you like me to **extend both JSONs** to include *real-time state tracking methods* (e.g., PnL buffer updates, simulated endpoints, and data persistence schema for your bot’s cache)?
That would make the profile ready for *live implementation* within your current Python trading bot framework.
