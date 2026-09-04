from __future__ import annotations

import re
from typing import Any
from pydantic import BaseModel, ValidationError

from .contracts import (
    LearningContractItemDefinition,
)
from .schemas import (
    LearningAnswerProposal,
    LearningContractItemProposal,
    LearningMetricProposal,
    LearningReportValidationError,
)

_UNSUPPORTED_EXTERNAL_CAUSALITY = re.compile(
    r"(?:导致|造成|带来|提升|提高|降低|减少|增加|推动|引发|"
    r"拉升|强化|增强|迫使|促使|吸引|激发|诱导|驱使)"
    r".{0,16}"
    r"(?:读者流失|读者留存|追读率|留存率|付费率|追读欲望|"
    r"阅读欲望|读者预期|读者期待|读者信任|读者共鸣|"
    r"读者耐心|读者兴趣|读者翻页|读者继续阅读|"
    r"读者好奇|读者关注|读者关切|读者渴望|读者想知道|"
    r"阅读冲动|追读欲望|留住读者|弃书|销量|口碑|"
    r"商业成功|市场表现|受众共鸣)"
)
_EXTERNAL_EFFECT_TERM = re.compile(
    r"(?:读者流失|读者留存|追读率|留存率|付费率|留住读者|"
    r"读者预期|读者期待|读者信任|读者共鸣|读者耐心|"
    r"读者兴趣|读者翻页|读者继续阅读|读者好奇|"
    r"读者关注|读者关切|读者渴望|读者想知道|"
    r"阅读冲动|追读欲望|弃书|销量|口碑|商业成功|"
    r"市场表现|受众共鸣)"
)
_READER_BEHAVIOR_OR_PSYCHOLOGY = re.compile(
    r"(?:读者|受众).{0,8}"
    r"(?:翻页|继续阅读|追读|好奇|关注|关切|渴望|想知道|"
    r"期待|兴趣|共鸣|耐心)"
)
_UNSUPPORTED_EXTERNAL_EFFECT_ASSERTION = re.compile(
    r"(?:读者流失|读者留存|追读率|留存率|付费率|留住读者|"
    r"读者预期|读者期待|读者信任|读者共鸣|读者耐心|"
    r"读者兴趣|读者翻页|读者继续阅读|读者好奇|"
    r"读者关注|读者关切|读者渴望|读者想知道|"
    r"阅读冲动|追读欲望|弃书|销量|口碑|商业成功|"
    r"市场表现|受众共鸣)"
    r".{0,16}"
    r"(?:提升|提高|降低|减少|增加|上升|下降|改善|恶化|"
    r"增强|减弱|变好|变差|更高|更低|很好|很差|建立|形成)"
)
_EXTERNAL_EFFECT_UNCERTAINTY = re.compile(
    r"(?:不能|无法|不可|不得|缺少|需要|需|仍需|有待|待)"
    r".{0,20}"
    r"(?:证明|验证|数据|外推|判断)"
)
_EXTERNAL_EFFECT_QUESTION = re.compile(
    r"(?:是否|能否|会否|尚不确定|尚不明确|仍不确定|未知)"
)
_UNSUPPORTED_EXTERNAL_MAGNITUDE = re.compile(
    r"(?:较大|很大|极大|极高|高)"
    r".{0,6}"
    r"(?:概率|风险)"
    r".{0,12}"
    r"(?:读者流失|读者留存|弃书|追读|付费|销量|口碑|市场)"
)
_PROHIBITED_PRICING_TERM = re.compile(
    r"(?:价格|价钱|报价|售价|收费|收取费用|计费|费率|"
    r"价格估算|币种|金额|单价|费用|"
    r"(?:模型|调用|API|Token|令牌|推理|生成).{0,8}(?:成本|花费)|"
    r"人民币|美元|欧元|日元|英镑|港币|CNY|RMB|USD|EUR|JPY|GBP|HKD|"
    r"[¥￥$€£]|"
    r"(?:\d[\d,]*(?:\.\d+)?|[零〇一二三四五六七八九十百千万亿两]+)"
    r"\s*(?:元|块钱|人民币|美元|欧元|日元|英镑|港币)|"
    r"(?:Token|令牌|调用).{0,16}(?:收|花费|耗费|成本)"
    r".{0,8}\d+(?:\.\d+)?\s*(?:元|块))",
    re.IGNORECASE,
)
_USER_TEXT_CLAUSE_BREAK = re.compile(
    r"[，,。；;！？!?\n\r：:]+|(?<!不)但(?:是)?|然而|不过|可是|反而"
)
_CHAPTER_END_RESPONSE_DISTANCE = re.compile(
    r"(?:回应|兑现(?!前置)|回收).{0,12}"
    r"(?:距离|章距|字距|间隔|跨度|章数|字数|字符数|耗时|用时|"
    r"下一章|下章|第[0-9一二三四五六七八九十百]+章|"
    r"\d[\d,，]*\s*(?:章|字|字符))"
    r"|(?:下一章|下章|第[0-9一二三四五六七八九十百]+章|"
    r"\d[\d,，]*\s*(?:章|字|字符))"
    r".{0,12}(?:回应|兑现(?!前置)|回收)"
)
_CHAPTER_END_RESPONSE_SCOPE_DENIAL = re.compile(
    r"(?:4\.9.{0,12})?"
    r"(?:不(?:负责|统计|追踪|计算|记录|涉及|包含|分析|回答)|"
    r"不得.{0,8}|无需.{0,8}|无须.{0,8})"
    r".{0,16}(?:回应|兑现(?!前置)|回收)"
    r"|(?:回应|兑现(?!前置)|回收).{0,12}"
    r"(?:距离|章距|字距|间隔|跨度|章数|字数|字符数)"
    r".{0,16}(?:不在|不属于|应由|交由|由)"
    r".{0,12}(?:4\.9|3\.4|4\.10|职责|范围)"
)

