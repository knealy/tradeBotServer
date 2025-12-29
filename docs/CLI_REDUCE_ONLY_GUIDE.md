# How to Use Reduce-Only Orders in CLI

**Quick Fix for Your Orphaned Orders!**

---

## The Problem You Just Hit

```
✅ BUY 1 MNQ (position opened)
✅ STOP SELL 1 @ 25020 (stop loss placed)
✅ LIMIT SELL 1 @ 25060 (take profit placed)
✅ SELL 1 MNQ (position closed)

🔴 PROBLEM: Stop and Limit orders still active!
   → If price hits 25020 or 25060, you'll open a NEW position (unwanted!)
```

---

## The Solution: Use `--reduce-only` Flag

### Updated Commands

**Stop Loss (with reduce-only):**
```bash
stop mnq sell 1 25020 --reduce-only
# or short form:
stop mnq sell 1 25020 -r
```

**Take Profit (with reduce-only):**
```bash
limit mnq sell 1 25060 --reduce-only
# or short form:
limit mnq sell 1 25060 -r
```

**What --reduce-only does:**
- ✅ Links order to your position
- ✅ Auto-cancels when position closes
- ✅ Can only reduce/close positions (not open new ones)
- ✅ Perfect for SL/TP protection

---

## Example: Correct Workflow

### 1. Open Position
```bash
trade mnq buy 1
```

### 2. Add Stop Loss (reduce-only!)
```bash
stop mnq sell 1 25020 --reduce-only
```

**Output:**
```
⚠️  CONFIRM STOP ORDER:
   Symbol: MNQ
   Side: SELL
   Quantity: 1
   Stop Price: $25020.0
   🛡️  Reduce-Only: YES (auto-cancels when position closes)
   Account: PRAC-V2-14334-56363256
   Confirm? (y/N): y

✅ Stop SELL order placed successfully!
   Order ID: 2103705727
   Status: Unknown
   🛡️  Reduce-only: Will auto-cancel when position closes
```

### 3. Add Take Profit (reduce-only!)
```bash
limit mnq sell 1 25060 --reduce-only
```

**Output:**
```
⚠️  CONFIRM LIMIT ORDER:
   Symbol: MNQ
   Side: SELL
   Quantity: 1
   Price: 25060.0
   🛡️  Reduce-Only: YES (auto-cancels when position closes)
   Account: PRAC-V2-14334-56363256
   Confirm? (y/N): y

✅ Limit order placed successfully!
   Order ID: 2103737952
   Status: Unknown
   🛡️  Reduce-only: Will auto-cancel when position closes
```

### 4. Close Position
```bash
trade mnq sell 1
# or
flatten
```

**Result:**
```
✅ Position closed
✅ Stop loss: AUTO-CANCELLED
✅ Take profit: AUTO-CANCELLED
→ No orphaned orders!
```

---

## Quick Reference

### Stop Order Syntax

**Without reduce-only** (opens new position if triggered):
```bash
stop <symbol> <side> <quantity> <price>
stop mnq sell 1 25020
```

**With reduce-only** (auto-cancels when position closes):
```bash
stop <symbol> <side> <quantity> <price> --reduce-only
stop mnq sell 1 25020 --reduce-only
stop mnq sell 1 25020 -r  # short form
```

### Limit Order Syntax

**Without reduce-only** (opens new position if filled):
```bash
limit <symbol> <side> <quantity> <price>
limit mnq sell 1 25060
```

**With reduce-only** (auto-cancels when position closes):
```bash
limit <symbol> <side> <quantity> <price> --reduce-only
limit mnq sell 1 25060 --reduce-only
limit mnq sell 1 25060 -r  # short form
```

---

## When to Use --reduce-only

### ✅ ALWAYS use for:
- Stop loss orders (protection)
- Take profit orders (exits)
- Any order meant to close a position
- Trailing stops

### ❌ NEVER use for:
- Entry orders (opening new positions)
- Adding to positions (scale-in)

---

## Fix Your Current Orphaned Orders

You currently have these orphaned orders:
```
2102906564   MGC      SELL   STOP     1          $3065.00     OPEN
2103705727   MNQ      SELL   STOP     1          $24988.00    OPEN
2103737952   MNQ      SELL   LIMIT    1          $25060.00    OPEN
```

**To clean them up:**
```bash
# Cancel the orphaned orders
cancel 2102906564
cancel 2103705727
cancel 2103737952
```

**Next time, use `--reduce-only` when placing them!**

---

## Testing Reduce-Only

### Test 1: Verify Auto-Cancel

```bash
# 1. Open position
trade mnq buy 1

# 2. Add reduce-only SL
stop mnq sell 1 25020 -r

# 3. Check orders (should see SL)
orders

# 4. Close position
trade mnq sell 1

# 5. Check orders again (SL should be gone!)
orders
```

**Expected:** SL order disappears from orders list ✅

### Test 2: Verify Can't Open New Position

```bash
# 1. NO position open (start flat)
positions  # Should show no positions

# 2. Try to place reduce-only order
stop mnq sell 1 25020 -r

# 3. Wait for price to hit stop
# Result: Order gets rejected or cancelled (can't open new position with reduce-only)
```

---

## Benefits

| Without --reduce-only | With --reduce-only |
|----------------------|---------------------|
| ❌ Orders remain after position closes | ✅ Orders auto-cancel |
| ❌ Can create unwanted positions | ✅ Can only close positions |
| ❌ Need manual cleanup | ✅ No cleanup needed |
| ❌ Risk exposure | ✅ Proper risk management |

---

## Common Scenarios

### Scenario 1: Manual Day Trading

```bash
# Open LONG
trade mnq buy 1

# Set protection (both reduce-only!)
stop mnq sell 1 25300 -r   # Stop loss
limit mnq sell 1 25500 -r  # Take profit

# Trade plays out...
# Either SL hits, TP hits, or you close manually
# Result: Other order auto-cancels ✅
```

### Scenario 2: Opposite Signal

```bash
# Open LONG
trade mnq buy 1

# Set protection
stop mnq sell 1 25300 -r
limit mnq sell 1 25500 -r

# Opposite signal triggers
trade mnq sell 2  # Close LONG + open SHORT

# Result: SL and TP auto-cancel ✅
```

### Scenario 3: Partial Exit

```bash
# Open 2 contracts
trade mnq buy 2

# Set protection for full size
stop mnq sell 2 25300 -r
limit mnq sell 2 25500 -r

# Partial exit
trade mnq sell 1  # Now only 1 contract LONG

# Result: SL and TP adjust to remaining position automatically
```

---

## Summary

**Key Points:**
1. ✅ **Use `--reduce-only` or `-r` for all SL/TP orders**
2. ✅ **Orders auto-cancel when position closes**
3. ✅ **Prevents orphaned orders creating unwanted positions**
4. ✅ **No manual cleanup needed**

**Updated Commands:**
```bash
# Stop loss with reduce-only
stop mnq sell 1 25020 --reduce-only

# Take profit with reduce-only
limit mnq sell 1 25060 --reduce-only

# Both with short form
stop mnq sell 1 25020 -r
limit mnq sell 1 25060 -r
```

**This is the proper way to manage risk!** 🛡️
