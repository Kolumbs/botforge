# botforge — working agreements

## Testing policy

Tests are written by a dedicated testing persona/agent. They are never written by
the persona or agent that wrote the feature or fixed the bug being tested.
Verification stays independent of implementation.

1. **Only the testing persona writes tests.** An agent implementing a feature or
   fixing a bug does not write tests for its own work, and does not add tests
   "while it is in there".

2. **Tests are read-only to implementers.** An agent fixing a bug or writing a
   feature must not change, delete, weaken, re-scope, or skip an existing test —
   not to make it pass, not to modernise it, not as cleanup.

3. **A failing test is a finding, not an obstacle.** Fix the code. If the test
   itself appears wrong, say so and hand it back to the testing persona; do not
   edit it in place.

4. **Tests are targeted.** They are written against an identified bug or a
   specific behaviour worth pinning down — not maintained as broad scaffolding
   that grows alongside development.

**Why:** an implementer who can also write and edit the tests can make any change
look verified. Keeping the roles apart means a passing suite is evidence about
the code, rather than evidence about what its author intended.

There is currently no standing suite. When a test is added, note that async tests
need `asyncio_mode = auto` (in `pytest.ini` or `pyproject.toml`), and the dev
extra installs the runner:

```bash
python -m venv .venv && .venv/bin/pip install -e '.[dev]'
```
