# Career domain example

This example keeps all career concepts outside `objecttree`. `Student`, `Skill`,
`Interest`, and `Career` are ordinary immutable dataclasses; ObjectTree only
sees registered Python values.

## Run

From the repository root (after `python -m pip install -e '.[dev]'`):

```bash
python -m examples.career.demo
```

The demo creates two assessment commits, prints the Python skill's path log and
a semantic diff, calculates skill statistics and growth, ranks career profiles,
and demonstrates `push`, fetch-without-working-tree-changes, and fast-forward
`pull` through `MemoryRemote`.

## Layout and API

- `models.py` defines only career-domain dataclasses.
- `serialization.py` registers explicit IDs such as `career.Skill`; these IDs
  remain stable if the example modules move or are invoked differently.
- `analytics.py` reads snapshots through ObjectTree's public API. Average and
  strongest-skill calculations use raw levels, growth matches stable node IDs,
  and recommendations use an explainable 70% skill / 30% interest score.
- `demo.py` is the runnable workflow.

```python
from objecttree import ObjectTree
from examples.career import Skill, average_skill_level, register_career_types

tree = ObjectTree()
register_career_types(tree)
tree.add("students/alice/skills/python", Skill("Python", 0.8))
tree.commit("Assess Alice")

print(average_skill_level(tree, "/students/alice"))
```

A reopened tree or sync peer must call `register_career_types()` before decoding
career values, because registrations are application configuration rather than
persisted executable code.
