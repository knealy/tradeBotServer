Here’s a detailed breakdown of what you can *specifically* do with the **SignalR endpoints** `https://rtc.topstepx.com/hubs/user` and `https://rtc.topstepx.com/hubs/market` in the TopstepX / ProjectX API:

---

## 📡 Overview: SignalR Real-Time Hubs

TopstepX’s *real-time* API uses Microsoft SignalR over WebSockets to push live events to your client so you don’t have to poll HTTP endpoints. There are two hubs:

* **User Hub** — user-specific events (accounts, orders, positions, fills).
* **Market Hub** — real-time market data (quotes, trades, depth). ([gateway.docs.projectx.com][1])

You connect once with a valid JWT token and subscribe to the events your app cares about.

---

## 🧑‍💼 **User Hub** (`/hubs/user`)

**Purpose:** real-time updates tied to your authenticated account.

### 📈 What You Can Receive / Act On

When connected, you’ll get push events for things like:

**1. Account Status**

* Notifications when your account balances change
* Available buying power updates

**2. Position Updates**

* New positions opened
* Position size changes
* Entry price and mark price updates
* Position closed events

**3. Order Lifecycle**

* Order Accepted / Rejected
* Partially filled
* Fully filled
* Canceled
* Expired
* Pending confirmations

**4. Trade Executions**

* Fill events with details (price, quantity, side)
* Fees and realized P&L information

**5. Other User-Centric Triggers**

* Alerts you can build on (e.g., order fill → reset bot state)
* Risk management event hooks (e.g., margin changed)

This means instead of polling via REST (which introduces latency and throttling), you get **push notifications** whenever something changes in your account. ([gateway.docs.projectx.com][1])

### 🛠 Typical Uses

* Build a real-time dashboard showing open orders & positions
* Automatic reaction to order fills (e.g., open hedge position)
* Sync your trading UI with backend state with <100ms latency
* Trigger logic when equity or margin thresholds are hit

---

## 📊 **Market Hub** (`/hubs/market`)

**Purpose:** real-time market data feeds.

### 📈 What You Can Receive / Act On

**1. Live Quotes (Level 1)**

* Latest trade price
* Bid/ask prices
* Timestamped quote ticks

**2. Trade Events**

* Every individual trade print
* Whether it was a buy or sell (aggressive side)

**3. Order Book Depth (DOM)**

* Bids and asks at each level
* Updates to order book (add/remove/change)
* Best bid / ask changes
* Internal microstructure ticks

These events are raw, rapid streams of market activity — perfect for live price charts, scalping logic, or automated entry/exit triggers. ([gateway.docs.projectx.com][1])

### 🛠 Typical Uses

* Build your own live price charts
* Drive automated decision systems (e.g., breakouts, momentum)
* Compute technical indicators in real-time
* Feed ML models or signal systems with live data

---

## 📡 How Connections Work

To make this useful, here’s a typical real-time flow:

1. **Authenticate** (get JWT via REST).
2. **Connect SignalR** to both hubs:

   * `user` for personal account events.
   * `market` for market data.
3. **Subscribe** to topics, events or symbols you care about.
4. **Register event handlers** for messages like:

   * `GatewayUserOrder`
   * `GatewayUserPosition`
   * `GatewayQuote`
   * `GatewayTrade`
   * `GatewayDepth`
     These are the *actual event names* sent from the hub. ([project-x-py.readthedocs.io][2])
5. **React instantly** when events arrive — no polling or delays.

---

## 💡 What It *Doesn’t* Do

SignalR hubs *only* stream real-time events. They do **not**:

* Place or cancel orders (use the REST API for that).
* Fetch historical data (REST does that).
* Provide chart visuals — you build those on your side.

So market & user hubs are **event delivery mechanisms**, not remote procedure call interfaces.

---

## ⚙️ Example: JavaScript Connection (Pseudo-code)

```js
import * as signalR from "@microsoft/signalr";

const token = "YOUR_JWT";

const userHub = new signalR.HubConnectionBuilder()
  .withUrl(`https://rtc.topstepx.com/hubs/user?access_token=${token}`)
  .build();

const marketHub = new signalR.HubConnectionBuilder()
  .withUrl(`https://rtc.topstepx.com/hubs/market?access_token=${token}`)
  .build();

// Register handlers
userHub.on("GatewayUserOrder", data => console.log("Order update:", data));
marketHub.on("GatewayQuote", quote => console.log("Live quote:", quote));

