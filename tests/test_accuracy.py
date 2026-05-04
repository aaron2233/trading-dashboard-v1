"""Per-indicator accuracy tests against TradingView truth values.

Each test loads a fixture from tests/fixtures/truth/<TICKER>_<indicator>.csv,
runs the Python implementation against the same bar data, and asserts the
row-level pass rate meets the v0.1 ship gate (>=95%).

Populated by later stories:
  - test_ma_ribbon_accuracy   (Story 3)
  - test_stochastic_accuracy  (Story 4)
  - test_sqn_accuracy         (Story 5)

Fixture format: tests/fixtures/truth/README.md
"""
