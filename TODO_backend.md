### BACKEND DESIGN:
- ~~failed to implement a hybrid, non-native version of bracket orders~~ → **paused**.
  Hybrid path left orphans / false cancel claims; not reliable enough for live.
  **Now:** on broker ``Position Brackets`` / Auto OCO disabled reject → one-shot
  Discord + ``BRACKET_MODE_ALERT`` telling you to enable **Auto OCO Brackets** in
  ProjectX; order is **refused** (no silent hybrid). Opt-in smoke only:
  ``TOPSTEPX_BRACKET_MODE=hybrid``.
  Deferred hybrid issues (if revisited):
    1. sweeper falsely claimed TP cancel while orphaned
    2. smoke ``hybrid_orders=N`` counted leftover TB-hyb-* from prior tests
    3. modify-leg while open
    4. event-driven order updates for bracket refs
