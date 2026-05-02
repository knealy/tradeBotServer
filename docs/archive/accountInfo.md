Yes — TopstepX **does expose unrealized and realized profit-and-loss data via its API**, but it’s **not a single dedicated “PnL” endpoint** on the official API docs. Instead, P&L is returned as part of your **positions and portfolio data** through the ProjectX Gateway API that TopstepX uses. ([ProjectX Gateway][1])

### 📌 How to Retrieve Unrealized and Realized P&L

#### 🧾 **Positions Endpoint**

Within the ProjectX API (used by TopstepX), the **Positions** endpoints return details for each open and closed position, and these include:

* **Unrealized P&L** — current floating profit/loss based on open positions.
* **Realized P&L** — profit/loss that has been locked in from closed trades.

You typically call something like:

```
GET /api/Positions/getAll
```

(or similar endpoint under the **Positions** category in the ProjectX API). The response includes P&L fields per position. ([ProjectX Gateway][1])

The **ProjectX Python SDK** (example from *project-x-py*) exposes these fields with properties such as:

```python
pos.unrealizedPnl
pos.realizedPnl
```

…for each position object returned. ([ProjectX Python SDK][2])

#### 🧠 What You’ll Get

* **Unrealized PnL** — floating (open trade) profit/loss
* **Realized PnL** — actual profit/loss from trades already closed
* **Aggregated/Session PnL** — if you need totals for the day/session, you can sum these across positions (or use portfolio aggregates if provided via API or SDK) ([ProjectX Python SDK][2])

### 🛠 Using the WebSocket API (Realtime)

In addition to REST, the **WebSocket** (Realtime) API can stream updates that include P&L changes as market prices move. This is useful if you want **live unrealized PnL** without polling. Look for realtime **position update events** exposing these values. ([ProjectX Gateway][1])

---

### 🧩 Example (Pseudo-REST Request)

```http
GET https://api.topstepx.com/api/Positions/getAll
Authorization: Bearer <your_session_token>
```

**Example Response Snippet** (conceptual):

```jsonc
[
  {
    "contractId": "CON.F.US.ES.Z26",
    "netPos": 2,
    "unrealizedPnl": 150.75,
    "realizedPnl": 102.50,
    ...
  },
  ...
]
```

Then you can sum:

```python
total_unrealized = sum(pos["unrealizedPnl"] for pos in positions)
total_realized = sum(pos["realizedPnl"] for pos in positions)
```

---

### 🧠 Notes

* The official ProjectX API docs list **Positions** endpoints under the API reference — that’s where P&L fields live. ([ProjectX Gateway][1])
* The TopstepX help center doesn’t explicitly document a separate “PnL” endpoint — the values come via the trading API structures. ([help.topstep.com][3])

---

If you want **exact field names and code examples** for the REST calls or for the Python/JS SDK, tell me your preferred language and I can generate a working snippet.

[1]: https://gateway.docs.projectx.com/docs/category/api-reference/?utm_source=chatgpt.com "API Reference - ProjectX API Documentation"
[2]: https://project-x-py.readthedocs.io/en/stable/api/trading.html?utm_source=chatgpt.com "Trading API — ProjectX Python SDK 3.3.4 documentation"
[3]: https://help.topstep.com/en/articles/11187768-topstepx-api-access?utm_source=chatgpt.com "TopstepX™ API Access - Topstep Help Center"



Short answer: **no — those risk-rule and platform-configuration values are *not* exposed via the TopstepX / ProjectX API**.

Longer, precise breakdown 👇

---

## 1️⃣ Maximum Loss Limit & Personal Daily Loss Limit

### ❌ Not exposed via any API endpoint

There is **no REST or SignalR endpoint** that returns:

* Maximum Loss Limit
* Personal Daily Loss Limit
* Trailing drawdown rules
* Remaining daily loss buffer
* Remaining max loss buffer

These values exist **outside the trading gateway layer** and live in Topstep’s **risk & compliance system**, not the ProjectX trading engine.

### Why this matters

* The ProjectX API is designed for **order routing and market data**
* Risk limits are enforced **server-side**, but **not readable**
* You only discover violations indirectly (order rejections, account lock, etc.)

### What *does* happen instead

* If you violate a rule, the API will:

  * Reject orders
  * Return generic risk-related error messages
  * Potentially disable trading for the account

But **you cannot query the thresholds themselves**.

➡️ You must track daily / max loss **locally** using fills + realized PnL.

---

## 2️⃣ OCO Brackets / Position Brackets Enabled?

### ❌ Also not exposed as account metadata

There is **no account-level flag** such as:

```json
{
  "ocoEnabled": true,
  "positionBracketsEnabled": false
}
```

Because:

* **OCO brackets** are:

  * Order-construction logic
  * Implemented by the client (TopstepX UI, Quantower, etc.)
  * Not an account property

* **Position brackets** are:

  * Platform features, not gateway features
  * Managed at the UI / platform layer

### API perspective

From the API’s point of view:

* An order is either:

  * A parent
  * Or a child linked via `parentOrderId`
* The API does **not** know whether this came from an “OCO UI toggle”

If you submit bracket orders correctly, they’ll work — regardless of what the UI shows.

---

## 3️⃣ What Account Info *Is* Exposed?

Via REST, you can typically retrieve things like:

* Account ID
* Account name
* Account status (enabled / disabled)
* Buying power / margin figures
* Positions
* Orders
* Fills

But **not**:

* Risk rule thresholds
* Evaluation vs Express vs Funded logic
* Trailing rules
* UI feature toggles

---

## 4️⃣ Practical Pattern (What Most API Users Do)

Since limits aren’t readable, most serious TopstepX API users:

### ✅ Track risk manually

* Build a **PnL ledger** from fills
* Reset realized PnL at session boundaries
* Enforce:

  * Daily loss stop
  * Max loss stop
  * Trade lockout

### ✅ Treat brackets as “always available”

* Always submit your own:

  * Stop
  * Target
  * OCO logic
* Never rely on UI-side bracket enforcement

### ✅ Handle rejections defensively

* Parse error codes/messages
* Assume a rejection could be:

  * Risk
  * Margin
  * Account state
* Fail closed

---

## 5️⃣ Bottom Line

| Item                          | Exposed via API? |
| ----------------------------- | ---------------- |
| Maximum Loss Limit            | ❌ No             |
| Personal Daily Loss Limit     | ❌ No             |
| Trailing Drawdown             | ❌ No             |
| Remaining loss buffer         | ❌ No             |
| OCO enabled flag              | ❌ No             |
| Position bracket enabled flag | ❌ No             |
| Account status                | ✅ Yes            |
| Positions / PnL               | ✅ Yes            |

---

If you want, next we can:

* Design a **drop-in risk engine** that mirrors Topstep rules
* Build a **session-aware PnL tracker**
* Create a **hard trade-lock system** that prevents violations before Topstep does

Just tell me which direction you want to go.
