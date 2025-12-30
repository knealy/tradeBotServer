# Build Solution - macOS Linker Issue

**Issue**: `cargo build --release` fails with linker errors on macOS  
**Status**: ✅ **SOLVED** - Use Maturin  
**Date**: December 5, 2025

---

## The Problem

When running `cargo build --release`, you encountered linker errors:

```
ld: Undefined symbols:
  _PyBaseObject_Type, referenced from:
  _PyBytes_AsString, referenced from:
  ...
clang: error: linker command failed with exit code 1
```

**Root Cause**: On macOS, `cargo build` doesn't automatically link against the Python library. PyO3 needs explicit Python symbols to create the extension module.

---

## The Solution: Use Maturin

[Maturin](https://www.maturin.rs/) is the official build tool for PyO3 projects. It handles all Python linking automatically.

### Quick Fix (3 commands)

```bash
# 1. Install Maturin
pip install maturin

# 2. Build and install the module
cd rust
maturin develop --release

# 3. Test it works
python3 -c "import trading_bot_rust; print('✅ Success!')"
```

That's it! Maturin will:
- ✅ Find your Python installation automatically
- ✅ Link against the correct Python library
- ✅ Build the optimized release version
- ✅ Install directly into your Python environment

---

## Why This Works

### cargo build (doesn't work on macOS)
- Tries to link manually
- Can't find Python symbols
- Requires complex environment variables
- Platform-specific issues

### maturin develop (works everywhere)
- Official PyO3 build tool
- Handles Python linking automatically
- Works on macOS, Linux, Windows
- Installs directly into Python environment
- Used by all major PyO3 projects

---

## Verification

After running `maturin develop --release`, test the module:

```python
import trading_bot_rust

# Create executor
executor = trading_bot_rust.OrderExecutor("https://api.topstepx.com")
print(f"✅ Base URL: {executor.get_base_url()}")

# Test token management
executor.set_token("test123")
print(f"✅ Token: {executor.get_token()}")

# Test contract caching
executor.set_contract_id("MNQ", 12345)
print(f"✅ Contract: {executor.get_contract_id('MNQ')}")

print("\n🎉 Rust module working perfectly!")
```

---

## What Changed

### Files Created
1. **`rust/pyproject.toml`** - Maturin configuration
2. **`rust/QUICKSTART_BUILD.md`** - Detailed build guide
3. **`rust/build.sh`** - Alternative build script
4. **`BUILD_SOLUTION.md`** - This file

### Files Updated
1. **`RUST_PHASE1_QUICKSTART.md`** - Updated build instructions
2. **`rust/Cargo.toml`** - Removed `rlib` crate type (not needed)

---

## Alternative: Manual Build (Not Recommended)

If you really want to use `cargo build` instead of maturin:

```bash
# Set Python path
export PYO3_PYTHON=$(which python3)

# Build
cd rust
cargo build --release

# Copy to Python path
cp target/release/libtrading_bot_rust.dylib ../trading_bot_rust.so

# Install in site-packages
cp trading_bot_rust.so $(python3 -c "import site; print(site.getsitepackages()[0])")
```

**But seriously, just use maturin.** It's easier, more reliable, and officially supported.

---

## Next Steps

Once built with maturin:

1. ✅ **Module is installed** - No need to copy files
2. ✅ **Ready to use** - `import trading_bot_rust` works immediately
3. ✅ **Integrate** - Follow `RUST_PHASE1_QUICKSTART.md`
4. ✅ **Benchmark** - Test performance vs Python

---

## Troubleshooting

### "maturin: command not found"
```bash
pip install --upgrade maturin
```

### "No Python interpreter found"
```bash
# Activate your venv first
source /Users/knealy/tradeBotServer/venv/bin/activate

# Then build
cd rust
maturin develop --release
```

### Still getting errors?
```bash
# Clean and rebuild
cd rust
cargo clean
maturin develop --release --verbose
```

---

## Resources

- **Maturin Documentation**: https://www.maturin.rs/
- **PyO3 Guide**: https://pyo3.rs/
- **Build Guide**: `rust/QUICKSTART_BUILD.md`
- **Integration Guide**: `RUST_PHASE1_QUICKSTART.md`
- **Full Status**: `docs/RUST_IMPLEMENTATION_STATUS.md`

---

## Summary

**Problem**: cargo build linker errors on macOS  
**Solution**: Use maturin develop --release  
**Time to fix**: 2 minutes  
**Status**: ✅ Ready to build and integrate

🚀 **Run this now**:
```bash
pip install maturin && cd rust && maturin develop --release
```