_CHAPTER_END_HOOK_TYPE_LABELS = {
    "CRISIS_SUSPENSION": ("CRISIS_SUSPENSION", "危机悬置"),
    "NEW_INFORMATION": ("NEW_INFORMATION", "新信息抛出", "新信息"),
    "PAYOFF_PRIMING": ("PAYOFF_PRIMING", "期待兑现前置"),
    "REVERSAL": ("REVERSAL", "反转"),
    "EMOTIONAL_FREEZE": ("EMOTIONAL_FREEZE", "情绪定格"),
    "NONE": ("NONE", "无钩"),
}
_CHAPTER_END_HOOK_STRENGTH_LABELS = {
    "STRONG": ("STRONG", "强钩"),
    "MEDIUM": ("MEDIUM", "中钩"),
    "LIGHT": ("LIGHT", "轻钩", "缓钩"),
    "NONE": ("NONE", "无钩"),
}
_PAYOFF_EXCLUSION_LABELS = {
    "PROMISE_SOURCE": "前置承诺来源",
    "PROMISE_RESTATEMENT": "承诺重述",
    "IDENTITY_GRANT": "身份授予",
    "DREAM_OR_HISTORY": "梦境或历史讲述",
    "STATIC_EVIDENCE": "静态物证或演示",
    "SIMULATION": "模拟场面",
    "PARTIAL_ONLY": "只部分兑现",
    "UNRELATED": "与核心承诺无关",
}


def _evidence_ids(value: object) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if key.endswith("evidence_ids") and isinstance(item, list):
                found.update(str(entry) for entry in item if entry)
            elif key.endswith("evidence_id") and item:
                found.add(str(item))
            else:
                found.update(_evidence_ids(item))
    elif isinstance(value, list):
        for item in value:
            found.update(_evidence_ids(item))
    return found


def _contract_item_by_id(
    answer: LearningAnswerProposal,
) -> dict[str, LearningContractItemProposal]:
    return {item.item_id: item for item in answer.contract_items}


def _metric_blob(item: LearningContractItemProposal) -> str:
    return "；".join(
        f"{metric.label}={metric.value}{metric.unit}（{metric.method}）"
        for metric in item.metrics
    )


def _metric_mentions_number(
    item: LearningContractItemProposal,
    value: int,
) -> bool:
    return re.search(
        rf"(?<!\d){re.escape(str(value))}(?!\d)",
        _metric_blob(item),
    ) is not None


def _metrics_with_label(
    item: LearningContractItemProposal,
    aliases: tuple[str, ...],
) -> list[LearningMetricProposal]:
    return [
        metric
        for metric in item.metrics
        if any(alias.casefold() in metric.label.casefold() for alias in aliases)
    ]


