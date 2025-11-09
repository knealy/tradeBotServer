# 📊 Frontend Feature Matrix

**Generated**: November 9, 2025  
**Purpose**: Map every capability in `trading_bot.py` (the engine) to backend exposure and current frontend coverage. This drives the roadmap for “UI parity with the bot.”

---

## Legend

| Symbol | Meaning |
| --- | --- |
| ✅ | Implemented and working today |
| 🟡 | Partially implemented (needs enhancements) |
| ⚠️ | Backend available, frontend missing |
| ❌ | Not exposed yet (backend + frontend gap) |

---

## Summary

- **Total CLI / engine capabilities reviewed**: 36  
- **Fully surfaced in frontend**: 6  
- **Exposed by API but not yet in UI**: 11  
- **Still backend work needed before UI**: 19  

High-priority next steps:
1. Performance history + trades endpoint polish (feeds dashboard chart).  
2. WebSocket/session stability & API error tracking surfaced in UI.  
3. Orders/strategies/risk management controls in the frontend.  
4. Market data endpoints (historical & real-time) for TradingView-style charts.  

---

## Accounts & Session Management

| CLI / Feature | Backend Status | Frontend Status | Notes / Follow-up |
| --- | --- | --- | --- |
| `accounts` (list) | ✅ `/api/accounts` | ✅ Account selector | Improve balance/equity detail via WebSocket. |
| `account_info` | ✅ `/api/account/info` | 🟡 Account card shows balance only | Extend API to include full account state summary, DLL, MLL. |
| `account_state` | ⚠️ Not exposed (uses `account_tracker`) | ❌ | Need `/api/account/state` returning balance, PnL, exposure, open positions. |
| `switch_account` | ✅ `/api/account/switch` | ✅ Dropdown + context | Done; ensure token reuse (WebSocket). |
| `metrics` | ✅ `/api/metrics` | 🟡 Overview card uses subset | Surface API error rate, cache stats, task_queue stats. |
| Health / status | ✅ `/health`, `/api/status` | ❌ | Add system health widget + reconnect indicator. |

---

## Orders & Position Management

| CLI / Feature | Backend Status | Frontend Status | Notes / Follow-up |
| --- | --- | --- | --- |
| `positions` | ✅ `/api/positions` | 🟡 Dashboard card (no details/actions) | Build positions table with close/flatten actions + WebSocket updates. |
| `orders` | ✅ `/api/orders` | ❌ | Need orders table with cancel/modify. |
| `trade` (market) | ✅ `/api/orders/place` (market) | ❌ | Add order ticket UI (market). |
| `limit` | ✅ `/api/orders/place` (limit) | ❌ | Extend UI ticket with limit support + price input. |
| `stop` | ❌ (`trading_bot.place_stop_order` exists but not wired) | ❌ | Expose via API + UI (stop/stop-limit). |
| `trail` | ❌ (no API wrapper) | ❌ | Expose trailing stop order endpoint & UI. |
| `bracket` | ❌ (place_bracket_order not exposed) | ❌ | Need rich order ticket supporting bracket params. |
| `native_bracket` | ❌ | ❌ | Same as above (native TopStepX bracket). |
| `close <position>` | ✅ `/api/positions/{id}/close` | ❌ | Add per-position close button + quantity selector. |
| `flatten` | ✅ `/api/positions/flatten` | ❌ | Add “flatten all” panic button w/ confirmation. |
| `cancel <order_id>` | ✅ `/api/orders/{id}/cancel` | ❌ | Integrate with orders table. |
| `cancel-all` | ✅ `/api/orders/cancel-all` | ❌ | Add “cancel all orders” button. |
| `modify` | ⚠️ `trading_bot.modify_order` exists; API missing | ❌ | Create `/api/orders/{id}/modify` + form for qty/price edits. |

---

## Market Data & Analytics