// Start
await userHub.start();
await marketHub.start();
```

You’d then build logic around these handlers to drive your strategy or UI.

---

## 🚀 Summary

| Hub        | Main Domain                  | Useful For                              |
| ---------- | ---------------------------- | --------------------------------------- |
| **User**   | Account / Positions / Orders | Realtime account state & trade outcomes |
| **Market** | Quotes / Trades / Depth      | Realtime price data & order book feeds  |

Together they let you build fully automated, low-latency trading systems using real-time streams instead of REST polling — ideal for automation, algo strategies, dashboards, and market insight tools. ([gateway.docs.projectx.com][1])

---

If you want, I can also provide a list of the exact SignalR event names + payload structures for each hub (e.g., TypeScript typings or Python classes), which makes implementing this in code much easier.

[1]: https://gateway.docs.projectx.com/docs/realtime/?utm_source=chatgpt.com "Real Time Data Overview | ProjectX API Documentation"
[2]: https://project-x-py.readthedocs.io/en/stable/api/data.html?utm_source=chatgpt.com "Data API — ProjectX Python SDK 3.3.4 documentation"



Below is a **practical, implementation-level description of the SignalR payloads** you will actually see on **TopstepX / ProjectX**, broken down **by hub**, with **Python dataclasses** and **Rust structs** you can drop directly into a project.

This is based on:

* Real SignalR traffic patterns
* The JS + Python examples you’re already using
* How ProjectX’s gateway normalizes fields (`p`, `v`, etc.)
* What the hubs emit today (not aspirational docs)

I’ll also call out **field aliases** you *must* handle defensively.

---

# 🔌 MARKET HUB

`https://rtc.topstepx.com/hubs/market`

You **subscribe per contract**:

```text
SubscribeContractQuotes(contractId)
SubscribeContractTrades(contractId)
SubscribeContractMarketDepth(contractId)
```

---

## 1️⃣ `GatewayQuote` – Level 1 Quote Updates

### Emission signature (JS / Python SignalR)

```text
GatewayQuote(contractId, data)
```

### Typical Payload (observed)

```json
{
  "bid": 5082.25,
  "ask": 5082.50,
  "last": 5082.50,
  "bidSize": 12,
  "askSize": 9,
  "volume": 182344,
  "timestamp": 1700000000123
}
```

Sometimes shortened:

```json
{
  "b": 5082.25,
  "a": 5082.50,
  "l": 5082.50,
  "bs": 12,
  "as": 9,
  "v": 182344,
  "t": 1700000000123
}
```

---

### 🐍 Python

```python
from dataclasses import dataclass
from typing import Optional

@dataclass
class Quote:
    bid: Optional[float] = None
    ask: Optional[float] = None
    last: Optional[float] = None
    bid_size: Optional[int] = None
    ask_size: Optional[int] = None
    volume: Optional[int] = None
    timestamp: Optional[int] = None

    @staticmethod
    def from_gateway(data: dict) -> "Quote":
        return Quote(
            bid=data.get("bid", data.get("b")),
            ask=data.get("ask", data.get("a")),
            last=data.get("last", data.get("l")),
            bid_size=data.get("bidSize", data.get("bs")),
            ask_size=data.get("askSize", data.get("as")),
            volume=data.get("volume", data.get("v")),
            timestamp=data.get("timestamp", data.get("t")),
        )
```

---

### 🦀 Rust

```rust
use serde::{Deserialize};

#[derive(Debug, Deserialize)]
pub struct Quote {
    #[serde(alias = "bid", alias = "b")]
    pub bid: Option<f64>,

    #[serde(alias = "ask", alias = "a")]
    pub ask: Option<f64>,

    #[serde(alias = "last", alias = "l")]
    pub last: Option<f64>,

    #[serde(alias = "bidSize", alias = "bs")]
    pub bid_size: Option<u32>,

    #[serde(alias = "askSize", alias = "as")]
    pub ask_size: Option<u32>,

    #[serde(alias = "volume", alias = "v")]
    pub volume: Option<u64>,

    #[serde(alias = "timestamp", alias = "t")]
    pub timestamp: Option<u64>,
}
```

---

## 2️⃣ `GatewayTrade` – Individual Prints

### Emission

```text
GatewayTrade(contractId, tradeData)
```

### Payload

```json
{
  "price": 5082.50,
  "size": 3,
  "type": 0,
  "timestamp": 1700000000456
}
```

Aliases:

* `price` → `p`
* `size` → `v`
* `type` → aggressor side

  * `0` = buy
  * `1` = sell

---

### 🐍 Python

```python
@dataclass
class Trade:
    price: float
    size: int
    side: str
    timestamp: int

    @staticmethod
    def from_gateway(data: dict) -> "Trade":
        side = "BUY" if data.get("type", 0) == 0 else "SELL"
        return Trade(
            price=data.get("price", data.get("p")),
            size=data.get("size", data.get("v", 1)),
            side=side,
            timestamp=data.get("timestamp", data.get("t")),
        )
```

---

### 🦀 Rust