def _metric_value_matches_number(
    metric: LearningMetricProposal,
    value: int,
) -> bool:
    return re.search(
        rf"(?<!\d){re.escape(str(value))}(?!\d)",
        metric.value,
    ) is not None


def _metric_group_matches_count(
    item: LearningContractItemProposal,
    aliases: tuple[str, ...],
    count: int,
) -> bool:
    return any(
        _metric_value_matches_number(metric, count)
        for metric in _metrics_with_label(item, aliases)
    )


def _metric_group_matches_ratio(
    item: LearningContractItemProposal,
    aliases: tuple[str, ...],
    ratio: float,
) -> bool:
    decimal_markers = {
        str(ratio),
        f"{ratio:.4f}",
        f"{ratio:.2f}",
        f"{ratio:.4f}".rstrip("0").rstrip("."),
        f"{ratio:.2f}".rstrip("0").rstrip("."),
    }
    percent_markers = {
        f"{ratio * 100:.2f}",
        f"{ratio * 100:.1f}",
        f"{ratio * 100:.2f}".rstrip("0").rstrip("."),
        f"{ratio * 100:.1f}".rstrip("0").rstrip("."),
    }
    for metric in _metrics_with_label(item, aliases):
        ratio_text = "；".join((metric.value, metric.unit, metric.method))
        decimal_text = (
            ratio_text
            if re.search(r"(?:占比|比例)", metric.label)
            else "；".join((metric.unit, metric.method))
        )
        if any(
            re.search(rf"(?<!\d){re.escape(marker)}\s*%", ratio_text)
            for marker in percent_markers
            if marker
        ) or any(
            re.search(
                rf"(?<![\d.]){re.escape(marker)}(?![\d.])",
                decimal_text,
            )
            for marker in decimal_markers
            if marker
        ):
            return True
    return False


def _validate_answer_evidence_subset(
    answer: LearningAnswerProposal,
    allowed_evidence_ids: set[str],
    *,
    error_code: str,
) -> None:
    referenced = _evidence_ids(answer.model_dump(mode="json"))
    if not referenced or not referenced.issubset(allowed_evidence_ids):
        raise ValueError(error_code)


def _answer_user_visible_texts(
    answer: LearningAnswerProposal,
) -> list[str]:
    return [
        answer.conclusion,
        answer.handbook.why_important if answer.handbook else "",
        *(answer.handbook.universal_methods if answer.handbook else []),
        *(item.mistake for item in (answer.handbook.common_errors if answer.handbook else [])),
        *(item.fix for item in (answer.handbook.common_errors if answer.handbook else [])),
        *(
            checkpoint
            for template in (answer.handbook.templates if answer.handbook else [])
            for checkpoint in template.checkpoints
        ),
        *answer.limitations,
        *answer.reusable_lessons,
        *answer.do_not_copy,
        *(
            text
            for item in answer.contract_items
            for text in (
                item.finding,
                *item.limitations,
                *(
                    value
                    for metric in item.metrics
                    for value in (
                        metric.label,
                        metric.value,
                        metric.unit,
                        metric.method,
                    )
                ),
            )
        ),
        *(
            value
            for metric in answer.metrics
            for value in (
                metric.label,
                metric.value,
                metric.unit,
                metric.method,
            )
        ),
    ]


def _answer_external_causality_texts(
    answer: LearningAnswerProposal,
) -> list[str]:
    return [
        answer.conclusion,
        *answer.limitations,
        *answer.reusable_lessons,
        *answer.do_not_copy,
        *(
            text
            for item in answer.contract_items
            for text in (
                item.finding,
                *item.limitations,
                *(
                    value
                    for metric in item.metrics
                    for value in (
                        metric.label,
                        metric.value,
                        metric.unit,
                        metric.method,
                    )
                ),
            )
        ),
        *(
            value
            for metric in answer.metrics
            for value in (
                metric.label,
                metric.value,
                metric.unit,
                metric.method,
            )
        ),
    ]


def _split_user_text_clauses(text: str) -> list[str]:
    return [
        clause.strip()
        for clause in _USER_TEXT_CLAUSE_BREAK.split(text)
        if clause.strip()
    ]