| CLI / Feature | Backend Status | Frontend Status | Notes / Follow-up |
| --- | --- | --- | --- |
| `history <symbol timeframe limit>` | ❌ (Bot method `get_historical_data` but no API) | ❌ | Create `/api/history` endpoint returning OHLCV; feed TradingView chart. |
| `quote <symbol>` | ❌ | ❌ | Expose `get_market_quote` via `/api/quote`. |
| `depth <symbol>` | ❌ | ❌ | Expose order book via `/api/depth`; optional for later. |
| Performance chart (cumulative P&L) | ⚠️ `/api/performance` returns summary only | 🟡 Static dummy chart | Build `/api/performance/history` (daily/transaction level) and feed chart. |
| Trade history (`trades`) | ✅ `/api/trades` (basic) | ❌ | Build trade log UI with filters/export. |
| Performance summary | ✅ `/api/performance` | 🟡 Metrics card (not using data) | Populate metrics, win rate, avg win/loss, etc. |
| API error rate | ✅ part of `/api/metrics` | ❌ | Show success vs failure counts + error log link. |

---

## Strategies & Automation

| CLI / Feature | Backend Status | Frontend Status | Notes / Follow-up |
| --- | --- | --- | --- |
| `strategies list/status` | ✅ `/api/strategies`, `/api/strategies/status` | 🟡 List page (read-only) | Show config, enabled flag, running state. |
| `strategies start/stop` | ✅ `/api/strategies/{name}/start|stop` | ❌ | Add per-strategy control buttons, symbol selector. |
| `strategies start_all/stop_all` | ❌ (StrategyManager has methods; no API) | ❌ | Add endpoints + “global” UI buttons. |
| `strategy_start/stop/status/test` (overnight) | ⚠️ Covered via generic strategy APIs | ❌ | Provide quick actions + diagnostics (ATR/Range preview). |
| Market condition filters toggles | ❌ (requires config endpoint) | ❌ | Need config mutation endpoint & UI toggles. |

---

## Risk, Compliance & Monitoring

| CLI / Feature | Backend Status | Frontend Status | Notes / Follow-up |
| --- | --- | --- | --- |
| `compliance` | ❌ | ❌ | Expose DLL/MLL checks via `/api/risk/compliance`. |
| `risk` | ❌ | ❌ | Endpoint returning current risk metrics (exposure, max loss). |
| `drawdown` / `max_loss` | ❌ | ❌ | Provide drawdown timeline + alerting. |
| `account_state` real-time tracker | ⚠️ Data stored in `account_tracker` JSON | ❌ | Surface via API (balance trend, open exposure). |
| Monitoring (`monitor`, `bracket_monitor`) | ❌ (internal coroutines, no API) | ❌ | Determine UI need (status indicators, manual trigger). |
| Fill automation (`auto_fills`, `check_fills`, etc.) | ❌ | ❌ | Provide toggle/status in UI + event log. |
| Discord notification status (`clear_notifications`) | ❌ | ❌ | Add endpoint for cached notifications, status view. |

---

## Market Access & Reference Data

| CLI / Feature | Backend Status | Frontend Status | Notes / Follow-up |
| --- | --- | --- | --- |
| `contracts` | ❌ (not exposed) | ❌ | Need `/api/contracts` to list instruments, tick sizes. |
| SignalR connection status | ⚠️ Internal logging only | ❌ | Show WebSocket connection health, restart button. |
| Task queue stats | ✅ part of `/api/metrics` | ❌ | Display queued/running/retry counts. |
| System logs | ⚠️ `get_system_logs` returns sample data | ❌ | Build log viewer (after real logs available). |

---

## Notifications & Integrations

| Feature | Backend Status | Frontend Status | Notes / Follow-up |
| --- | --- | --- | --- |
| Discord notifier activity | ❌ | ❌ | Need endpoint or webhook to show last alerts. |
| Railway deployment status | Outside of bot | ❌ | Optional future integration (CI/CD health). |
| Database cache metrics | ✅ (`metrics` cache section) | ❌ | Add chart for hit/miss & latency (cold vs warm). |

---

## Next Steps Checklist

1. **Spec & implement analytics endpoints**  
   - `/api/performance/history` (account cumulative P&L).  
   - `/api/trades` (date filters, pagination).  
   - `/api/history` for OHLCV (powers TradingView component).  

2. **Stabilize WebSocket & session auth**  
   - Persistent connection context; reuse token; surface connection health.  

3. **Build UI modules** (orders management, strategy controls, risk dashboard, trade log, TradingView chart).  

4. **Add backend endpoints for risk/compliance, monitoring toggles, contract metadata.**  

This document will evolve as features graduate from “gap” to “done.” Update it alongside new endpoints/UI work to keep roadmap alignment.  

---

