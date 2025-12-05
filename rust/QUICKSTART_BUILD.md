# Quick Build Guide - Rust Module

## Issue: Linker Errors on macOS

The linker errors you're seeing (`Undefined symbols: _PyBaseObject_Type, etc.`) are because the Rust compiler can't find Python symbols to link against. This is a common issue with PyO3 on macOS.

## Solution: Use Maturin (Recommended)

Maturin is the official build tool for PyO3 projects and handles all the linking complexity automatically.

### Install Maturin

```bash
pip install maturin
```

### Build with Maturin

```bash
cd /Users/knealy/tradeBotServer/rust

# Development build (faster, with debug symbols)
maturin develop

# OR Release build (optimized)
maturin develop --release
```

This will:
1. Build the Rust code
2. Link against your Python installation automatically
3. Install the module directly into your Python environment

### Test Import

```bash
python3 -c "import trading_bot_rust; print('✅ Module loaded successfully!')"
```

## Alternative: Manual Build (Advanced)

If you prefer to use `cargo build` directly, you need to set environment variables:

```bash
# Find your Python
PYTHON_PATH=$(which python3)

# Set PyO3 environment
export PYO3_PYTHON=$PYTHON_PATH

# Build
cargo build --release

# Copy to Python path
cp target/release/libtrading_bot_rust.dylib ../trading_bot_rust.so
```

## Why Maturin?

- ✅ Handles Python linking automatically
- ✅ Works on all platforms (macOS, Linux, Windows)
- ✅ Installs directly into Python environment
- ✅ Supports development and release builds
- ✅ Official tool for PyO3 projects

## Next Steps

Once built with maturin:

1. **Test the module**:
   ```python
   import trading_bot_rust
   executor = trading_bot_rust.OrderExecutor("https://api.topstepx.com")
   print(f"✅ Base URL: {executor.get_base_url()}")
   ```

2. **Integrate with trading bot** (see `RUST_PHASE1_QUICKSTART.md`)

3. **Run benchmarks** to verify performance improvements

## Troubleshooting

### "maturin: command not found"
```bash
pip install --upgrade maturin
```

### "No Python interpreter found"
```bash
# Specify Python explicitly
maturin develop --release -i python3
```

### Still getting linker errors?
Make sure you're using the Python from your venv:
```bash
source /Users/knealy/tradeBotServer/venv/bin/activate
which python3  # Should show venv path
maturin develop --release
```

## Documentation

- Maturin: https://www.maturin.rs/
- PyO3: https://pyo3.rs/
- Full integration guide: `../RUST_PHASE1_QUICKSTART.md`

