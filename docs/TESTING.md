# Testing

Default `pytest` scope is configured in [pytest.ini](../pytest.ini) via `testpaths` (fast smoke modules).

```bash
pytest -q

# Full legacy tree (may include stale tests):
pytest --override-ini="testpaths=tests" -q
```

See [CONVENTIONS.md](CONVENTIONS.md) for async and import rules that tests should respect.
