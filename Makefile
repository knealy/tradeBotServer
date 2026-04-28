# Convenience targets (plan phase3-tests: wire pytest from repo root).
.PHONY: test verify map bench
test:
	pytest

verify:
	scripts/verify_handoff.sh

map:
	scripts/gen_map.sh

bench:
	scripts/run_bench.sh
