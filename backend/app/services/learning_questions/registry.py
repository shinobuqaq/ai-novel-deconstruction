from __future__ import annotations

from typing import Any
from .base import BaseQuestionPlugin
from .contracts import LEARNING_QUESTION_CATALOG
from .plugins.q1_4 import Q1_4Plugin
from .plugins.q2_1 import Q2_1Plugin
from .plugins.q2_2 import Q2_2Plugin
from .plugins.q3_1 import Q3_1Plugin
from .plugins.q3_2 import Q3_2Plugin
from .plugins.q3_4 import Q3_4Plugin
from .plugins.q4_9 import Q4_9Plugin
from .plugins.standard import StandardQuestionPlugin

_SPECIALIZED_PLUGINS: dict[str, BaseQuestionPlugin] = {
    "1.4": Q1_4Plugin(),
    "2.1": Q2_1Plugin(),
    "2.2": Q2_2Plugin(),
    "3.1": Q3_1Plugin(),
    "3.2": Q3_2Plugin(),
    "3.4": Q3_4Plugin(),
    "4.9": Q4_9Plugin(),
}

_QUESTION_REGISTRY: dict[str, BaseQuestionPlugin] = {}

def _init_registry() -> None:
    for q in LEARNING_QUESTION_CATALOG:
        qid = q.question_id
        if qid in _SPECIALIZED_PLUGINS:
            _QUESTION_REGISTRY[qid] = _SPECIALIZED_PLUGINS[qid]
        else:
            _QUESTION_REGISTRY[qid] = StandardQuestionPlugin(qid)

_init_registry()


def get_question_plugin(question_id: str) -> BaseQuestionPlugin:
    if question_id not in _QUESTION_REGISTRY:
        return StandardQuestionPlugin(question_id)
    return _QUESTION_REGISTRY[question_id]


def get_all_question_plugins() -> dict[str, BaseQuestionPlugin]:
    return dict(_QUESTION_REGISTRY)
