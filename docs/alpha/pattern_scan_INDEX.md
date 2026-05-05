# Pattern scan outputs

Bar-level **conditional** scans (Fisher exact on 2×2 tables + Benjamini–Hochberg FDR) on merged **1m** Databento CSVs, resampled to **5m** in `America/New_York`. Regenerate:

```bash
.venv/bin/python scripts/pattern_conditional_scan.py --symbols MNQ MES MGC \
  --start 2024-01-01 --output-dir docs/alpha
```

See [`../ALPHA_DISCOVERY.md`](../ALPHA_DISCOVERY.md) § “Conditional short-horizon scan”.

- [`pattern_scan_MNQ.md`](pattern_scan_MNQ.md)
- [`pattern_scan_MES.md`](pattern_scan_MES.md)
- [`pattern_scan_MGC.md`](pattern_scan_MGC.md)
