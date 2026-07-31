# botforge developer documentation

Meant for developers who want to contribute to botforge project.

## Dev environment

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
```

That pulls in `zoozl` and `openai-agents` alongside the package itself, so the
plugin can be imported and run locally.

## Layering

Three modules must stay free of any LLM SDK import: `tools.py`,
`dynamic_tools.py`, and `session.py`. They hold the tool descriptors, the stored
tool contract, and conversation history — none of which should depend on which
agent framework drives the loop. The framework is confined to `openai_tools.py`
(the adapter), `agent.py`, and `plugin.py`.

`__init__.py` is deliberately empty for the same reason; zoozl loads
`botforge.plugin` directly:

```toml
extensions = ["botforge.plugin"]
```

To check the boundary still holds:

```bash
.venv/bin/python -c "
import sys, importlib
sys.modules['agents'] = None
for m in ('botforge.tools', 'botforge.dynamic_tools', 'botforge.session'):
    importlib.import_module(m)
print('value layer is SDK-free')
"
```

## The stored tool contract

Dynamic tool source lives in the database, not in files, so its shape cannot be
changed by editing code — every tool ever authored by chat is written against
it. `load_tool_source()` in `dynamic_tools.py` is the single definition, used by
both the write path and the load path. If the contract genuinely has to change,
bump `CONTRACT_VERSION` and migrate or re-author the stored rows.

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

There is currently no standing suite. When a test is added, async tests need
`asyncio_mode = auto` in `pytest.ini` or `pyproject.toml`.
