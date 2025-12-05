# Installation Guide - Rust Module

## Issue: Python 2 vs Python 3

If you see errors like `ValueError: unsupported hash type md5`, it means `pip` is using Python 2.7 instead of Python 3.

## Solution: Use Python 3

### Option 1: Use pip3 (Recommended)

```bash
# Install maturin with pip3
pip3 install maturin

# Build the module
cd rust
maturin develop --release
```

### Option 2: Activate Virtual Environment First

```bash
# Activate your venv (if you have one)
cd /Users/knealy/tradeBotServer
source venv/bin/activate

# Now pip will use Python 3
pip install maturin

# Build
cd rust
maturin develop --release
```

### Option 3: Use python3 -m pip

```bash
# This ensures Python 3's pip is used
python3 -m pip install maturin

# Build
cd rust
maturin develop --release
```

## Verify Python Version

Before installing, verify you're using Python 3:

```bash
python3 --version  # Should show Python 3.x.x
which python3      # Should show path to Python 3
```

## Complete Installation Steps

```bash
# 1. Navigate to project root
cd /Users/knealy/tradeBotServer

# 2. Activate venv (if you have one)
source venv/bin/activate

# 3. Install maturin with Python 3
pip3 install maturin
# OR: python3 -m pip install maturin

# 4. Build the Rust module
cd rust
maturin develop --release

# 5. Test installation
python3 -c "import trading_bot_rust; print('✅ Module loaded successfully!')"
```

## Troubleshooting

### "pip3: command not found"

Install Python 3 pip:
```bash
python3 -m ensurepip --upgrade
```

### "maturin: command not found"

Make sure you installed it with Python 3:
```bash
which maturin  # Should show a path
pip3 show maturin  # Should show maturin is installed
```

### Still using Python 2?

Check your PATH:
```bash
echo $PATH
which pip
which pip3
```

Use `pip3` explicitly or activate your venv.

## Quick Command (Copy & Paste)

```bash
cd /Users/knealy/tradeBotServer && \
source venv/bin/activate 2>/dev/null || true && \
pip3 install maturin && \
cd rust && \
maturin develop --release && \
python3 -c "import trading_bot_rust; print('✅ Success!')"
```

This will:
1. Navigate to project
2. Activate venv (if exists)
3. Install maturin with Python 3
4. Build the module
5. Test it works

