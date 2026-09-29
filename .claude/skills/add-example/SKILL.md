---
name: add-example
description: Add a runnable, explained example to examples/ that the tests execute. Use when a change adds a concept users should see, or the user asks for an example.
---

# Add an example

1. One concept per file: `examples/<name>.py`, in the shape of `examples/custom_detector.py`:
   - a docstring with **What:**, **How:**, **Why:** and **Run:**, in that order;
   - offline: synthetic runs from `hone_lens.testing` and the fakes, no network, no GPU;
2. List it in `examples/README.md`, in reading order.
3. List it in `examples/README.md`, which must list nothing else.
4. `uv run pytest tests/e2e/test_ac20_examples.py -q`.
