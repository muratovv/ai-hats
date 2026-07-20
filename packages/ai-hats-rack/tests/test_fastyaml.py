"""fastyaml.load must be a byte-identical drop-in for yaml.safe_load (HATS-1065).

The rack swapped its read loader to libyaml for the ~13x ``rack ls`` speedup; the
acceptance bar is that ``rack ls --all --json`` stays byte-for-byte the same. That
holds iff the fast loader constructs the same Python objects as the pure loader,
so this pins the equivalence across every task.yaml field shape.
"""

from __future__ import annotations

import yaml

from ai_hats_rack import fastyaml
from ai_hats_rack.models import TaskCard

# Every tricky shape in one card: unicode, unquoted date (-> datetime.date),
# quoted timestamp (-> str), bare-string AND structured work_log, list/dict
# links, tags, and an unknown key (extras round-trip).
RICH_CARD = """\
id: HATS-1065
title: ускорить скан бэклога — libyaml ✓
state: execute
description: |
  Многострочное описание.
  Вторая строка с юникодом: ⚡ 日本語.
priority: high
reviewer: user
role: maintainer
parent_task: HATS-1014
depends_on: [HATS-1044, HATS-1029]
related: []
links:
  see_also: [HATS-1032]
tags: [perf, rack]
work_log:
  - '2026-01-01: bare string entry (pre-WorkLogEntry format)'
  - timestamp: 2026-05-01T10:00:00Z
    message: structured entry
created: 2026-07-19
updated: '2026-07-20T05:34:00Z'
attachments:
  - name: profile.txt
    digest: 41abcdef0123
"""


def test_load_matches_safe_load_exactly():
    """Fast loader builds the identical Python structure as the pure loader."""
    assert fastyaml.load(RICH_CARD) == yaml.safe_load(RICH_CARD)


def test_loaded_card_round_trips_identically():
    """TaskCard parsed via either loader serializes to the same mapping."""
    fast = TaskCard.model_validate(fastyaml.load(RICH_CARD)).to_dict()
    pure = TaskCard.model_validate(yaml.safe_load(RICH_CARD)).to_dict()
    assert fast == pure


def test_empty_document_loads_to_none():
    """An empty file -> None (the ``or {}`` guard at each call-site depends on it)."""
    assert fastyaml.load("") is None
