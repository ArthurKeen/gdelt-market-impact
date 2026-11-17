# Tests

Two suites, split by what they need.

| | needs a database | run it |
|---|---|---|
| `test_pure.py` | no | `.venv/bin/python -m pytest tests/test_pure.py` |
| `test_graph.py` | yes, a loaded `gdelt_market` | `.venv/bin/python -m pytest tests/` |

`test_pure.py` covers the arithmetic that produced every published number, and runs anywhere in under a second. Start there.

These are **invariants, not coverage.** Each one exists because getting it wrong
already cost real time on this project — the event-study window anchored a day late
and flipped signs, a merge that would have deleted the file it was adding. They are cheap regression tests for expensive
mistakes rather than an attempt at a percentage.
