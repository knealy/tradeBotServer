# Master dashboard page theme

The Master GUI (`/master`) uses **CSS custom properties** on `:root` for layout colors and typography. The shipped defaults are **warm** (espresso / beige / brown panels, **dusty light blue** accent, crisp brown borders)—not a flat grey zinc look. You can change the look **without editing HTML** by editing JSON and reloading the page.

## Files

| File | Role |
|------|------|
| [`config/page_theme.template.json`](../config/page_theme.template.json) | Committed defaults (kept in sync with `:root` in `gui/master_control.html`) |
| `config/page_theme.json` | **Optional local overrides** (gitignored) — merged on top of the template |
| [`scripts/init_page_theme_template.py`](../scripts/init_page_theme_template.py) | Regenerate the template file from built-in defaults |

## How it loads

1. On each `/master` load, the browser calls **`GET /api/chart/theme/page`** (same host/port as the chart server).
2. The server merges **`page_theme.template.json`** then **`page_theme.json`** (if present): later keys win.
3. Only keys starting with **`--`** are applied via `document.documentElement.style.setProperty(...)`.
4. The `<style>:root` block in HTML remains the **fallback** if the request fails.
5. **Session Theme** (toolbar button right of **Help**): native **color pickers** + **hex** fields apply the same CSS variables for **this tab only** (`sessionStorage`); loaded **after** the server merge. **Reset & reload** clears that override.
6. **Presets** (same dialog): one-click swatches mapped from your reference palettes. Each preset sets **all** `:root` variables (including **`--chart-canvas-bg`**) **and** Lightweight Charts colors (candles, volume, crosshair, …). Stored with the session bundle so a reload keeps chart + page in sync.

### Preset ids (for `applyMasterThemePreset(id)` in the console)

| id | Palette vibe |
|----|----------------|
| `earth-grid` | 21‑swatch earth grid (deep browns, camel, forest, brick) |
| `khaki-almond` | Khaki / almond / isabelline / charcoal / desert sand |
| `apricot-rose` | Light orange → apricot → coral → old rose → rose taupe |
| `modern-neutral` | Dim gray, French gray, Cambridge blue, desert sand, pale dogwood |
| `neutral-boho` | Battleship gray, chamoisee, melon, rosy brown, Payne’s gray |
| `neutral-summer` | Federal blue, charcoal, rose quartz, silver, alabaster |
| `lavender-house` | Charcoal / plum tertiary, cream text, lavender muted labels, grey accents; **chart canvas `#000000`** |

## Quick start

```bash
cp config/page_theme.template.json config/page_theme.json
# Edit colors in config/page_theme.json under "vars"
# Reload /master in the browser
```

Regenerate the committed template after hand-edits:

```bash
python scripts/init_page_theme_template.py
```

## Variables (current set)

| Variable | Typical use |
|----------|-------------|
| `--bg-primary` | Page / dashboard background |
| `--bg-secondary` | Panels, header, account bar |
| `--bg-tertiary` | Inputs, buttons, table headers |
| `--text-primary` | Main text |
| `--text-secondary` | Labels, muted text |
| `--border-color` | Panel and control borders |
| `--accent-color` | Primary actions, focus |
| `--positive-color` | Buys, profit, connected |
| `--negative-color` | Sells, loss, danger |
| `--warning-color` | Warnings |
| `--chart-canvas-bg` | Master chart pane + **Lightweight Charts** pane background (`#chart-container` + `createChart` layout); default **`#000000`** |
| `--font-size-base` | Base `rem`/`px` for body text |

Add **new** variables here and in `gui/master_control.html` CSS (use `var(--your-name)`) to extend the system.

## Related

- Other chart options (crosshair, candles, …): [`CHART_LIGHTWEIGHT_OPTIONS.md`](CHART_LIGHTWEIGHT_OPTIONS.md) and [`config/chart_theme.template.json`](../config/chart_theme.template.json)
- Manual theme refresh in the console: `applyPageThemeFromServer()` (exposed on `window`)
