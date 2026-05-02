"""
Alpha Discovery — statistically-grounded signal research for tradeBotServer.

Pipeline:
    bars (CSV/API) → SessionFeatureExtractor → AlphaScanner → AlphaReport

Quick start:
    python scripts/alpha_discovery.py --symbol MNQ --csv historical_data/MNQ_5m.csv
"""
from .features import SessionFeatureExtractor
from .hypothesis import HypothesisResult, apply_fdr_correction
from .scanner import AlphaScanner
from .report import AlphaReport

__all__ = [
    "SessionFeatureExtractor",
    "HypothesisResult",
    "apply_fdr_correction",
    "AlphaScanner",
    "AlphaReport",
]