def _clause_marks_external_effect_uncertainty(clause: str) -> bool:
    return bool(
        _EXTERNAL_EFFECT_UNCERTAINTY.search(clause)
        or _EXTERNAL_EFFECT_QUESTION.search(clause)
    )


def _has_unsupported_external_effect_claim(text: str) -> bool:
    clauses = _split_user_text_clauses(text)
    for index, clause in enumerate(clauses):
        if _UNSUPPORTED_EXTERNAL_MAGNITUDE.search(clause):
            return True
        has_causal_claim = bool(
            _UNSUPPORTED_EXTERNAL_CAUSALITY.search(clause)
            or _UNSUPPORTED_EXTERNAL_EFFECT_ASSERTION.search(clause)
            or _READER_BEHAVIOR_OR_PSYCHOLOGY.search(clause)
        )
        if not has_causal_claim and not _EXTERNAL_EFFECT_TERM.search(clause):
            continue
        if _clause_marks_external_effect_uncertainty(clause):
            continue
        if has_causal_claim:
            return True
        adjacent_clauses = clauses[max(0, index - 1):index] + clauses[
            index + 1:index + 2
        ]
        if any(
            _clause_marks_external_effect_uncertainty(adjacent)
            for adjacent in adjacent_clauses
        ):
            continue
        return True
    return False


def _has_out_of_scope_4_9_response_distance(
    answer: LearningAnswerProposal,
) -> bool:
    return any(
        _CHAPTER_END_RESPONSE_DISTANCE.search(clause)
        and not _CHAPTER_END_RESPONSE_SCOPE_DENIAL.search(clause)
        for text in _answer_user_visible_texts(answer)
        for clause in _split_user_text_clauses(text)
    )


def _validate_answer_user_text_boundaries(
    answer: LearningAnswerProposal,
) -> None:
    answer_texts = _answer_user_visible_texts(answer)
    if any(_PROHIBITED_PRICING_TERM.search(text) for text in answer_texts):
        raise LearningReportValidationError(
            "LEARNING_REPORT_PRICING_OUT_OF_SCOPE",
            [{
                "path": ["answers", answer.question_id],
                "type": "value_error",
                "message": (
                    "创作学习答案只记录 Token、调用、重试、耗时和失败，"
                    "不得出现价格、币种、金额、单价或费用字段。"
                ),
            }],
        )
    unsupported_claims = [
        text
        for text in _answer_external_causality_texts(answer)
        if _has_unsupported_external_effect_claim(text)
    ]
    if unsupported_claims:
        raise LearningReportValidationError(
            "LEARNING_REPORT_EXTERNAL_CAUSALITY_UNSUPPORTED",
            [{
                "path": ["answers", answer.question_id],
                "type": "value_error",
                "message": (
                    "书内结构证据不能证明读者流失、留存、销量、"
                    "口碑或市场表现的因果；请改写为结构观察或待外部数据验证。"
                ),
            }],
        )
    if (
        answer.question_id == "4.9"
        and _has_out_of_scope_4_9_response_distance(answer)
    ):
        raise LearningReportValidationError(
            "LEARNING_REPORT_4_9_RESPONSE_METRIC_OUT_OF_SCOPE",
            [{
                "path": ["answers", answer.question_id],
                "type": "value_error",
                "message": (
                    "4.9 只回答章末钩类型和节律，不得在用户答案中"
                    "记录回应、兑现或回收距离。"
                ),
            }],
        )


def _validation_errors(error: ValidationError) -> list[dict[str, Any]]:
    return [
        {
            "path": list(entry.get("loc", ())),
            "type": str(entry.get("type", "value_error")),
            "message": str(entry.get("msg", "Validation error")),
        }
        for entry in error.errors()
    ]


def _inline_model_schema(model: type[BaseModel]) -> dict:
    schema = model.model_json_schema()
    defs = schema.pop("$defs", {})

    def expand(value: object) -> object:
        if isinstance(value, dict):
            if "$ref" in value:
                ref_key = value["$ref"].split("/")[-1]
                return expand(defs.get(ref_key, {}))
            return {k: expand(v) for k, v in value.items()}
        if isinstance(value, list):
            return [expand(item) for item in value]
        return value

    return expand(schema)  # type: ignore[return-value]
