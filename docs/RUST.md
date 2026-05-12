# Rust hot path

## Current state

- **Opt-in:** `TOPSTEPX_USE_RUST` (default off). Only a few call sites in the Python stack invoke the extension; most logic stays in Python.
- **Crate:** `rust/` (Cargo project). See [DECISIONS.md](DECISIONS.md) for the “freeze vs finish” stance.

## Reference notes (may lag code)

- [reference/rust/RUST_INTEGRATION_GUIDE.md](reference/rust/RUST_INTEGRATION_GUIDE.md)
- [reference/rust/RUST_PHASE1_QUICKSTART.md](reference/rust/RUST_PHASE1_QUICKSTART.md)
- [archive/RUST_PYTHON_EXECUTION_PATHS.md](archive/RUST_PYTHON_EXECUTION_PATHS.md) — path overview (archived; may lag `rust/` layout).

Treat benchmark numbers in old docs as **environment-specific** unless reproduced on your hardware.