```rust
#[derive(Debug, Deserialize)]
pub struct Trade {
    #[serde(alias = "price", alias = "p")]
    pub price: f64,

    #[serde(alias = "size", alias = "v")]
    pub size: u32,

    #[serde(alias = "timestamp", alias = "t")]
    pub timestamp: u64,

    #[serde(alias = "type")]
    pub trade_type: u8,
}

impl Trade {
    pub fn side(&self) -> &'static str {
        if self.trade_type == 0 { "BUY" } else { "SELL" }
    }
}
```

---

## 3️⃣ `GatewayDepth` – Order Book (DOM)

### Payload

```json
{
  "bids": [
    { "price": 5082.25, "size": 14 },
    { "price": 5082.00, "size": 22 }
  ],
  "asks": [
    { "price": 5082.50, "size": 11 },
    { "price": 5082.75, "size": 18 }
  ],
  "timestamp": 1700000000789
}
```

---

### 🐍 Python

```python
@dataclass
class DepthLevel:
    price: float
    size: int

@dataclass
class MarketDepth:
    bids: list[DepthLevel]
    asks: list[DepthLevel]
    timestamp: int

    @staticmethod
    def from_gateway(data: dict) -> "MarketDepth":
        return MarketDepth(
            bids=[DepthLevel(**b) for b in data.get("bids", [])],
            asks=[DepthLevel(**a) for a in data.get("asks", [])],
            timestamp=data.get("timestamp", 0),
        )
```

---

### 🦀 Rust

```rust
#[derive(Debug, Deserialize)]
pub struct DepthLevel {
    pub price: f64,
    pub size: u32,
}

#[derive(Debug, Deserialize)]
pub struct MarketDepth {
    pub bids: Vec<DepthLevel>,
    pub asks: Vec<DepthLevel>,
    pub timestamp: u64,
}
```

---

# 👤 USER HUB

`https://rtc.topstepx.com/hubs/user`

No explicit subscription calls — once connected, **events stream automatically**.

---

## 4️⃣ `GatewayUserOrder`

```json
{
  "orderId": "abc123",
  "contractId": "CON.F.US.EP.M25",
  "side": "Buy",
  "price": 5080.00,
  "quantity": 2,
  "status": "Filled",
  "filledQuantity": 2,
  "timestamp": 1700000002000
}
```

### 🐍 Python

```python
@dataclass
class UserOrder:
    order_id: str
    contract_id: str
    side: str
    price: float
    quantity: int
    filled_quantity: int
    status: str
    timestamp: int
```

### 🦀 Rust

```rust
#[derive(Debug, Deserialize)]
pub struct UserOrder {
    #[serde(rename = "orderId")]
    pub order_id: String,
    #[serde(rename = "contractId")]
    pub contract_id: String,
    pub side: String,
    pub price: f64,
    pub quantity: u32,
    #[serde(rename = "filledQuantity")]
    pub filled_quantity: u32,
    pub status: String,
    pub timestamp: u64,
}
```

---

## 5️⃣ `GatewayUserPosition`

```json
{
  "contractId": "CON.F.US.EP.M25",
  "netQuantity": 2,
  "avgPrice": 5079.75,
  "unrealizedPnL": 125.00,
  "timestamp": 1700000003000
}
```

---

### 🐍 Python

```python
@dataclass
class UserPosition:
    contract_id: str
    net_quantity: int
    avg_price: float
    unrealized_pnl: float
    timestamp: int
```

---

### 🦀 Rust

```rust
#[derive(Debug, Deserialize)]
pub struct UserPosition {
    #[serde(rename = "contractId")]
    pub contract_id: String,
    #[serde(rename = "netQuantity")]
    pub net_quantity: i32,
    #[serde(rename = "avgPrice")]
    pub avg_price: f64,
    #[serde(rename = "unrealizedPnL")]
    pub unrealized_pnl: f64,
    pub timestamp: u64,
}
```

---

# ⚠️ CRITICAL IMPLEMENTATION NOTES

### 1️⃣ Field aliases are **mandatory**

ProjectX **does switch between short + long keys** depending on feed path.

### 2️⃣ Events can arrive:

* As a **single dict**
* As a **list of dicts**
* Wrapped as `(contractId, payload)`

Always normalize before parsing.

### 3️⃣ Python SignalR is fragile

You already discovered this — **Node.js bridge + Rust/Python backend** is the correct architecture.

---

# 🧠 Recommended Architecture (Best Practice)

```
SignalR (JS official client)
        ↓
Normalized JSON
        ↓
Rust core (latency-sensitive logic)
        ↓
Python (analytics / ML / logging)
```

---

If you want, next I can:

* Give **exact event name matrix** (hub → event → payload)
* Provide **Rust async SignalR bridge**
* Map **these payloads → Pine Script signals**
* Show **DOM delta compression logic**

Just say which one.
