from __future__ import annotations

import json
import hashlib
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import Settings
from ..models import (
    AnalysisRun,
    AnalysisRunStatus,
    AnalysisRunTask,
    DeepAnalysis,
    EvidenceSpan,
    LearningReport,
    SourceUnit,
    SourceVersion,
    Task,
    TaskStatus,
)
from .provider_config import (
    ENTITIES_EVENTS_PROFILE_ID,
    ModelSettingsError,
    prepare_task_provider_routes,
    resolve_analysis_profile,
)
from .source_import import source_text


LEARNING_REPORT_TASK_KIND = "analysis.learning_report"
LEARNING_REPORT_PROMPT_ID = "learning_report"
LEARNING_REPORT_PROMPT_VERSION = "1.6.0"
LEARNING_REPORT_COMPATIBLE_PROMPT_VERSIONS = frozenset({
    "1.4.2",
    "1.4.3",
    "1.5.0",
    "1.5.1",
    "1.5.2",
    "1.5.3",
    "1.5.4",
    "1.5.5",
    "1.6.0",
})
LEARNING_QUESTION_CATALOG_VERSION = "1.3.0"
LEARNING_REPORT_BATCH_LABEL = "首组逐问增量编译（5/42）"
LEARNING_ANSWER_DEFAULT_SOFT_INPUT_CAP_TOKENS = 150_000
LEARNING_ANSWER_ESTIMATED_CHARS_PER_TOKEN = 1.5
OPENING_PAYOFF_MAX_WINDOW_CANDIDATES = 200

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
LEARNING_QUESTION_CONTRACT_VERSIONS = {
    "1.4": "2.4.0",
    "2.1": "2.0.0",
    "2.2": "2.1.0",
    "4.9": "2.0.0",
}


@dataclass(frozen=True, slots=True)
class LearningQuestionDefinition:
    question_id: str
    question: str
    analysis_requirement: str
    evidence_view: str
    output_contract: str
    measurement_requirements: str
    evidence_requirements: str
    scope_requirement: str
    external_data_policy: str

    @property
    def stage_id(self) -> int:
        return int(self.question_id.split(".", 1)[0])


STAGE_NAMES = {
    1: "开书前决策",
    2: "设定构建",
    3: "开篇",
    4: "连载运营",
    5: "长线管理",
    6: "完本与复盘",
    7: "学习结果应用",
    8: "跨书学习",
}


@dataclass(frozen=True, slots=True)
class LearningQuestionContract:
    output_contract: str
    measurement_requirements: str
    evidence_requirements: str
    scope_requirement: str
    external_data_policy: str


def _contract(
    output: str,
    measurement: str,
    evidence: str,
    scope: str,
    external: str = "仅用书内文本和结构数据即可回答；不得把结构观察写成市场效果因果。",
) -> LearningQuestionContract:
    return LearningQuestionContract(output, measurement, evidence, scope, external)


# 逐问合同恢复自 Fable 的北极星 66 问底稿。这里保留的是产出、计数、证据、
# 样本范围与降级要求；用户已经否决的“拆书结果直连写作 Agent”不在合同中。
LEARNING_QUESTION_CONTRACTS = {
    "1.1": _contract(
        "品类必备元素检查表（元素、是否兑现、兑现章节、兑现方式）及每项在本书中的实现案例。",
        "逐项统计已兑现、未兑现和改写数量；记录每项首次兑现章、段落或字数位置。",
        "品类清单必须注明来自专家先验还是跨书交集；每个兑现判断至少附一处带出处原文。",
        "单书全篇；品类清单在同品类至少 3 本形成交集前标为先验候选。",
        "书内只能证明是否兑现及如何兑现；数据或口碑代价必须有平台数据，否则标注‘待读者数据验证’。",
    ),
    "1.2": _contract(
        "品类雷点枚举及本书规避、擦边和化解案例表（位置、手法、原文）。",
        "逐项统计未出现、擦边、明确触发和已化解数量；记录从风险出现到化解的章节或字数距离。",
        "雷点清单必须注明先验或跨书来源；擦边和化解均需前后文证据，不能凭情节摘要定性。",
        "单书全篇；清单与 1.1 使用同一品类、平台和商业模式口径。",
        "实际读者反应与损失需书评、追读或付费数据；缺失时只输出结构风险和化解手法。",
    ),
    "1.3": _contract(
        "套路层与创新层分层对照表，并与同品类作品的一句话卖点做对比。",
        "按题材外衣、核心机制、叙事视角、情绪基调、人物关系逐层标注相同与差异；统计触碰核心预期的项目。",
        "每个差异化判断需同时给出品类基线来源和本书实现原文，不能只依据书名或简介。",
        "单书全篇加同品类对照样本；单书阶段只能给差异候选，不能宣称品类规律。",
        "差异化造成的收益或代价需要同口径外部数据；缺失时不做效果归因。",
    ),
    "1.4": _contract(
        "卖点一句话卡、承诺—兑现追踪表（承诺内容、首次兑现位置、兑现方式）及同类书卖点对比。",
        "分别记录书名、简介和前三章表达的承诺；定位首次兑现章、段落和累计字数，计算承诺到兑现的距离。",
        "一句话卖点必须能由开篇事件和原文支持；首次兑现必须引用兑现现场，不能用全书摘要代替。",
        "书名、简介和前三章为主，必要时回查全篇确认首次兑现；跨书对比按同品类同商业模式分组。",
    ),
    "1.5": _contract(
        "金手指五要素规格卡（能力、条件、代价、成长轨道、爽点类型）、限制触发事件表及同期同类差异对比。",
        "统计每条限制真实触发次数、位置、后果和例外；记录成长节点间隔以及每次产生的爽点类型。",
        "机制条款、真实触发和后果各需原文证据；没有触发记录时不能把设定说明当成已执行规则。",
        "单书全篇；同期同类差异需 2 至 3 本可比作品，单书阶段明确缺少对照。",
    ),
    "2.1": _contract(
        "角色出场序表（顺序、章节、首场功能、后续戏份量级）和跨书开篇阵容对比。",
        "由程序分别统计前 3、10、30 章中实际参与有效行动的去重具名人物；同时记录新增人物间隔和首场功能分布。",
        "计数必须落到事件或行动证据，人物名单和单纯被提及不算；身份未裁定造成的重复计数必须单列。",
        "单书至少覆盖前 30 章；不足 30 章按实际篇幅标为部分回答，跨书结论另行汇总。",
    ),
    "2.2": _contract(
        "主角双层欲望卡、最小完整集交代节奏表（要素、首次展示章节、展示事件）和弧光转折时间轴。",
        "分别定位表层欲望、深层欲望、动机、性格反差、行为底线和核心能力的首次事件；统计两层欲望发生冲突的节点。",
        "每个‘立住’判断必须引用主角通过行动或选择表现该要素的原文；人物简介或模型标签不能单独作证。",
        "重点覆盖前三章、前 30 章和全书转折点；只看到早期片段时不得推断完整人物弧光。",
    ),
    "2.3": _contract(
        "配角功能分工矩阵（角色、功能、活跃卷）以及功能重叠和空缺标注。",
        "统计常驻配角数量、每种叙事功能人数、兼任与重叠次数、各卷活跃范围。",
        "功能必须由角色实际参与的事件和结果证明；性格描述不能替代叙事功能证据。",
        "单书全篇并按卷观察；跨书阵容基线另行比较。",
    ),
    "2.4": _contract(
        "反派梯队表（层级、登退场位置、与主角实力差、铺垫起点）和主要反派动机卡。",
        "统计每层反派的供压时长、消耗间隔、首次铺垫距离；记录是否存在独立事业线及其推进节点。",
        "反派目标、实力差、压力升级和退场均需事件证据；不能把与主角冲突的所有人物自动归为反派。",
        "单书全篇，至少覆盖开篇第一梯队和最终对手的铺垫链。",
    ),
    "2.6": _contract(
        "世界观信息释放曲线、设定引用计数与依赖图、揭示时间表、必锁定/可延迟清单和开篇露出比。",
        "逐章统计前 30 章新增设定；记录核心设定首提、部分揭示、完整揭示和后文引用次数。",
        "设定出现、引用和依赖均需对应剧情载体与原文；未被正文使用的设定不能凭推测补入。",
        "前 30 章用于开篇边界，全书用于确认长线引用与完整揭示。",
    ),
    "2.7": _contract(
        "设定揭示案例库（设定项、载体类型、当时主角动机、原文位置）及说明书式灌输反例。",
        "统计各载体类型使用次数和占比；记录揭示发生时主角是否具有明确求知或行动动机。",
        "每个案例必须附揭示现场原文和上下文；摘要不能证明揭示方式。",
        "单书全篇，重大设定至少覆盖首次与完整揭示。",
    ),
    "2.8": _contract(
        "终极悬念分段揭示图（段、位置、揭示内容、同时新抛问题）。",
        "统计揭示段数、相邻揭示的章节与字数间隔、每次是否补入更大谜团。",
        "埋设、阶段揭示和最终回应分别附原文；只有回收处而无早期证据时不能倒造铺垫。",
        "单书全篇；未完本或悬念未回收时明确开放状态。",
    ),
    "2.9": _contract(
        "势力档案、圈层详略图、带时间维度的关系矩阵、分卷关系快照和主角棋盘位置轨迹。",
        "统计各圈层首次提及与首次详写位置、关系重组次数及触发事件、主角位置升级节点。",
        "势力目标、资源、关系和变化均需原文或事件证据；静态名单不能证明动态关系。",
        "单书全篇并按卷生成快照。",
    ),
    "2.10": _contract(
        "力量体系阶梯表（层级、标志能力、获取条件）和规则—代价条款清单。",
        "统计明确层级数、升级条件、规则和代价；记录阶梯在全书首次使用与最终覆盖位置。",
        "每一级、条件和代价均需设定或实际剧情证据；设定未执行时必须标为仅声明。",
        "单书全篇；未展示的未来层级不得由模型补齐。",
    ),
    "2.11": _contract(
        "预谋/即兴判定清单、后补设定软着陆案例库，以及锁死层、生长层和模糊带分层清单。",
        "统计跨长距离呼应、前文零铺垫后引入、前后微调和软着陆手法；记录对应章节与跨度。",
        "‘开书前锁定’只能作为作者决策推断，必须给早期铺垫与后期回应的双端证据和限制说明。",
        "单书全篇，必须跨越早中后阶段；短片段不能回答。",
    ),
    "2.12": _contract(
        "分卷任务表（卷号、字数、任务、对手、卷末状态）、卷结构参数和主线不可逆节点时间轴。",
        "统计卷数、卷长、节点数、节点间章节与字数间隔，并标记每卷任务升级关系。",
        "卷边界和任务需由章节结构及事件链证明；自动分段与原书卷标记必须区分。",
        "单书全篇；卷参数分布和标准区间只有跨书时才能形成。",
        "单书可完整产出自身卷表；‘标准区间’需同品类同商业模式跨书样本。",
    ),
    "3.1": _contract(
        "首章开场卡（场景类型、主角出场段落、初始麻烦），以及按品类的开局类型统计和原文案例。",
        "定位主角首次出场段落和累计字数；统计开场类型、第一句/段的功能。",
        "必须引用首章开头、主角出场和初始麻烦现场，不能用全书总览代替。",
        "单书首章；品类分布需多书对照。",
    ),
    "3.2": _contract(
        "黄金三章逐章任务拆解、段落功能序列、关键事件字数定位和信息装载时间轴。",
        "逐章统计各信息模块首次位置和字数占比，特别记录核心能力首次亮相位置。",
        "每项任务和信息模块必须回到前三章对应段落；章节摘要只能导航。",
        "严格覆盖第 1 至 3 章；标准节奏区间需同类多书对比。",
    ),
    "3.3": _contract(
        "首个爽点定位卡（位置、类型、铺垫来源、铺垫距离）及跨书首爽字数对比。",
        "记录爽点章、段、累计字数和铺垫起点，计算两者字数距离。",
        "铺垫与释放必须各附原文，并说明情绪期待如何被兑现；普通行动成功不能自动算爽点。",
        "从正文开头追踪到第一个可确认爽点；跨书分布另行生成。",
    ),
    "3.4": _contract(
        "前三章章末钩清单（类型、指向问题、兑现位置、赊账距离）。",
        "逐章记录最后有效段落、钩子类型、回应章和章节/字数距离。",
        "每章必须引用真实章末原文和后续回应原文；剧情阶段的 next_hook 不能冒充章末钩。",
        "严格覆盖前三章及其后续回应位置。",
    ),
    "4.1": _contract(
        "事件单元结构模板、全书实例清单（位置、时长、变体）和单元衔接方式清单。",
        "统计单元长度、内部阶段长度、复用次数和变体位置；计算相邻单元衔接间隔。",
        "模板必须由多个完整事件链实例归纳，每个阶段有事件和原文证据；单个案例不能称为复用模板。",
        "单书全篇；至少两个相似完整单元才能形成单书候选。",
    ),
    "4.2": _contract(
        "卷末高潮解剖图和全书高潮分布图，包含收线、伏笔调用、波次、持续时长与结算段。",
        "统计收线起点、高潮持续章数、波次数、大高潮间隔和结算过渡长度。",
        "高潮起止、每波转折、调用线索和卷末状态均需事件及原文证据。",
        "单书全篇并按卷分析；无可靠卷边界时明确限制。",
    ),
    "4.4": _contract(
        "逐章主/支线标注序列、分卷配比曲线、支线插入与回勾统计，以及最长主线零推进区间。",
        "按章统计主线、支线、混合占比；记录支线连续时长、回勾方式、离题距离和零推进补偿。",
        "每章分类需由事件目标与结果证明；回勾必须引用支线元素重新影响主线的事件。",
        "单书全篇并按卷输出。",
    ),
    "4.5": _contract(
        "逐爽点标注表、类型占比、主力类型变体案例和同类型强度升级阶梯。",
        "统计各类型次数与占比、连续同类上限、变体方式和强度变化。",
        "每个爽点需铺垫与释放证据；强度是有明示口径的相对分级，不得伪造读者感受。",
        "单书全篇并按卷、开中后期比较。",
    ),
    "4.6": _contract(
        "逐章爽点标注、分卷密度曲线、开中后期参数对比和命名节奏约束候选。",
        "分别计算每章、每万字的小中大爽点密度，低谷持续长度和大高潮间隔。",
        "每个计数点必须回到 4.5 的可核查爽点证据；曲线不能由剧情摘要估算。",
        "单书全篇；跨书公共区间必须按同品类同商业模式叠加。",
    ),
    "4.7": _contract(
        "压抑—释放配对表和铺垫时长分布，包含压抑手段、翻盘与放大技巧。",
        "统计每对起止、章数和字数时长、全书平均与最长压抑段。",
        "压抑起点与释放现场需成对原文；不能只凭负面情节长度推断读者憋屈。",
        "单书全篇；‘安全上限’需跨书与读者数据校验。",
        "无追读数据时只列时长排行和异常候选，效果标注‘待读者数据验证’。",
    ),
    "4.9": _contract(
        "章末钩类型表、逐章标注统计、类型轮换与强弱节律、无钩章分析，并为每种类型提供带出处原文实例。",
        "正式答案逐章统计类型数量与占比、连续强钩、同类钩上限、类型轮换次数和无钩比例；不统计后续回应距离。",
        "必须保存每章最后有效段落；剧情阶段悬念、普通场景分析和后续回应证据都不能替代章末证据。",
        "正式模式按连续窗口覆盖全书并精确合并相邻节律；快速预览只能使用预注册连续块并明确标为估算，不能冒充正式答案。",
    ),
    "4.10": _contract(
        "悬念账本（埋设、兑现、跨度、档位）和悬念存量随章节变化曲线。",
        "统计短中长悬念数量、跨度、任一章节未结数量、兑现后补入新悬念的间隔和真空期。",
        "每条悬念的提出和回应都需原文；开放悬念不能被强行判定为伏笔或已兑现。",
        "单书全篇；未完本作品明确截止章节。",
    ),
    "4.12": _contract(
        "章型分类、各章型段落功能模板和字数、场景、切换粒度、对话/动作/叙述/心理比例统计。",
        "按章型计算样本量、各段功能顺序、比例区间和场景数分布。",
        "章型与段落功能必须来自原文章节级标注；少量示例不能直接外推全书模板。",
        "按章型分层抽样并覆盖开中后及不同卷；每类报告样本量。",
    ),
    "5.1": _contract(
        "战力曲线、主角—对手战力差曲线、越级幅度与手段统计、防无敌手法清单。",
        "统计升级间隔、压制期、越级幅度和各防膨胀手法启用位置。",
        "等级、战斗结果和越级依据需设定与事件双重证据；没有统一等级时说明替代测量口径。",
        "单书全篇并分开篇、中期、后期；跨书曲线另行对比。",
        "口碑滑坡需外部数据；缺失时只标战力曲线异常候选。",
    ),
    "5.2": _contract(
        "规则—代价执行表和一致性校验表（条款、硬软分级、全书遵守记录）。",
        "逐条统计执行、破例、圆回次数及位置；记录代价是否真正影响人物选择或结果。",
        "规则声明、执行、破例和修正分别提供原文；被声明但从未触发的条款单列。",
        "单书全篇。",
    ),
    "5.3": _contract(
        "伏笔三点账本（埋点、补提、回收）、悬置期分布、并发未回收曲线、明暗比例及伪装/提醒手法清单。",
        "统计长线伏笔数量、每条补提间隔、回收距离、任一章节开放数量和明暗比例。",
        "同一伏笔必须保存埋设、保温和回收各阶段原文；只有回收或只有摘要时不得补造完整生命周期。",
        "单书全篇；长线定义按实际篇幅报告，同时单列超过 50 万字的伏笔。",
    ),
    "5.4": _contract(
        "吃书案例前后原文对照、易崩事实类型排行、圆场手法清单和事实状态字段清单。",
        "统计矛盾、修正和未解决次数，按战力、规则、时间、知情面等类型分布。",
        "矛盾双方和后续圆场分别引用原文；人物误解、传闻和设定真改必须区分。",
        "单书全篇，按事实版本链回放。",
    ),
    "6.4": _contract(
        "作者决策推断清单（可能的前置决定、建议深度、使用证据、可能的后期生长项），汇总第一、二阶段结论。",
        "统计各候选决定在前 3、10、30 章的实际使用和后文依赖；记录早期铺垫到后期回应跨度。",
        "每项都必须以成书结构反推并说明不确定性；这是参考书作者决策推断，不是用户新书的最小开书包。",
        "单书全篇，且依赖 1.x 与 2.x 的专项结果就绪后才能结算。",
    ),
    "6.5": _contract(
        "结构性可参考、风格性难复制、环境性不可复制三栏归因表，包含证据、适用条件、风险和候选权重。",
        "逐项统计支持证据、反证和适用范围；权重必须说明计算或判断口径。",
        "结构项需书内证据；风格项需原文样本；平台推荐、题材红利和成功因果不能由文本推断。",
        "单书只能产出‘待跨书验证的方法候选’，不能冒充品类规律。",
        "环境因素和成功因果需要平台、时间与对照数据；缺失时列待核项，不分配伪权重。",
    ),
    "7.1": _contract(
        "学习结论—用户创作决策用途映射表，说明每条结论能帮助用户判断什么以及不能替用户决定什么。",
        "统计各类创作决策获得的结论数、证据充分度和待补缺口；一条结论可映射多个用途但需说明主用途。",
        "映射必须基于已完成学习答案及其证据，不能把旧书结论自动写成用户新书设定。",
        "当前单书学习报告；用户主动选择后才可作为新书共创参考。",
    ),
    "7.2": _contract(
        "每条学习结论的通俗解释、证据、适用边界和可收藏参考卡完整性检查。",
        "统计完整、部分完整和不可收藏条目；逐项检查解释、计数、原文、限制、可参考与不可照搬字段。",
        "参考卡只能引用对应学习答案已核验的证据，不能新增未经分析的结论。",
        "覆盖当前已生成的全部学习答案，不把未生成问题计为通过。",
    ),
    "7.3": _contract(
        "用户主动选择的参考方法清单、抽象后的可借鉴机制、原作专有内容排除项和带入共创时的讨论问题。",
        "记录选择、跳过和待定数量；逐项检查是否去除原作人物、设定名、专有表达和无适用条件的表层形式。",
        "必须保留学习答案来源和用户选择记录；系统不得自动把拆书结果写入新书设定。",
        "只有用户作出选择后生成；属于拆书学习到新书共创之间的人工确认边界。",
    ),
    "7.4": _contract(
        "节奏指标定义（测量方法、单位、边界）和样本书逐章多指标曲线。",
        "至少定义冲突密度、新信息量、爽点兑现密度、情绪变化和悬念张力，并逐章计算。",
        "每个指标必须能回到可重复的章节标注和原文证据；‘节奏好’等形容词不算指标。",
        "单书全篇；跨书比较必须使用同一指标版本。",
    ),
    "7.5": _contract(
        "品类套路阶段状态机（阶段、进入条件、退出条件、必备事件）和样本书实例；跨书后再给变体对比。",
        "统计每个阶段在样本书中的位置、时长、转移次数和缺失的必备事件。",
        "状态与转移必须由事件链证据支持；单书归纳只能称为候选状态机。",
        "单书可给候选实例；品类状态机需同品类多书验证。",
    ),
    "8.1": _contract(
        "分级套路库、候选晋升规则和品类必备元素交集统计。",
        "记录每条方法支持书目数、样本量、品类与商业模式；至少 3 本成功书重复出现才进入已验证层。",
        "每本支持书必须保留对应单书答案和原文证据；相似标题或模型常识不能代替跨书复核。",
        "跨书任务；单书只进入待验证候选层。",
        "‘成功书’筛选需要可核查的平台与成绩口径；没有统一口径时只报告样本来源，不做成功因果。",
    ),
    "8.2": _contract(
        "按商业模式分层的跨书参数表，共性参数给区间，发散参数标为个人风格候选。",
        "比较爽点密度、升级间隔、单元时长、主支线比和悬念配比等同口径参数，并报告样本量、分布和离群值。",
        "每个参数必须追溯到同版本单书计数合同；付费与免费作品不得混层。",
        "跨书任务；同品类同商业模式至少多本，样本不足 3 本时只给初步候选。",
        "需要每本书的平台与商业模式元数据；缺失时不得进入共性区间计算。",
    ),
}


def _q(question_id: str, question: str, requirement: str, evidence_view: str) -> LearningQuestionDefinition:
    contract = LEARNING_QUESTION_CONTRACTS[question_id]
    return LearningQuestionDefinition(
        question_id,
        question,
        requirement,
        evidence_view,
        contract.output_contract,
        contract.measurement_requirements,
        contract.evidence_requirements,
        contract.scope_requirement,
        contract.external_data_policy,
    )


LEARNING_QUESTION_CATALOG = (
    _q("1.1", "品类“必给预期”清单；本书逐条兑现在第几章、怎么兑现？", "品类预期与章节兑现定位", "plot"),
    _q("1.2", "品类雷点禁忌清单；本书哪里擦边、怎么化解？", "品类禁忌、风险情节与化解方式分析", "plot"),
    _q("1.3", "差异化落在哪一层，动没动品类核心预期？", "品类基线与本书差异化对照", "overview"),
    _q("1.4", "卖点能否一句话概括、第几章第一次兑现？", "故事卖点归纳与首次兑现定位", "overview"),
    _q("1.5", "金手指五要素规格；限制条款被真实触发过几次？", "核心能力规格、限制和触发统计", "world"),
    _q("2.1", "前 3/10/30 章实际登场并起作用的人物各几个？", "人物登场、有效行动与分段计数", "characters"),
    _q("2.2", "主角双层欲望与最小完整集各在第几章立住？", "主角欲望、能力、缺陷和立住章节分析", "characters"),
    _q("2.3", "常驻配角按什么功能编制，有无重叠或空缺？", "配角叙事功能与角色编制分析", "characters"),
    _q("2.4", "反派梯队怎么供压？反派有没有自己的事业？", "反派层级、目标与压力供给分析", "characters"),
    _q("2.6", "哪些设定必须开书前锁定，世界观预写边界在哪？", "设定依赖与首次使用顺序倒推", "world"),
    _q("2.7", "设定揭示用什么剧情载体，才不写成说明书？", "设定揭示场景与剧情载体分析", "pacing"),
    _q("2.8", "世界级终极悬念切几段、各隔多少字兑现？", "长线悬念分段、距离与兑现统计", "foreshadowing"),
    _q("2.9", "势力圈层怎么详略、关系怎么重组、主角怎么爬？", "势力关系、圈层变化与主角路径分析", "relations"),
    _q("2.10", "力量体系阶梯够不够长？硬规则与代价是什么？", "力量层级、硬规则和代价执行分析", "world"),
    _q("2.11", "哪些设定是开书前锁死的、哪些边写边长出来的？", "设定出现顺序与前置依赖倒推", "world"),
    _q("2.12", "全书几卷？卷级参数与主线节点密度是多少？", "卷级边界、篇幅与主线节点统计", "plot"),
    _q("3.1", "开场第一幕什么类型，主角怎么登场？", "开场场景功能与主角登场方式分析", "pacing"),
    _q("3.2", "前三章逐章任务与信息装载顺序是什么？", "前三章场景任务和信息释放分析", "pacing"),
    _q("3.3", "第一个爽点在第几章第几段、铺垫距离多少字？", "爽点识别、原文定位与距离统计", "pacing"),
    _q("3.4", "前三章章末钩是什么、多快兑现？", "章末钩识别与兑现距离统计", "pacing"),
    _q("4.1", "有没有可复用的事件单元模板？复用了几次？", "事件单元聚类与复用次数统计", "events"),
    _q("4.2", "卷末大高潮的收束链与波次结构怎么运作？", "卷末事件链、冲突波次与收束分析", "conflicts"),
    _q("4.4", "主支线配比、插入位置与回勾规律是什么？", "主支线分类、篇幅和回勾位置统计", "plot"),
    _q("4.5", "爽点类型怎么配比？重复时怎么对抗边际递减？", "爽点分类、配比与变体分析", "pacing"),
    _q("4.6", "爽点密度曲线什么形状、分阶段怎么变？", "爽点逐章标注与阶段密度统计", "pacing"),
    _q("4.7", "压抑—释放间距与“憋屈上限”是多少？", "压抑和释放配对及间距统计", "pacing"),
    _q("4.9", "章末钩类型配比与强弱节律是什么？", "章末钩逐章分类、强度和节律统计", "pacing"),
    _q("4.10", "悬念的埋设、兑现和存量账本怎么管理？", "悬念生命周期与存量变化统计", "foreshadowing"),
    _q("4.12", "单章内部的微结构模板是什么？", "单章场景序列聚类与模板分析", "pacing"),
    _q("5.1", "战力曲线全景与防膨胀手法是什么？", "能力状态、对手层级与战力变化分析", "states"),
    _q("5.2", "规则与代价被真实执行过吗？硬软怎么区分？", "规则触发、代价执行与例外统计", "world"),
    _q("5.3", "长线伏笔怎么埋、怎么保温、怎么引爆？", "伏笔生命周期、强化和回收分析", "foreshadowing"),
    _q("5.4", "哪类设定最容易吃书？作者怎么圆？", "事实版本冲突、修正与补丁分析", "facts"),
    _q("6.4", "从成书倒推：作者开书前可能锁定了哪些关键决策？这些只作为学习参考，不等于用户新书的最小开书包。", "跨结构证据倒推作者前置决策", "overview"),
    _q("6.5", "本书成功哪些可复制、哪些不可复制？", "方法机制、适用条件与不可复制条件区分", "claims"),
    _q("7.1", "每条学习结论会帮助用户做哪一种新书决策？", "学习结论与创作决策用途映射", "claims"),
    _q("7.2", "每条学习结论是否同时提供通俗解释、证据、适用边界和可收藏的参考卡？", "学习答案完整性与可收藏性检查", "claims"),
    _q("7.3", "用户选择哪些参考方法进入新书共创，如何防止照搬原作？", "用户主动选择、抽象改造和防照搬边界", "claims"),
    _q("7.4", "“节奏”用什么可测指标定义？", "节奏指标定义与逐章统计", "pacing"),
    _q("7.5", "品类套路能否表达为可供用户理解和改造的阶段状态机？", "套路阶段、转移条件与变体分析", "plot"),
    _q("8.1", "哪些结论跨书验证过、可进已验证套路库？", "跨书同类方法比较与样本量记录", "claims"),
    _q("8.2", "哪些参数是品类共性、哪些是个人风格？", "跨书参数分布与作者差异比较", "claims"),
)

INITIAL_INCREMENTAL_QUESTION_IDS = ("1.4", "2.1", "2.2", "3.4", "4.9")
_RECOMMENDED_RANK = {
    question_id: rank
    for rank, question_id in enumerate(INITIAL_INCREMENTAL_QUESTION_IDS, start=1)
}
_QUESTION_BY_ID = {item.question_id: item for item in LEARNING_QUESTION_CATALOG}

if len(LEARNING_QUESTION_CATALOG) != 42 or len(_QUESTION_BY_ID) != 42:
    raise RuntimeError("LEARNING_QUESTION_CATALOG_MUST_CONTAIN_42_UNIQUE_QUESTIONS")
if set(LEARNING_QUESTION_CONTRACTS) != set(_QUESTION_BY_ID):
    raise RuntimeError("EVERY_LEARNING_QUESTION_MUST_HAVE_ONE_FABLE_OUTPUT_CONTRACT")


@dataclass(frozen=True, slots=True)
class LearningContractItemDefinition:
    item_id: str
    label: str
    requirement: str


# 每一问在进入正式实现时，都要把 Fable 的自然语言合同拆成可逐项验收的
# 结果项目。2.1 是首个完成该闭环的问题；其余问题不能因为尚未拆项而冒充
# 已通过同等级验收。
LEARNING_QUESTION_ITEM_CONTRACTS: dict[
    str, tuple[LearningContractItemDefinition, ...]
] = {
    "1.4": (
        LearningContractItemDefinition(
            "selling_point_card",
            "一句话卖点卡",
            "用一句话同时说明主角起点、核心异常或机制、以及它把主角推入的主要矛盾，并引用开篇原文。",
        ),
        LearningContractItemDefinition(
            "opening_promise_sources",
            "开篇承诺来源",
            "分别说明当前材料能确认的书名、简介、故事前提和前三章承诺；以源文件前置内容为准，缺失来源必须明确标出。",
        ),
        LearningContractItemDefinition(
            "first_payoff_location",
            "首次兑现位置与方式",
            "按开篇事件时序排除更早候选，定位首次兑现章、章节标题、段落、源文件累计字符、兑现事件和原文，并计算承诺到兑现的章距与字符距离；不能用简介或全书摘要代替兑现现场。",
        ),
        LearningContractItemDefinition(
            "cross_book_comparison",
            "同类书卖点对比",
            "只有同品类、同商业模式多书使用同一口径后才能回答；当前缺少对照时必须标为证据不足。",
        ),
    ),
    "2.1": (
        LearningContractItemDefinition(
            "opening_character_counts",
            "前 3/10/30 章有效行动人物数",
            "使用程序计数，不得由模型重新数人物名单。",
        ),
        LearningContractItemDefinition(
            "character_appearance_sequence",
            "角色首行动出场序",
            "覆盖前 30 章每位有效行动人物的首次行动章节与事件。",
        ),
        LearningContractItemDefinition(
            "first_scene_functions",
            "人物首场功能",
            "逐人判断首次行动场景承担的叙事功能，并引用该事件原文。",
        ),
        LearningContractItemDefinition(
            "later_role_volume",
            "后续戏份量级",
            "按前 30 章参与有效行动事件数，由程序分档。",
        ),
        LearningContractItemDefinition(
            "new_character_intervals",
            "新增人物间隔",
            "按首次行动章节计算相邻引入批次的章节间隔。",
        ),
        LearningContractItemDefinition(
            "first_function_distribution",
            "首场功能分布",
            "由逐人首场功能分类汇总，不得凭印象估算。",
        ),
        LearningContractItemDefinition(
            "identity_duplicate_risks",
            "身份重复计数风险",
            "单列仍未裁定是否同一人的名字候选；没有候选也要明确记零。",
        ),
        LearningContractItemDefinition(
            "cross_book_comparison",
            "同品类开篇阵容对比",
            "只有同品类、同商业模式多书使用同一口径后才能回答。",
        ),
    ),
    "2.2": (
        LearningContractItemDefinition(
            "surface_desire",
            "表层欲望",
            "给出表层欲望、首次由行动或选择展示的章节和事件，并引用该现场原文。",
        ),
        LearningContractItemDefinition(
            "deep_desire",
            "深层欲望",
            "给出深层欲望、首次由行动或选择展示的章节和事件，并引用该现场原文。",
        ),
        LearningContractItemDefinition(
            "motivation",
            "核心动机",
            "给出推动主角关键选择的核心动机、首次展示章节和事件，并引用该现场原文。",
        ),
        LearningContractItemDefinition(
            "contrast",
            "性格反差",
            "给出稳定性格反差、首次通过行动展示的章节和事件，并引用该现场原文。",
        ),
        LearningContractItemDefinition(
            "boundary",
            "行为底线",
            "给出主角不能接受或必然越线的底线、首次由选择展示的章节和事件，并引用该现场原文。",
        ),
        LearningContractItemDefinition(
            "core_ability",
            "核心能力",
            "给出核心能力、首次真实使用的章节和事件，并引用该现场原文；设定说明不能替代真实使用。",
        ),
        LearningContractItemDefinition(
            "desire_conflicts",
            "双层欲望冲突节点",
            "统计表层与深层欲望发生冲突的节点，并说明主角的选择与弧光变化。",
        ),
        LearningContractItemDefinition(
            "arc_timeline",
            "弧光转折时间轴",
            "按章节顺序汇总已核验冲突节点形成的转折；当前来源未覆盖后续作品时必须限定范围。",
        ),
    ),
    "4.9": (
        LearningContractItemDefinition(
            "chapter_coverage",
            "全书逐章覆盖",
            "说明正式账本覆盖的章节数、窗口数和是否连续不重不漏。",
        ),
        LearningContractItemDefinition(
            "type_distribution",
            "钩子类型配比",
            "按程序账本给出每种章末钩的数量与占比，不能由剧情阶段或场景摘要估算。",
        ),
        LearningContractItemDefinition(
            "strength_rhythm",
            "强弱节律",
            "给出强、中、轻、无钩分布和最长连续强钩章数。",
        ),
        LearningContractItemDefinition(
            "type_rotation",
            "类型轮换",
            "给出相邻章节类型切换次数和同类钩连续上限，并说明可观察的轮换方式。",
        ),
        LearningContractItemDefinition(
            "no_hook_analysis",
            "无钩章分析",
            "给出无钩章数量与比例，并只分析书内结构位置，不推断读者流失或留存。",
        ),
        LearningContractItemDefinition(
            "representative_examples",
            "各类型原文实例",
            "每种实际出现的钩子类型至少引用一章最后有效段落；不得引用后续回应冒充章末证据。",
        ),
        LearningContractItemDefinition(
            "scope_boundary",
            "与 3.4、4.10 的职责边界",
            "明确 4.9 不追踪逐章后续回应；前三章首次回应属于 3.4，重要悬念生命周期属于 4.10。",
        ),
    ),
}

# 2.1 的八项最终合同仍全部保留，但模型只负责其中的语义分类。
# 其余七项由程序账本确定性编译，不能要求模型重复抄回程序已有事实。
LEARNING_QUESTION_MODEL_ITEM_CONTRACTS: dict[
    str, tuple[LearningContractItemDefinition, ...]
] = {
    "2.1": tuple(
        item
        for item in LEARNING_QUESTION_ITEM_CONTRACTS["2.1"]
        if item.item_id == "first_scene_functions"
    ),
}


class LearningMetricProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1, max_length=160)
    value: str = Field(min_length=1, max_length=2000)
    unit: str = Field(default="", max_length=60)
    method: str = Field(min_length=1, max_length=500)
    evidence_ids: list[str] = Field(default_factory=list, max_length=16)


class LearningContractClassificationProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sequence_no: int = Field(ge=1, le=10_000)
    category: Literal[
        "主角锚点",
        "关系锚点",
        "引路或导师",
        "盟友或协作者",
        "对手或竞争者",
        "威胁或施压者",
        "权威或规则执行者",
        "信息提供者",
        "世界展示载体",
        "调剂或喜剧功能",
        "背景行动者",
        "其他",
    ]


class LearningPayoffClassificationProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sequence_no: int = Field(ge=1)
    matched_facet_ids: list[Literal["F1", "F2", "F3"]] = Field(
        default_factory=list,
        max_length=3,
    )
    exclusion_code: Literal[
        "NONE",
        "PROMISE_SOURCE",
        "PROMISE_RESTATEMENT",
        "IDENTITY_GRANT",
        "DREAM_OR_HISTORY",
        "STATIC_EVIDENCE",
        "SIMULATION",
        "PARTIAL_ONLY",
        "UNRELATED",
    ]
    anchor_evidence_no: int = Field(ge=1, le=16)


class LearningContractItemProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_id: str = Field(min_length=1, max_length=80)
    status: Literal["SUPPORTED", "INSUFFICIENT_EVIDENCE"]
    finding: str = Field(min_length=1, max_length=800)
    metrics: list[LearningMetricProposal] = Field(default_factory=list, max_length=20)
    evidence_ids: list[str] = Field(default_factory=list, max_length=32)
    limitations: list[str] = Field(default_factory=list, max_length=8)
    classifications: list[LearningContractClassificationProposal] = Field(
        default_factory=list,
        max_length=200,
    )
    payoff_classifications: list[LearningPayoffClassificationProposal] = Field(
        default_factory=list,
        max_length=OPENING_PAYOFF_MAX_WINDOW_CANDIDATES,
    )


class LearningAnswerProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_id: str = Field(pattern=r"^[1-8]\.[0-9]+$")
    status: Literal["ANSWERED", "PARTIAL", "INSUFFICIENT_EVIDENCE"]
    conclusion: str = Field(min_length=1, max_length=1800)
    metrics: list[LearningMetricProposal] = Field(default_factory=list, max_length=20)
    evidence_ids: list[str] = Field(default_factory=list, max_length=24)
    counter_evidence_ids: list[str] = Field(default_factory=list, max_length=16)
    limitations: list[str] = Field(min_length=1, max_length=10)
    reusable_lessons: list[str] = Field(default_factory=list, max_length=8)
    do_not_copy: list[str] = Field(min_length=1, max_length=8)
    contract_items: list[LearningContractItemProposal] = Field(
        default_factory=list,
        max_length=16,
    )


class AuthorDecisionProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=180)
    likely_timing: Literal["BEFORE_WRITING", "EARLY_SERIALIZATION", "LATER_GROWTH", "UNKNOWN"]
    inference: str = Field(min_length=1, max_length=1200)
    evidence_ids: list[str] = Field(min_length=1, max_length=20)
    limitations: list[str] = Field(min_length=1, max_length=8)
    confidence: int = Field(ge=0, le=100)


class MethodCandidateProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=180)
    mechanism: str = Field(min_length=1, max_length=1200)
    observed_result: str = Field(min_length=1, max_length=1000)
    applicability: list[str] = Field(min_length=1, max_length=8)
    risks: list[str] = Field(min_length=1, max_length=8)
    evidence_ids: list[str] = Field(min_length=1, max_length=20)
    do_not_copy: str = Field(min_length=1, max_length=800)
    verification_scope: Literal["SINGLE_BOOK_PENDING"] = "SINGLE_BOOK_PENDING"


class LearningReportOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answers: list[LearningAnswerProposal] = Field(min_length=1, max_length=5)
    author_decisions: list[AuthorDecisionProposal] = Field(default_factory=list, max_length=20)
    method_candidates: list[MethodCandidateProposal] = Field(default_factory=list, max_length=20)


@dataclass(frozen=True, slots=True)
class PersistedLearningReport:
    report_id: str


class LearningReportValidationError(ValueError):
    def __init__(self, code: str, errors: list[dict[str, Any]]) -> None:
        super().__init__(code)
        self.code = code
        self.errors = errors


def _validation_errors(error: ValidationError) -> list[dict[str, Any]]:
    return [
        {
            "path": list(item.get("loc", ())),
            "type": str(item.get("type") or "value_error"),
            "message": str(item.get("msg") or "字段不符合要求"),
        }
        for item in error.errors()
    ]


def _inline_model_schema(model: type[BaseModel]) -> dict:
    raw = model.model_json_schema()
    definitions = raw.get("$defs", {})

    def expand(value: object) -> object:
        if isinstance(value, dict):
            reference = value.get("$ref")
            if isinstance(reference, str) and reference.startswith("#/$defs/"):
                return expand(definitions.get(reference.rsplit("/", 1)[-1], {}))
            return {key: expand(item) for key, item in value.items() if key not in {"$defs", "$ref"}}
        if isinstance(value, list):
            return [expand(item) for item in value]
        return value

    return expand(raw)  # type: ignore[return-value]


def _prompt() -> str:
    path = Path(__file__).resolve().parents[3] / "prompts" / "learning_report_v1.md"
    return path.read_text(encoding="utf-8").strip()


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


def _balanced_items(items: list[dict], chapter_key: str) -> list[dict]:
    """Order chapter material so every prefix is spread across the book."""
    ordered = sorted(items, key=lambda item: int(item.get(chapter_key) or 0))
    if len(ordered) <= 2:
        return ordered
    remaining = list(range(len(ordered)))
    selected_indexes: list[int] = []
    while remaining:
        if not selected_indexes:
            chosen = remaining[0]
        elif len(selected_indexes) == 1:
            chosen = remaining[-1]
        else:
            chosen = max(
                remaining,
                key=lambda index: min(abs(index - selected) for selected in selected_indexes),
            )
        selected_indexes.append(chosen)
        remaining.remove(chosen)
    return [ordered[index] for index in selected_indexes]


def _request_budget(profile: Any) -> dict[str, int | float | str]:
    output_reserve = max(1, int(getattr(profile, "max_output_tokens", 16_000)))
    context_window = getattr(profile, "context_window_tokens", None)
    if context_window is not None:
        available_tokens = max(
            8_000,
            int(context_window) - output_reserve - 4_096,
        )
        input_tokens = min(
            LEARNING_ANSWER_DEFAULT_SOFT_INPUT_CAP_TOKENS,
            available_tokens,
        )
        source = "MODEL_CONTEXT_WITH_DEFAULT_SOFT_CAP"
    else:
        input_tokens = LEARNING_ANSWER_DEFAULT_SOFT_INPUT_CAP_TOKENS
        source = "DEFAULT_SOFT_CAP_WITHOUT_REPORTED_CONTEXT"
    return {
        "default_soft_input_cap_tokens": (
            LEARNING_ANSWER_DEFAULT_SOFT_INPUT_CAP_TOKENS
        ),
        "input_token_budget": input_tokens,
        "input_char_budget": int(
            input_tokens * LEARNING_ANSWER_ESTIMATED_CHARS_PER_TOKEN
        ),
        "estimated_chars_per_token": LEARNING_ANSWER_ESTIMATED_CHARS_PER_TOKEN,
        "budget_source": source,
    }


def _chapter_index_by_unit_id(
    session: Session,
    source_version_id: str,
) -> dict[str, dict[str, object]]:
    chapter_units = list(session.scalars(
        select(SourceUnit)
        .where(
            SourceUnit.source_version_id == source_version_id,
            SourceUnit.unit_type == "CHAPTER",
        )
        .order_by(SourceUnit.ordinal)
    ))
    return {
        unit.id: {"ordinal": ordinal, "title": unit.title}
        for ordinal, unit in enumerate(chapter_units, start=1)
    }


def _evidence_records(
    evidence_ids: set[str],
    evidence_by_id: dict[str, EvidenceSpan],
    chapter_by_unit_id: dict[str, dict[str, object]],
) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for evidence_id in sorted(evidence_ids):
        evidence = evidence_by_id.get(evidence_id)
        if evidence is None:
            continue
        chapter = chapter_by_unit_id.get(evidence.source_unit_id, {})
        records.append({
            "id": evidence.id,
            "chapter_ordinal": chapter.get("ordinal"),
            "chapter_title": chapter.get("title"),
            "paragraph_number": evidence.paragraph_index + 1,
            "source_char_start": evidence.start_char + 1,
            "source_char_end": evidence.end_char,
            "text": evidence.text_snapshot,
        })
    return records


_FRONT_MATTER_LABEL = re.compile(
    r"^(?:内容简介|内容提要|作品简介|简介)\s*[:：]?\s*$"
)
_NARRATIVE_HEADING = re.compile(
    r"^(?:序幕|楔子|引子|尾声|后记|"
    r"第[0-9一二三四五六七八九十百千万零〇两]+"
    r"(?:卷|部|篇|幕|章|回|节))"
)
_TITLE_NOISE = re.compile(
    r"^(?:作者|编者|译者|来源|正文前内容)\s*[:：]?"
)


def _opening_promise_source_artifact(
    session: Session,
    settings: Settings,
    version: SourceVersion,
) -> dict[str, object]:
    units = list(session.scalars(
        select(SourceUnit)
        .where(SourceUnit.source_version_id == version.id)
        .order_by(SourceUnit.ordinal)
    ))
    first_chapter = next(
        (unit for unit in units if unit.unit_type == "CHAPTER"),
        None,
    )
    if first_chapter is None:
        return {
            "preferred_title": "",
            "title_candidates": [],
            "description_present": False,
            "description_text": "",
            "description_evidence_ids": [],
            "chapter_numbering_policy": (
                "只对 CHAPTER 类型来源单元按顺序从 1 编号；"
                "PREFACE 等前置内容不计入章节序号。"
            ),
        }
    text = source_text(settings, version)
    excerpt_end = min(
        len(text),
        first_chapter.end_char,
        first_chapter.start_char + 12_000,
    )
    excerpt = text[:excerpt_end]
    line_records: list[tuple[str, int, int]] = []
    cursor = 0
    for raw_line in excerpt.splitlines(keepends=True):
        line = raw_line.strip()
        line_start = cursor
        cursor += len(raw_line)
        line_records.append((line, line_start, cursor))
    if cursor < len(excerpt):
        line_records.append((excerpt[cursor:].strip(), cursor, len(excerpt)))

    description_marker_index = next(
        (
            index
            for index, (line, _start, _end) in enumerate(line_records)
            if _FRONT_MATTER_LABEL.fullmatch(line)
        ),
        None,
    )
    first_narrative_index = next(
        (
            index
            for index, (line, _start, _end) in enumerate(line_records)
            if line and _NARRATIVE_HEADING.match(line)
        ),
        None,
    )
    if description_marker_index is not None:
        title_scan_end = description_marker_index
    elif first_narrative_index is not None:
        title_scan_end = first_narrative_index
    else:
        title_scan_end = min(24, len(line_records))
    title_candidates = [
        line
        for line, _start, _end in line_records[:title_scan_end]
        if (
            1 < len(line) <= 80
            and not _TITLE_NOISE.match(line)
            and not _NARRATIVE_HEADING.match(line)
            and not _FRONT_MATTER_LABEL.fullmatch(line)
            and "验收样本" not in line
        )
    ]
    preferred_title = title_candidates[-1] if title_candidates else ""

    description_lines: list[str] = []
    description_start: int | None = None
    description_end: int | None = None
    if description_marker_index is not None:
        for line, line_start, line_end in line_records[
            description_marker_index + 1:
        ]:
            if not line and not description_lines:
                continue
            if line and _NARRATIVE_HEADING.match(line):
                break
            if line:
                if description_start is None:
                    description_start = line_start + (
                        len(excerpt[line_start:line_end])
                        - len(excerpt[line_start:line_end].lstrip())
                    )
                description_lines.append(line)
                description_end = line_end
            elif description_lines:
                description_lines.append("")
    description_text = "\n".join(description_lines).strip()
    if description_start is not None and description_end is not None:
        description_evidence_ids = list(session.scalars(
            select(EvidenceSpan.id)
            .where(
                EvidenceSpan.source_version_id == version.id,
                EvidenceSpan.start_char < description_end,
                EvidenceSpan.end_char > description_start,
            )
            .order_by(EvidenceSpan.start_char)
        ))
    else:
        description_evidence_ids = []
    front_matter_end = (
        description_end
        if description_end is not None
        else min(excerpt_end, first_chapter.start_char + 2_000)
    )
    return {
        "preferred_title": preferred_title,
        "title_candidates": list(dict.fromkeys(title_candidates)),
        "front_matter_text": text[:front_matter_end].strip(),
        "description_present": bool(description_text),
        "description_text": description_text,
        "description_source_char_start": (
            description_start + 1 if description_start is not None else None
        ),
        "description_source_char_end": description_end,
        "description_evidence_ids": description_evidence_ids,
        "evidence_ids": description_evidence_ids,
        "chapter_numbering_policy": (
            "只对 CHAPTER 类型来源单元按顺序从 1 编号；"
            "PREFACE 等前置内容不计入章节序号。"
        ),
        "promise_chapter_position": 0,
    }


def _opening_payoff_candidate_artifact(
    projection: dict,
    evidence_by_id: dict[str, EvidenceSpan],
    chapter_by_unit_id: dict[str, dict[str, object]],
    opening_promise_sources: dict[str, object],
    *,
    candidate_start_sequence: int = 1,
    candidate_limit: int | None = OPENING_PAYOFF_MAX_WINDOW_CANDIDATES,
) -> dict[str, object]:
    description_evidence_ids = {
        str(evidence_id)
        for evidence_id in opening_promise_sources.get(
            "description_evidence_ids",
            [],
        )
        if evidence_id
    }
    sortable_events: list[tuple[int, int, str, dict, list[EvidenceSpan]]] = []
    for event in projection.get("events", []):
        if not isinstance(event, dict):
            continue
        chapters = [
            int(value)
            for value in event.get("chapter_ordinals", [])
            if int(value) > 0
        ]
        evidence = sorted(
            (
                evidence_by_id[str(evidence_id)]
                for evidence_id in event.get("evidence_ids", [])
                if str(evidence_id) in evidence_by_id
            ),
            key=lambda item: (item.start_char, item.end_char, item.id),
        )
        if not chapters or not evidence:
            continue
        sortable_events.append((
            min(chapters),
            min(item.start_char for item in evidence),
            str(event.get("id") or ""),
            event,
            evidence,
        ))
    sortable_events.sort(key=lambda item: item[:3])
    candidate_start_sequence = max(1, candidate_start_sequence)
    selection_start = candidate_start_sequence - 1
    selection_end = (
        None
        if candidate_limit is None
        else selection_start + max(0, candidate_limit)
    )
    selected_events = sortable_events[selection_start:selection_end]
    candidates: list[dict[str, object]] = []
    for sequence_no, (
        event_chapter,
        _event_start,
        event_id,
        event,
        evidence,
    ) in enumerate(
        selected_events,
        start=candidate_start_sequence,
    ):
        evidence_options: list[dict[str, object]] = []
        for evidence_no, item in enumerate(evidence[:16], start=1):
            chapter = chapter_by_unit_id.get(item.source_unit_id, {})
            evidence_options.append({
                "evidence_no": evidence_no,
                "evidence_id": item.id,
                "chapter_ordinal": int(
                    chapter.get("ordinal") or event_chapter
                ),
                "chapter_title": str(
                    chapter.get("title") or item.source_unit.title
                ),
                "paragraph_number": item.paragraph_index + 1,
                "source_char_start": item.start_char + 1,
                "text": item.text_snapshot[:360],
            })
        event_evidence_ids = {
            str(item.id) for item in evidence
        }
        candidates.append({
            "sequence_no": sequence_no,
            "event_id": event_id,
            "event_title": str(event.get("title") or "未命名事件"),
            "event_summary": str(
                event.get("summary")
                or event.get("process")
                or event.get("outcome")
                or ""
            )[:600],
            "narrative_mode": str(
                event.get("narrative_mode") or "UNKNOWN"
            ),
            "program_exclusion_code": (
                "PROMISE_SOURCE"
                if (
                    event_evidence_ids
                    and event_evidence_ids.issubset(
                        description_evidence_ids
                    )
                )
                else ""
            ),
            "evidence_options": evidence_options,
        })
    return {
        "facet_definitions": [
            {
                "facet_id": "F1",
                "definition": (
                    "一句话卖点中的核心异常、机制或主要矛盾已经在正文中"
                    "作为正在发生的实体、力量或行动出现；简介、口述、"
                    "身份授予、梦境、历史讲述和静态物证不算。"
                ),
            },
            {
                "facet_id": "F2",
                "definition": (
                    "主角在现场直接遭遇该异常、被其作用，或已经被它"
                    "实际推入一句话卖点所说的主要矛盾。"
                ),
            },
            {
                "facet_id": "F3",
                "definition": (
                    "事件发生在正文现实行动层，不是前置简介、梦境、"
                    "历史转述、模拟演示或纯设定说明。"
                ),
            },
        ],
        "classification_policy": (
            "从 sequence_no=1 连续分类；同时命中 F1/F2/F3 且 "
            "exclusion_code=NONE 才是完整兑现。程序选择第一条完整兑现，"
            "并从 anchor_evidence_no 对应原文编译所有位置数字。"
        ),
        "coverage": {
            "source_event_count": len(sortable_events),
            "candidate_count": len(candidates),
            "candidate_start_sequence": candidate_start_sequence,
            "candidate_end_sequence": (
                candidate_start_sequence + len(candidates) - 1
                if candidates
                else candidate_start_sequence - 1
            ),
            "candidate_limit": candidate_limit,
            "source_sequence_contiguous": True,
            "all_source_events_in_window": (
                selection_start == 0
                and len(candidates) == len(sortable_events)
            ),
        },
        "candidates": candidates,
    }


def _material_bundle(
    kind: str,
    item: dict,
    evidence_by_id: dict[str, EvidenceSpan],
    chapter_by_unit_id: dict[str, dict[str, object]],
) -> dict[str, object]:
    return {
        "kind": kind,
        "item": item,
        "evidence": _evidence_records(
            _evidence_ids(item), evidence_by_id, chapter_by_unit_id
        ),
    }


def _source_materials(
    projection: dict,
    question_ids: tuple[str, ...],
) -> list[tuple[int, str, dict]]:
    deep = projection.get("deep_analysis") or {}
    materials: list[tuple[int, str, dict]] = []
    overview = projection.get("story_overview")
    if question_ids != ("2.1",) and isinstance(overview, dict):
        materials.append((116, "story_overview", overview))
    character_design = projection.get("character_design_evidence")
    if (
        "2.2" in question_ids
        and isinstance(character_design, dict)
        and character_design.get("is_current") is not False
    ):
        materials.append((120, "character_design_evidence", character_design))
    chapter_end_hooks = projection.get("chapter_end_hooks_evidence")
    if (
        {"3.4", "4.9"}.intersection(question_ids)
        and isinstance(chapter_end_hooks, dict)
        and chapter_end_hooks.get("is_current") is not False
    ):
        hook_metadata = {
            key: value for key, value in chapter_end_hooks.items() if key != "chapters"
        }
        materials.append((122, "chapter_end_hooks_summary", hook_metadata))
        hook_items = chapter_end_hooks.get("chapters", [])
        if "3.4" in question_ids and "4.9" not in question_ids:
            hook_items = [
                item
                for item in hook_items
                if 1 <= int(item.get("chapter_ordinal") or 0) <= 3
            ]
        for item in _balanced_items(hook_items, "chapter_ordinal"):
            priority = (
                121 if int(item.get("chapter_ordinal") or 0) <= 3 else 118
            )
            materials.append((priority, "chapter_end_hook", item))
    opening_hook_payoffs = projection.get("opening_hook_payoffs_evidence")
    if (
        "3.4" in question_ids
        and isinstance(opening_hook_payoffs, dict)
        and opening_hook_payoffs.get("is_current") is not False
    ):
        payoff_metadata = {
            key: value
            for key, value in opening_hook_payoffs.items()
            if key != "hooks"
        }
        materials.append((126, "opening_hook_payoffs_summary", payoff_metadata))
        for item in opening_hook_payoffs.get("hooks", []):
            materials.append((125, "opening_hook_payoff", item))
    if question_ids == ("1.4",):
        protagonist = str((overview or {}).get("protagonist") or "").strip()
        normalized_protagonist = _normalized_person_name(protagonist)
        for item in projection.get("characters", []):
            names = [
                item.get("name"),
                *item.get("aliases", []),
            ]
            is_named_protagonist = bool(
                normalized_protagonist
                and any(
                    _normalized_person_name(name) == normalized_protagonist
                    for name in names
                )
            )
            if is_named_protagonist:
                materials.append((117, "protagonist", item))
            elif item.get("role") == "PROTAGONIST":
                materials.append((112, "protagonist", item))
        return materials
    if "2.1" in question_ids:
        opening_artifact = _opening_character_program_artifact(projection)
        first_event_ids = {
            str(item.get("first_action_event_id") or "")
            for item in opening_artifact["roles"]
            if isinstance(item, dict)
        }
        for item in projection.get("events", []):
            if str(item.get("id") or "") not in first_event_ids:
                continue
            materials.append((125, "opening_first_action_event", {
                key: item.get(key)
                for key in (
                    "id",
                    "title",
                    "chapter_ordinals",
                    "people",
                    "trigger",
                    "process",
                    "outcome",
                    "evidence_ids",
                )
            }))
        if question_ids == ("2.1",):
            return materials
    protagonist = str((overview or {}).get("protagonist") or "").strip()
    for item in projection.get("characters", []):
        if item.get("role") == "PROTAGONIST" or item.get("name") == protagonist:
            materials.append((112, "protagonist", item))
    for item in projection.get("phases", []):
        materials.append((88, "narrative_phase", item))
    for item in deep.get("foreshadowing", []):
        materials.append((96, "foreshadowing", item))
    for item in _balanced_items(deep.get("scene_analysis", []), "chapter_ordinal"):
        materials.append((86, "scene_analysis", item))
    for item in deep.get("claims", []):
        materials.append((82, "analysis_claim", item))
    for item in deep.get("world_rules", []):
        materials.append((76, "world_rule", item))
    for item in deep.get("conflicts", []):
        materials.append((74, "conflict", item))
    return materials


def _opening_action_counts(projection: dict) -> list[dict[str, object]]:
    events = projection.get("events", [])
    result: list[dict[str, object]] = []
    for chapter_limit in (3, 10, 30):
        people: dict[str, set[str]] = {}
        event_ids: set[str] = set()
        evidence_ids: set[str] = set()
        for event in events:
            chapters = {
                int(chapter)
                for chapter in event.get("chapter_ordinals", [])
                if int(chapter) > 0
            }
            if not any(chapter <= chapter_limit for chapter in chapters):
                continue
            event_id = str(event.get("id") or "")
            if event_id:
                event_ids.add(event_id)
            event_evidence = {
                str(evidence_id)
                for evidence_id in event.get("evidence_ids", [])
                if evidence_id
            }
            evidence_ids.update(event_evidence)
            for person in event.get("people", []):
                name = str(person).strip()
                if name:
                    people.setdefault(name, set()).update(event_evidence)
        result.append({
            "through_chapter": chapter_limit,
            "active_character_count": len(people),
            "active_characters": sorted(people),
            "event_count": len(event_ids),
            "evidence_ids": sorted(evidence_ids),
            "method": "按前 N 章事件中实际参与行动的具名人物去重；仅在人物名单中出现的不计入。",
        })
    return result


def _normalized_person_name(value: object) -> str:
    return "".join(str(value or "").split()).casefold()


def _opening_character_program_artifact(projection: dict) -> dict[str, object]:
    """Build the deterministic half of question 2.1 from canonical events.

    The model only proposes each first scene's narrative function. Sequence,
    counts, intervals and later activity volume remain program-owned facts.
    """

    opening_events: list[tuple[int, int, dict]] = []
    for event_order, event in enumerate(projection.get("events", [])):
        chapter_ordinals = sorted({
            int(chapter)
            for chapter in event.get("chapter_ordinals", [])
            if 0 < int(chapter) <= 30
        })
        if not chapter_ordinals:
            continue
        opening_events.append((chapter_ordinals[0], event_order, event))
    opening_events.sort(key=lambda item: (item[0], item[1], str(item[2].get("id") or "")))

    people: dict[str, dict[str, object]] = {}
    canonical_labels: dict[str, str] = {}
    for first_chapter, event_order, event in opening_events:
        event_id = str(event.get("id") or "").strip()
        if not event_id:
            continue
        event_evidence_ids = sorted({
            str(evidence_id)
            for evidence_id in event.get("evidence_ids", [])
            if evidence_id
        })
        for raw_name in event.get("people", []):
            name = str(raw_name or "").strip()
            normalized = _normalized_person_name(name)
            if not normalized:
                continue
            canonical_labels.setdefault(normalized, name)
            item = people.setdefault(normalized, {
                "character_name": canonical_labels[normalized],
                "first_action_chapter": first_chapter,
                "first_action_event_id": event_id,
                "first_action_event_title": str(event.get("title") or ""),
                "first_action_event_order": event_order,
                "first_action_evidence_ids": event_evidence_ids,
                "action_event_ids_through_30": [],
            })
            action_event_ids = item["action_event_ids_through_30"]
            if isinstance(action_event_ids, list) and event_id not in action_event_ids:
                action_event_ids.append(event_id)

    roles: list[dict[str, object]] = []
    for item in people.values():
        event_count = len(item["action_event_ids_through_30"])
        if event_count <= 1:
            volume = "单次行动"
        elif event_count <= 4:
            volume = "持续参与"
        else:
            volume = "高频参与"
        roles.append({
            **item,
            "action_event_count_through_30": event_count,
            "later_role_volume": volume,
        })
    roles.sort(key=lambda item: (
        int(item["first_action_chapter"]),
        int(item["first_action_event_order"]),
        str(item["character_name"]),
    ))
    for sequence_no, item in enumerate(roles, start=1):
        item["sequence_no"] = sequence_no

    introductions_by_chapter: dict[int, list[str]] = {}
    for item in roles:
        introductions_by_chapter.setdefault(
            int(item["first_action_chapter"]),
            [],
        ).append(str(item["character_name"]))
    introduction_points: list[dict[str, object]] = []
    previous_chapter: int | None = None
    for chapter in sorted(introductions_by_chapter):
        introduction_points.append({
            "chapter_ordinal": chapter,
            "new_character_count": len(introductions_by_chapter[chapter]),
            "new_characters": introductions_by_chapter[chapter],
            "chapters_since_previous_introduction": (
                None if previous_chapter is None else chapter - previous_chapter
            ),
        })
        previous_chapter = chapter

    active_names = {
        _normalized_person_name(item["character_name"]) for item in roles
    }
    identity_candidates = [
        {
            key: candidate.get(key)
            for key in (
                "candidate_key",
                "left_name",
                "right_name",
                "reason",
                "evidence_ids",
            )
        }
        for candidate in projection.get("person_identity_candidates", [])
        if (
            _normalized_person_name(candidate.get("left_name")) in active_names
            or _normalized_person_name(candidate.get("right_name")) in active_names
        )
    ]
    return {
        "scope": "前 30 章内事件中实际参与行动的具名人物",
        "roles": roles,
        "introduction_points": introduction_points,
        "program_counts": _opening_action_counts(projection),
        "identity_duplicate_candidates": identity_candidates,
    }


def _compact_opening_character_model_artifact(
    artifact: dict[str, object],
) -> dict[str, object]:
    """Keep only the stable key and context needed for the model's proposal."""

    return {
        "scope": artifact["scope"],
        "roles": [
            {
                key: item.get(key)
                for key in (
                    "sequence_no",
                    "character_name",
                    "first_action_chapter",
                    "first_action_event_id",
                    "first_action_event_title",
                    "first_action_evidence_ids",
                )
            }
            for item in artifact["roles"]
            if isinstance(item, dict)
        ],
    }


def _readiness_check(
    question_id: str,
    *,
    ready: bool,
    observed: dict[str, object],
    gaps: list[str],
    required_artifact: str,
    answer_scope: Literal["COMPLETE", "PARTIAL", "NOT_READY"] | None = None,
    source_material: object | None = None,
) -> dict[str, object]:
    resolved_scope = answer_scope or ("COMPLETE" if ready else "NOT_READY")
    fingerprint_material: object = (
        source_material if source_material is not None else observed
    )
    contract_version = LEARNING_QUESTION_CONTRACT_VERSIONS.get(question_id)
    if contract_version is not None:
        fingerprint_material = {
            "question_contract_version": contract_version,
            "source_material": fingerprint_material,
        }
    return {
        "question_id": question_id,
        "question": _QUESTION_BY_ID[question_id].question,
        "ready": ready,
        "answer_scope": resolved_scope,
        "source_fingerprint": hashlib.sha256(
            json.dumps(
                fingerprint_material,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            ).encode("utf-8")
        ).hexdigest(),
        "observed": observed,
        "gaps": gaps,
        "required_artifact": required_artifact,
    }


def assess_learning_report_readiness(projection: dict) -> dict[str, object]:
    """Classify each first-group question independently as complete, partial, or blocked."""

    chapters = projection.get("chapters", [])
    chapter_count = len(chapters)
    events = projection.get("events", [])
    overview = projection.get("story_overview") or {}
    characters = projection.get("characters", [])
    deep = projection.get("deep_analysis") or {}
    action_counts = _opening_action_counts(projection)

    opening_events = [
        event
        for event in events
        if any(
            0 < int(chapter) <= 3
            for chapter in event.get("chapter_ordinals", [])
        )
        and event.get("evidence_ids")
    ]
    selling_point_gaps: list[str] = []
    if not str(overview.get("premise") or "").strip():
        selling_point_gaps.append("缺少可核验的故事前提，不能稳定压缩一句话卖点。")
    if not overview.get("evidence_ids"):
        selling_point_gaps.append("故事前提没有原文依据。")
    if not opening_events:
        selling_point_gaps.append("前三章没有带原文依据的开篇事件，无法定位首次兑现。")
    opening_payoff_candidates = (
        projection.get("opening_payoff_candidates_evidence") or {}
    )
    payoff_coverage = (
        opening_payoff_candidates.get("coverage", {})
        if isinstance(opening_payoff_candidates, dict)
        else {}
    )
    payoff_selected = (
        opening_payoff_candidates.get("selected")
        if isinstance(opening_payoff_candidates, dict)
        else None
    )
    payoff_projection_present = (
        "opening_payoff_candidates_status" in projection
        or "opening_payoff_candidates_evidence" in projection
    )
    if payoff_projection_present:
        if not opening_payoff_candidates:
            selling_point_gaps.append(
                "卖点首次兑现连续候选账本尚未生成。"
            )
        elif opening_payoff_candidates.get("is_current") is False:
            selling_point_gaps.append(
                "卖点首次兑现连续候选账本已经过期。"
            )
        elif not payoff_selected and not payoff_coverage.get(
            "all_source_events_scanned"
        ):
            selling_point_gaps.append(
                "卖点首次兑现候选尚未连续扫描到首次完整兑现或全书末尾。"
            )
    checks: dict[str, dict[str, object]] = {}
    checks["1.4"] = _readiness_check(
        "1.4",
        ready=not selling_point_gaps,
        observed={
            "chapter_count": chapter_count,
            "opening_event_count": len(opening_events),
            "overview_evidence_count": len(overview.get("evidence_ids", [])),
            "payoff_candidate_count": int(
                payoff_coverage.get("source_event_count") or 0
            ),
            "payoff_scanned_candidate_count": int(
                payoff_coverage.get("scanned_candidate_count") or 0
            ),
            "payoff_found": bool(payoff_selected),
        },
        gaps=(
            selling_point_gaps
            or [
                "当前只能形成单书部分回答；书名与简介须按源文件前置内容核对，"
                "同品类同商业模式的多书对照仍缺失。"
            ]
        ),
        required_artifact="开篇承诺与首次兑现证据表",
        answer_scope="PARTIAL" if not selling_point_gaps else "NOT_READY",
        source_material={
            "story_overview": overview,
            "opening_events": opening_events,
            "opening_payoff_candidates": opening_payoff_candidates,
        },
    )

    opening_character_artifact = _opening_character_program_artifact(projection)
    opening_character_roles = opening_character_artifact["roles"]
    action_gaps: list[str] = []
    for item in action_counts:
        if min(int(item["through_chapter"]), chapter_count) > 0 and int(item["active_character_count"]) == 0:
            action_gaps.append(
                f"前 {item['through_chapter']} 章没有可由事件证据确认的有效行动人物。"
            )
    roles_without_first_evidence = [
        str(item["character_name"])
        for item in opening_character_roles
        if not item.get("first_action_evidence_ids")
    ]
    action_ready = bool(opening_character_roles) and not roles_without_first_evidence
    if not action_ready:
        action_gaps.append("现有章节没有可由事件原文确认的有效行动人物。")
    if roles_without_first_evidence:
        action_gaps.append(
            f"有 {len(roles_without_first_evidence)} 位人物的首次行动事件缺少原文，"
            "不能判断首场功能。"
        )
    action_limitations = list(action_gaps)
    if chapter_count < 30:
        action_limitations.append(
            f"当前只有 {chapter_count} 章，只能按实际篇幅回答，不能完成前 30 章口径。"
        )
    identity_candidate_count = len(
        opening_character_artifact["identity_duplicate_candidates"]
    )
    if identity_candidate_count:
        action_limitations.append(
            f"仍有 {identity_candidate_count} 组涉及开篇人物的身份候选未裁定，"
            "程序会单列风险，不把它们静默合并。"
        )
    action_limitations.append("缺少同品类同商业模式作品的开篇阵容对照。")
    checks["2.1"] = _readiness_check(
        "2.1",
        ready=action_ready,
        observed={
            "program_counts": action_counts,
            "opening_character_role_count": len(opening_character_roles),
            "introduction_point_count": len(
                opening_character_artifact["introduction_points"]
            ),
            "identity_duplicate_candidate_count": identity_candidate_count,
            "roles_without_first_evidence_count": len(roles_without_first_evidence),
        },
        gaps=action_limitations,
        required_artifact=(
            "2.1 角色首行动序、首场功能、后续量级、新增间隔、"
            "功能分布与身份风险完整账本"
        ),
        answer_scope="PARTIAL" if action_ready else "NOT_READY",
        source_material=opening_character_artifact,
    )

    protagonist_name = str(overview.get("protagonist") or "").strip()
    protagonist = next(
        (
            character
            for character in characters
            if str(character.get("name") or "").strip() == protagonist_name
            or character.get("role") == "PROTAGONIST"
        ),
        None,
    )
    character_design = projection.get("character_design_evidence") or {}
    field_items = character_design.get("fields", []) if isinstance(character_design, dict) else []
    character_design_by_field = {
        str(item.get("field") or ""): item
        for item in field_items
        if isinstance(item, dict)
    }
    if not character_design_by_field and isinstance(character_design, dict):
        character_design_by_field = character_design
    required_character_fields = {
        "surface_desire": "表层欲望",
        "deep_desire": "深层欲望",
        "motivation": "动机",
        "contrast": "性格反差",
        "boundary": "行为底线",
        "core_ability": "核心能力",
    }
    character_gaps: list[str] = []
    character_limitations: list[str] = []
    insufficient_character_fields: list[str] = []
    if protagonist is None:
        character_gaps.append("尚未确认主角。")
    if character_design and character_design.get("is_current") is False:
        character_gaps.append("主角证据表基于旧人物或旧拆解结果，需要重新生成。")
    coverage = character_design.get("coverage", {}) if isinstance(character_design, dict) else {}
    if character_design and coverage.get("event_coverage_complete") is not True:
        character_gaps.append("主角证据表没有覆盖当前运行的全部主角事件。")
    for key, label in required_character_fields.items():
        item = character_design_by_field.get(key)
        if not isinstance(item, dict):
            character_gaps.append(f"缺少{label}的首次展示事件与原文。")
            continue
        status = item.get("status", "SUPPORTED")
        if status == "SUPPORTED":
            if (
                not str(item.get("value") or "").strip()
                or not item.get("first_display_chapter_ordinal")
                or not item.get("first_display_event_id")
                or not str(item.get("display_event") or "").strip()
                or not item.get("evidence_ids")
            ):
                character_gaps.append(f"缺少{label}的首次展示事件与原文。")
        elif status == "INSUFFICIENT_EVIDENCE":
            if (
                str(item.get("value") or "").strip()
                or item.get("first_display_chapter_ordinal") is not None
                or str(item.get("first_display_event_id") or "").strip()
                or str(item.get("display_event") or "").strip()
                or item.get("evidence_ids")
                or not str(item.get("explanation") or "").strip()
            ):
                character_gaps.append(f"{label}的证据不足记录格式无效，需要重新生成。")
            else:
                insufficient_character_fields.append(label)
                character_limitations.append(
                    f"当前全书主角事件账本未找到可核验的{label}首次展示事件与原文。"
                )
        else:
            character_gaps.append(f"{label}使用了未知证据状态，需要重新生成。")
    if character_design and not str(character_design.get("arc_summary") or "").strip():
        character_gaps.append("主角证据表缺少全书人物弧光总结。")
    checks["2.2"] = _readiness_check(
        "2.2",
        ready=not character_gaps,
        answer_scope=(
            "PARTIAL"
            if not character_gaps and insufficient_character_fields
            else None
        ),
        observed={
            "protagonist": protagonist_name,
            "existing_goal_count": len((protagonist or {}).get("goals", [])),
            "existing_motivation_count": len((protagonist or {}).get("motivations", [])),
            "contract_field_evidence_count": sum(
                1
                for key in required_character_fields
                if isinstance(character_design_by_field.get(key), dict)
                and character_design_by_field[key].get("status", "SUPPORTED") == "SUPPORTED"
                and character_design_by_field[key].get("evidence_ids")
            ),
            "contract_field_insufficient_count": len(
                insufficient_character_fields
            ),
            "contract_field_assessed_count": sum(
                1
                for key in required_character_fields
                if isinstance(character_design_by_field.get(key), dict)
                and character_design_by_field[key].get(
                    "status",
                    "SUPPORTED",
                )
                in {"SUPPORTED", "INSUFFICIENT_EVIDENCE"}
            ),
            "desire_conflict_count": len(character_design.get("desire_conflicts", []))
            if isinstance(character_design, dict)
            else 0,
            "event_coverage_complete": coverage.get("event_coverage_complete"),
        },
        gaps=character_gaps or character_limitations,
        required_artifact="主角双层欲望与最小完整集证据表",
        source_material=character_design,
    )

    chapter_end_hooks_evidence = projection.get("chapter_end_hooks_evidence") or {}
    chapter_end_hooks = projection.get("chapter_end_hooks") or []
    valid_hook_samples = [
        item
        for item in chapter_end_hooks
        if item.get("ending_evidence_ids")
        and item.get("hook_type")
        and item.get("strength")
    ]
    hook_gaps: list[str] = []
    if chapter_end_hooks_evidence and chapter_end_hooks_evidence.get("is_current") is False:
        hook_gaps.append("章末钩账本对应旧版拆解或旧版正文，需要重新生成。")
    if len(valid_hook_samples) < chapter_count:
        hook_gaps.append(
            f"需要连续覆盖全书 {chapter_count} 章，当前只有 {len(valid_hook_samples)} 章有效分类。"
        )
    coverage = (
        chapter_end_hooks_evidence.get("coverage", {})
        if isinstance(chapter_end_hooks_evidence, dict)
        else {}
    )
    if chapter_end_hooks and coverage.get("ending_evidence_complete") is not True:
        hook_gaps.append("章末钩账本没有通过程序的逐章结尾证据覆盖检查。")
    if chapter_end_hooks and coverage.get("sample_policy") != "ALL_CHAPTERS_WINDOWED":
        hook_gaps.append("章末钩账本不是按连续窗口覆盖全书，不能精确计算相邻轮换与连续记录。")
    if chapter_end_hooks and coverage.get("sequence_metrics_exact") is not True:
        hook_gaps.append("全书相邻轮换与连续强钩指标尚未通过精确合并检查。")
    checks["4.9"] = _readiness_check(
        "4.9",
        ready=not hook_gaps,
        observed={
            "chapter_count": chapter_count,
            "required_sample_count": chapter_count,
            "valid_chapter_end_sample_count": len(valid_hook_samples),
            "sample_policy": coverage.get("sample_policy"),
            "window_count": coverage.get("window_count"),
            "sequence_metrics_exact": coverage.get("sequence_metrics_exact"),
            "response_tracking_scope": coverage.get("response_tracking_scope"),
            "generic_scene_analysis_count": len(deep.get("scene_analysis", [])),
        },
        gaps=hook_gaps,
        required_artifact="全书连续覆盖的章末钩类型与节律账本",
        source_material=chapter_end_hooks_evidence,
    )

    opening_payoffs_evidence = (
        projection.get("opening_hook_payoffs_evidence") or {}
    )
    opening_hook_samples = (
        opening_payoffs_evidence.get("hooks", [])
        if isinstance(opening_payoffs_evidence, dict)
        else []
    )
    opening_coverage = (
        opening_payoffs_evidence.get("coverage", {})
        if isinstance(opening_payoffs_evidence, dict)
        else {}
    )
    opening_hook_gaps: list[str] = []
    required_opening_hook_count = min(3, chapter_count)
    if not opening_payoffs_evidence:
        opening_hook_gaps.append(
            "3.4 需要独立的前三章章末钩兑现追踪；4.9 只负责全书类型与节律，不能再拿它代替。"
        )
    elif opening_payoffs_evidence.get("is_current") is False:
        opening_hook_gaps.append(
            "3.4 兑现追踪对应旧版正文或旧版 4.9 账本，需要重新生成。"
        )
    if len(opening_hook_samples) < required_opening_hook_count:
        opening_hook_gaps.append(
            f"前三章需要 {required_opening_hook_count} 条钩子兑现记录，"
            f"当前只有 {len(opening_hook_samples)} 条。"
        )
    if chapter_count < 3:
        opening_hook_gaps.append(
            f"作品当前只有 {chapter_count} 章，无法形成完整三章口径。"
        )
    if (
        opening_payoffs_evidence
        and opening_coverage.get("search_policy")
        != "ALL_LATER_CHAPTERS_WINDOWED"
    ):
        opening_hook_gaps.append(
            "3.4 没有按连续窗口搜索全部后续章节，不能判定最早回应或全书未回应。"
        )
    if (
        opening_payoffs_evidence
        and opening_coverage.get("all_windows_completed") is not True
        and opening_coverage.get("all_required_hooks_resolved") is not True
    ):
        opening_hook_gaps.append(
            "3.4 尚未找到全部开篇钩子的首次回应，也没有连续检查到全书结尾。"
        )
    if (
        opening_payoffs_evidence
        and opening_coverage.get("ending_evidence_complete") is not True
    ):
        opening_hook_gaps.append("3.4 的前三章真实章末证据不完整。")
    if (
        opening_payoffs_evidence
        and opening_coverage.get("response_position_program_validated")
        is not True
    ):
        opening_hook_gaps.append("3.4 的回应章节与原文对应尚未通过程序校验。")
    checks["3.4"] = _readiness_check(
        "3.4",
        ready=not opening_hook_gaps,
        observed={
            "required_chapter_count": required_opening_hook_count,
            "classified_opening_chapter_count": len(opening_hook_samples),
            "valid_payoff_tracking_count": len(opening_hook_samples),
            "search_policy": opening_coverage.get("search_policy"),
            "window_count": opening_coverage.get("window_count"),
            "all_windows_completed": opening_coverage.get(
                "all_windows_completed"
            ),
        },
        gaps=opening_hook_gaps,
        required_artifact="前三章章末钩兑现追踪表（独立于 4.9）",
        source_material=opening_payoffs_evidence,
    )

    foreshadowing_ledger = projection.get("foreshadowing_ledger") or {}
    ledger_coverage = int(foreshadowing_ledger.get("covered_chapter_count") or 0)
    lifecycle_items = foreshadowing_ledger.get("lifecycles") or []
    valid_lifecycles = [
        item
        for item in lifecycle_items
        if item.get("setup_evidence_ids")
        and item.get("reinforcement_points") is not None
        and item.get("payoff_status")
    ]
    foreshadowing_gaps: list[str] = []
    if ledger_coverage < chapter_count:
        foreshadowing_gaps.append(
            f"伏笔专项账本只覆盖 {ledger_coverage}/{chapter_count} 章，不能判断未发现是否真实为零。"
        )
    if lifecycle_items and len(valid_lifecycles) != len(lifecycle_items):
        foreshadowing_gaps.append("已有伏笔条目缺少埋设、保温或回收状态中的至少一段。")
    if not foreshadowing_ledger:
        foreshadowing_gaps.append("现有深层伏笔候选不是覆盖全书的生命周期账本。")
    checks["5.3"] = _readiness_check(
        "5.3",
        ready=not foreshadowing_gaps,
        observed={
            "covered_chapter_count": ledger_coverage,
            "lifecycle_count": len(valid_lifecycles),
            "generic_foreshadowing_count": len(deep.get("foreshadowing", [])),
        },
        gaps=foreshadowing_gaps,
        required_artifact="全书伏笔埋设—保温—回收生命周期账本",
    )

    decision_dependencies = [checks[question_id] for question_id in ("1.4", "2.1", "2.2")]
    decision_gaps = [
        f"依赖问题 {item['question_id']} 的证据原料尚未就绪。"
        for item in decision_dependencies
        if not item["ready"]
    ]
    if not projection.get("phases"):
        decision_gaps.append("缺少覆盖全书的剧情阶段，无法区分前置决定与后期生长。")
    checks["6.4"] = _readiness_check(
        "6.4",
        ready=not decision_gaps,
        observed={
            "dependency_status": {
                item["question_id"]: item["ready"] for item in decision_dependencies
            },
            "phase_count": len(projection.get("phases", [])),
        },
        gaps=decision_gaps,
        required_artifact="作者前置决策的跨问题结算表",
    )

    method_dependencies = [checks[question_id] for question_id in ("1.4", "4.9", "5.3")]
    sample_metadata = projection.get("sample_metadata") or {}
    method_gaps = [
        f"依赖问题 {item['question_id']} 的证据原料尚未就绪。"
        for item in method_dependencies
        if not item["ready"]
    ]
    if not sample_metadata.get("platform") or not sample_metadata.get("commercial_model"):
        method_gaps.append("缺少平台与商业模式元数据，环境因素不能进入可复制性判断。")
    checks["6.5"] = _readiness_check(
        "6.5",
        ready=not method_gaps,
        observed={
            "dependency_status": {
                item["question_id"]: item["ready"] for item in method_dependencies
            },
            "has_platform": bool(sample_metadata.get("platform")),
            "has_commercial_model": bool(sample_metadata.get("commercial_model")),
        },
        gaps=method_gaps,
        required_artifact="单书方法候选三栏结算表",
    )

    ordered_checks = [
        checks[question_id] for question_id in INITIAL_INCREMENTAL_QUESTION_IDS
    ]
    blocking_checks = [item for item in ordered_checks if not item["ready"]]
    generation_ready_question_ids = [
        str(item["question_id"]) for item in ordered_checks if item["ready"]
    ]
    return {
        "ready": bool(generation_ready_question_ids),
        "policy": "按问题独立生成；部分答案明确缺口，无关问题不得阻塞。",
        "ready_question_count": len(generation_ready_question_ids),
        "complete_question_count": sum(
            item["answer_scope"] == "COMPLETE" for item in ordered_checks
        ),
        "partial_question_count": sum(
            item["answer_scope"] == "PARTIAL" for item in ordered_checks
        ),
        "total_question_count": len(LEARNING_QUESTION_CATALOG),
        "assessed_question_count": len(ordered_checks),
        "generation_ready_question_ids": generation_ready_question_ids,
        "checks": ordered_checks,
        "next_required_artifacts": list(dict.fromkeys(
            str(item["required_artifact"]) for item in blocking_checks
        )),
    }


class LearningReportNotReadyError(ValueError):
    def __init__(self, readiness: dict[str, object]) -> None:
        super().__init__("LEARNING_REPORT_DATA_NOT_READY")
        self.readiness = readiness


def _report_uses_current_contract(report: LearningReport) -> bool:
    if report.prompt_version not in LEARNING_REPORT_COMPATIBLE_PROMPT_VERSIONS:
        return False
    try:
        payload = json.loads(report.payload_json)
    except (TypeError, json.JSONDecodeError):
        return False
    return payload.get("catalog_version") == LEARNING_QUESTION_CATALOG_VERSION


def _answer_uses_current_contract(
    payload: dict[str, object],
    question_id: str,
) -> bool:
    versions = payload.get("question_contract_versions") or {}
    if not isinstance(versions, dict):
        return False
    return versions.get(question_id, "1.0.0") == (
        LEARNING_QUESTION_CONTRACT_VERSIONS.get(question_id, "1.0.0")
    )


def provider_payload_for_learning_report(
    session: Session,
    settings: Settings,
    task_payload: dict,
) -> dict:
    from .workbench import build_workbench_projection

    run_id = str(task_payload.get("run_id") or "")
    version = session.get(SourceVersion, task_payload.get("source_version_id"))
    deep = session.scalar(
        select(DeepAnalysis)
        .where(DeepAnalysis.run_id == run_id)
        .order_by(DeepAnalysis.revision_no.desc())
    )
    if not run_id or version is None or deep is None:
        raise ValueError("DEEP_ANALYSIS_NOT_READY")
    expected_deep_revision = int(task_payload.get("source_deep_revision") or 0)
    if deep.revision_no != expected_deep_revision:
        raise ValueError("LEARNING_REPORT_SOURCE_OUTDATED")
    projection = build_workbench_projection(session, run_id)
    readiness = assess_learning_report_readiness(projection)
    requested_question_ids = tuple(
        str(question_id) for question_id in task_payload.get("question_ids", [])
    )
    if (
        len(requested_question_ids) != 1
        or len(set(requested_question_ids)) != len(requested_question_ids)
        or not set(requested_question_ids).issubset(INITIAL_INCREMENTAL_QUESTION_IDS)
    ):
        raise ValueError("LEARNING_REPORT_QUESTION_SELECTION_INVALID")
    readiness_by_id = {
        str(item["question_id"]): item for item in readiness["checks"]
    }
    requested_fingerprints = task_payload.get("question_source_fingerprints") or {}
    if any(
        not readiness_by_id[question_id]["ready"]
        for question_id in requested_question_ids
    ):
        raise LearningReportNotReadyError(readiness)
    if any(
        requested_fingerprints.get(question_id)
        != readiness_by_id[question_id]["source_fingerprint"]
        for question_id in requested_question_ids
    ):
        raise ValueError("LEARNING_REPORT_SOURCE_OUTDATED")
    _service, profile = resolve_analysis_profile(
        settings,
        str(task_payload.get("model_profile_id") or ENTITIES_EVENTS_PROFILE_ID),
    )
    chapter_by_unit_id = _chapter_index_by_unit_id(session, version.id)
    source_materials = _source_materials(projection, requested_question_ids)
    opening_promise_sources: dict[str, object] | None = None
    if requested_question_ids == ("1.4",):
        opening_promise_sources = _opening_promise_source_artifact(
            session,
            settings,
            version,
        )
        source_materials.append((
            130,
            "opening_promise_sources",
            opening_promise_sources,
        ))
    all_evidence_ids = _evidence_ids({
        "story_overview": projection.get("story_overview"),
        "characters": projection.get("characters", []),
        "materials": [item for _priority, _kind, item in source_materials],
    })
    evidence_by_id = {
        item.id: item
        for item in session.scalars(
            select(EvidenceSpan).where(
                EvidenceSpan.source_version_id == version.id,
                EvidenceSpan.id.in_(all_evidence_ids),
            )
        )
    }
    question_catalog = [
        {
            "question_id": question_id,
            "question": _QUESTION_BY_ID[question_id].question,
            "analysis_requirement": _QUESTION_BY_ID[question_id].analysis_requirement,
            "output_contract": _QUESTION_BY_ID[question_id].output_contract,
            "measurement_requirements": _QUESTION_BY_ID[question_id].measurement_requirements,
            "evidence_requirements": _QUESTION_BY_ID[question_id].evidence_requirements,
            "scope_requirement": _QUESTION_BY_ID[question_id].scope_requirement,
            "external_data_policy": _QUESTION_BY_ID[question_id].external_data_policy,
        }
        for question_id in requested_question_ids
    ]
    for item in question_catalog:
        item["answer_scope"] = readiness_by_id[item["question_id"]]["answer_scope"]
        item["known_gaps"] = readiness_by_id[item["question_id"]]["gaps"]
        item["required_contract_items"] = [
            {
                "item_id": definition.item_id,
                "label": definition.label,
                "requirement": definition.requirement,
            }
            for definition in LEARNING_QUESTION_MODEL_ITEM_CONTRACTS.get(
                item["question_id"],
                LEARNING_QUESTION_ITEM_CONTRACTS.get(item["question_id"], ()),
            )
        ]
    compact_action_counts = [
        {
            key: item[key]
            for key in (
                "through_chapter",
                "active_character_count",
                "event_count",
                "method",
            )
        }
        for item in _opening_action_counts(projection)
    ]
    question_id = requested_question_ids[0]
    include_chapter_catalog = question_id in {"1.4", "2.1", "3.4", "4.9"}
    fixed_input: dict[str, object] = {
        "catalog_version": LEARNING_QUESTION_CATALOG_VERSION,
        "question_contract_version": LEARNING_QUESTION_CONTRACT_VERSIONS.get(
            question_id,
            "1.0.0",
        ),
        "batch_label": LEARNING_REPORT_BATCH_LABEL,
        "generation_policy": (
            "本次只回答 question_catalog 中唯一一问；"
            "PARTIAL 必须保留已知缺口，不得借用其他问题的就绪状态。"
        ),
        "question_catalog": question_catalog,
    }
    if include_chapter_catalog:
        fixed_input["chapter_catalog"] = [
            {
                "ordinal": int(item.get("ordinal") or 0),
                "title": item.get("title"),
            }
            for item in sorted(
                chapter_by_unit_id.values(),
                key=lambda item: int(item.get("ordinal") or 0),
            )
        ]
    if question_id == "1.4":
        if opening_promise_sources is None:
            raise ValueError("LEARNING_REPORT_1_4_PROMISE_SOURCE_MISSING")
        payoff_ledger = (
            projection.get("opening_payoff_candidates_evidence") or {}
        )
        if (
            not isinstance(payoff_ledger, dict)
            or payoff_ledger.get("is_current") is False
            or not payoff_ledger.get("coverage")
        ):
            raise ValueError(
                "LEARNING_REPORT_1_4_PAYOFF_LEDGER_NOT_READY"
            )
        payoff_rows = [
            item
            for item in payoff_ledger.get("classifications", [])
            if isinstance(item, dict)
        ]
        selected_payoff = payoff_ledger.get("selected")
        selected_sequence = int(
            (selected_payoff or {}).get("sequence_no") or 0
        )
        representative_rows = [
            item
            for item in payoff_rows
            if (
                int(item.get("chapter_ordinal") or 0) <= 3
                or (
                    selected_sequence > 0
                    and selected_sequence - 3
                    <= int(item.get("sequence_no") or 0)
                    <= selected_sequence
                )
            )
        ]
        fixed_input["program_artifacts"] = {
            "opening_payoff_candidate_ledger": {
                "coverage": payoff_ledger["coverage"],
                "selected": selected_payoff,
                "representative_classifications": (
                    representative_rows[:24]
                ),
                "program_policy": (
                    "专项已按全书事件顺序连续分窗；"
                    "用户答案不得重做分类或改选位置。"
                ),
            },
        }
    if question_id == "2.1":
        opening_character_artifact = _opening_character_program_artifact(projection)
        fixed_input["program_metrics"] = {
            "opening_action_character_counts": compact_action_counts,
        }
        fixed_input["program_artifacts"] = {
            "opening_character_ledger": _compact_opening_character_model_artifact(
                opening_character_artifact
            ),
        }
    fixed_chars = len(
        json.dumps(fixed_input, ensure_ascii=False, separators=(",", ":"), default=str)
    )
    request_budget = _request_budget(profile)
    material_budget = max(
        0,
        int(request_budget["input_char_budget"]) - fixed_chars - 4_000,
    )
    ranked_bundles: list[tuple[int, int, str, dict]] = []
    for order, (priority, kind, item) in enumerate(source_materials):
        bundle = _material_bundle(kind, item, evidence_by_id, chapter_by_unit_id)
        ranked_bundles.append((priority, order, kind, bundle))
    ranked_bundles.sort(key=lambda entry: (-entry[0], entry[1]))
    selected: list[dict] = []
    omitted: list[dict[str, object]] = []
    used_chars = 0
    selected_by_kind: dict[str, int] = {}
    for _priority, _order, kind, bundle in ranked_bundles:
        size = len(
            json.dumps(bundle, ensure_ascii=False, separators=(",", ":"), default=str)
        )
        if used_chars + size <= material_budget:
            selected.append(bundle)
            used_chars += size
            selected_by_kind[kind] = selected_by_kind.get(kind, 0) + 1
        else:
            omitted.append({"kind": kind, "reason": "当前模型上下文预算不足"})
    input_payload = {
        **fixed_input,
        "materials": selected,
        "coverage_manifest": {
            "question_id": question_id,
            **request_budget,
            "material_budget_chars": material_budget,
            "selected_count": len(selected),
            "selected_chars": used_chars,
            "selected_by_kind": selected_by_kind,
            "omitted_count": len(omitted),
            "omitted_by_kind": {
                kind: sum(1 for item in omitted if item["kind"] == kind)
                for kind in sorted({str(item["kind"]) for item in omitted})
            },
        },
    }
    return {
        "instructions": _prompt(),
        "input": json.dumps(
            input_payload,
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        ),
        "output_schema": _inline_model_schema(LearningReportOutput),
        "model_profile_id": str(task_payload.get("model_profile_id") or ENTITIES_EVENTS_PROFILE_ID),
        "prompt_id": LEARNING_REPORT_PROMPT_ID,
        "prompt_version": LEARNING_REPORT_PROMPT_VERSION,
        "source_version_id": version.id,
        "source_char_start": 0,
        "source_char_end": version.total_chars,
        "context_manifest": input_payload["coverage_manifest"],
    }


def _answer_user_visible_texts(
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
        for text in answer_texts
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


def parse_learning_report(
    value: dict,
    *,
    expected_question_ids: tuple[str, ...] | list[str] | None = None,
    defer_user_text_checks_for: set[str] | None = None,
) -> LearningReportOutput:
    try:
        output = LearningReportOutput.model_validate(value)
    except ValidationError as exc:
        raise LearningReportValidationError(
            "LEARNING_REPORT_OUTPUT_INVALID", _validation_errors(exc)
        ) from exc
    question_ids = [item.question_id for item in output.answers]
    if len(set(question_ids)) != len(question_ids):
        raise LearningReportValidationError(
            "LEARNING_REPORT_QUESTION_DUPLICATED",
            [{"path": ["answers"], "type": "value_error", "message": "同一问题只能回答一次"}],
        )
    expected = tuple(expected_question_ids or INITIAL_INCREMENTAL_QUESTION_IDS)
    if set(question_ids) != set(expected) or len(question_ids) != len(expected):
        raise LearningReportValidationError(
            "LEARNING_REPORT_QUESTION_COVERAGE_INVALID",
            [{
                "path": ["answers"],
                "type": "value_error",
                "message": f"必须恰好回答本次提交的 {len(expected)} 个问题",
            }],
        )
    if output.author_decisions or output.method_candidates:
        raise LearningReportValidationError(
            "LEARNING_REPORT_OUT_OF_SCOPE_SUMMARY",
            [{
                "path": ["author_decisions", "method_candidates"],
                "type": "value_error",
                "message": "首组逐问答案不得提前生成作者决策或方法候选汇总",
            }],
        )
    deferred = defer_user_text_checks_for or set()
    for answer in output.answers:
        if answer.question_id not in deferred:
            _validate_answer_user_text_boundaries(answer)
        definitions = LEARNING_QUESTION_MODEL_ITEM_CONTRACTS.get(
            answer.question_id,
            LEARNING_QUESTION_ITEM_CONTRACTS.get(answer.question_id),
        )
        if not definitions:
            continue
        expected_item_ids = [item.item_id for item in definitions]
        actual_item_ids = [item.item_id for item in answer.contract_items]
        if (
            actual_item_ids != expected_item_ids
            or len(set(actual_item_ids)) != len(actual_item_ids)
        ):
            raise LearningReportValidationError(
                "LEARNING_REPORT_CONTRACT_ITEM_COVERAGE_INVALID",
                [{
                    "path": ["answers", answer.question_id, "contract_items"],
                    "type": "value_error",
                    "message": (
                        "必须按规定顺序逐项返回问题合同："
                        f"{'、'.join(expected_item_ids)}"
                    ),
                }],
            )
        insufficient_items = [
            item.item_id
            for item in answer.contract_items
            if item.status == "INSUFFICIENT_EVIDENCE"
        ]
        unsupported_items_without_support = [
            item.item_id
            for item in answer.contract_items
            if (
                item.status == "SUPPORTED"
                and not item.evidence_ids
                and not item.metrics
                and not item.classifications
                and not item.payoff_classifications
                and item.item_id not in {
                    "first_scene_functions",
                    "scope_boundary",
                }
            )
        ]
        if unsupported_items_without_support:
            raise LearningReportValidationError(
                "LEARNING_REPORT_CONTRACT_ITEM_SUPPORT_MISSING",
                [{
                    "path": [
                        "answers",
                        answer.question_id,
                        "contract_items",
                    ],
                    "type": "value_error",
                    "message": (
                        "标为已支持的合同项目必须带原文、可核查指标或程序分类："
                        f"{'、'.join(unsupported_items_without_support)}"
                    ),
                }],
            )
        if insufficient_items and answer.status == "ANSWERED":
            raise LearningReportValidationError(
                "LEARNING_REPORT_CONTRACT_STATUS_INCONSISTENT",
                [{
                    "path": ["answers", answer.question_id, "status"],
                    "type": "value_error",
                    "message": (
                        "仍有合同项目证据不足时，整问不能标为已完整回答："
                        f"{'、'.join(insufficient_items)}"
                    ),
                }],
            )
        if answer.question_id == "1.4":
            item_by_id = {
                item.item_id: item for item in answer.contract_items
            }
            if answer.status != "PARTIAL":
                raise LearningReportValidationError(
                    "LEARNING_REPORT_1_4_SCOPE_INVALID",
                    [{
                        "path": ["answers", answer.question_id, "status"],
                        "type": "value_error",
                        "message": (
                            "当前缺少同口径多书对照，1.4 必须标为部分回答。"
                        ),
                    }],
                )
            if (
                item_by_id["cross_book_comparison"].status
                != "INSUFFICIENT_EVIDENCE"
            ):
                raise LearningReportValidationError(
                    "LEARNING_REPORT_1_4_CROSS_BOOK_SCOPE_INVALID",
                    [{
                        "path": [
                            "answers",
                            answer.question_id,
                            "contract_items",
                            "cross_book_comparison",
                        ],
                        "type": "value_error",
                        "message": "没有同口径多书数据时不得生成同类书卖点对比。",
                    }],
                )
        if answer.question_id == "2.2":
            expected_status = (
                "PARTIAL" if insufficient_items else "ANSWERED"
            )
            if answer.status != expected_status:
                raise LearningReportValidationError(
                    "LEARNING_REPORT_2_2_CONTRACT_STATUS_INVALID",
                    [{
                        "path": ["answers", answer.question_id, "status"],
                        "type": "value_error",
                        "message": (
                            "2.2 有证据不足项目时整问必须标为部分回答；"
                            "八项均有证据时才可标为完整回答。"
                        ),
                    }],
                )
        if answer.question_id == "4.9":
            if answer.status != "ANSWERED" or insufficient_items:
                raise LearningReportValidationError(
                    "LEARNING_REPORT_4_9_CONTRACT_INCOMPLETE",
                    [{
                        "path": ["answers", answer.question_id, "status"],
                        "type": "value_error",
                        "message": "专项原料已完整，必须逐项回答全部合同。",
                    }],
                )
        if answer.question_id == "2.1":
            item_by_id = {
                item.item_id: item for item in answer.contract_items
            }
            function_item = item_by_id["first_scene_functions"]
            if (
                function_item.status != "SUPPORTED"
                or not function_item.classifications
            ):
                raise LearningReportValidationError(
                    "LEARNING_REPORT_2_1_FIRST_FUNCTIONS_MISSING",
                    [{
                        "path": [
                            "answers",
                            answer.question_id,
                            "contract_items",
                            "first_scene_functions",
                        ],
                        "type": "value_error",
                        "message": "2.1 必须逐人返回首场功能分类，不能只给人数。",
                    }],
                )
            if answer.status != "PARTIAL":
                raise LearningReportValidationError(
                    "LEARNING_REPORT_2_1_CROSS_BOOK_SCOPE_INVALID",
                    [{
                        "path": [
                            "answers",
                            answer.question_id,
                            "status",
                        ],
                        "type": "value_error",
                        "message": "当前没有同口径多书数据，2.1 必须标为部分回答。",
                    }],
                )
    return output


def _validated_2_1_program_artifact(
    answer: LearningAnswerProposal,
    projection: dict,
) -> tuple[dict[str, object], list[dict[str, object]]]:
    program_artifact = _opening_character_program_artifact(projection)
    roles = [
        item
        for item in program_artifact["roles"]
        if isinstance(item, dict)
    ]
    roles_by_sequence = {
        int(item["sequence_no"]): item
        for item in roles
    }
    function_item = next(
        (
            item
            for item in answer.contract_items
            if item.item_id == "first_scene_functions"
        ),
        None,
    )
    if function_item is None:
        raise ValueError("LEARNING_REPORT_2_1_FIRST_FUNCTIONS_MISSING")
    classifications_by_sequence: dict[
        int, LearningContractClassificationProposal
    ] = {}
    for classification in function_item.classifications:
        sequence_no = classification.sequence_no
        if sequence_no in classifications_by_sequence:
            raise ValueError("LEARNING_REPORT_2_1_CHARACTER_DUPLICATED")
        role = roles_by_sequence.get(sequence_no)
        if role is None:
            raise ValueError("LEARNING_REPORT_2_1_CHARACTER_REFERENCE_INVALID")
        classifications_by_sequence[sequence_no] = classification
    if set(classifications_by_sequence) != set(roles_by_sequence):
        missing = sorted(
            str(roles_by_sequence[sequence]["character_name"])
            for sequence in set(roles_by_sequence) - set(classifications_by_sequence)
        )
        raise ValueError(
            "LEARNING_REPORT_2_1_CHARACTER_COVERAGE_INVALID:"
            + "、".join(missing[:20])
        )

    function_distribution: dict[str, int] = {}
    completed_roles: list[dict[str, object]] = []
    for role in roles:
        classification = classifications_by_sequence[int(role["sequence_no"])]
        function_distribution[classification.category] = (
            function_distribution.get(classification.category, 0) + 1
        )
        completed_roles.append({
            key: value
            for key, value in role.items()
            if key != "first_action_event_order"
        } | {
            "first_scene_function": classification.category,
            "first_scene_function_evidence_ids": list(
                role["first_action_evidence_ids"]
            ),
        })
    completed_artifact = {
        **program_artifact,
        "roles": completed_roles,
        "function_distribution": [
            {"function": function, "count": count}
            for function, count in sorted(
                function_distribution.items(),
                key=lambda item: (-item[1], item[0]),
            )
        ],
        "contract_validation": {
            "required_item_count": len(
                LEARNING_QUESTION_ITEM_CONTRACTS["2.1"]
            ),
            "covered_item_count": len(
                LEARNING_QUESTION_ITEM_CONTRACTS["2.1"]
            ),
            "model_returned_item_count": len(answer.contract_items),
            "character_classification_complete": True,
            "program_owned_fields": [
                "人物计数",
                "首行动顺序",
                "后续行动事件数与量级",
                "新增人物间隔",
                "功能分布",
                "身份重复候选",
            ],
            "model_proposed_program_validated_fields": ["首场功能"],
        },
    }
    program_metrics = [
        {
            "label": f"前 {item['through_chapter']} 章有效行动人物",
            "value": str(item["active_character_count"]),
            "unit": "人",
            "method": str(item["method"]),
            "evidence_ids": list(item.get("evidence_ids", []))[:16],
        }
        for item in program_artifact["program_counts"]
    ]
    program_metrics.extend([
        {
            "label": "首次行动引入批次",
            "value": str(len(program_artifact["introduction_points"])),
            "unit": "批",
            "method": "按人物首次参与有效行动的章节去重后计数。",
            "evidence_ids": [],
        },
        {
            "label": "未裁定身份重复候选",
            "value": str(len(program_artifact["identity_duplicate_candidates"])),
            "unit": "组",
            "method": "仅统计涉及前 30 章有效行动人物、且尚未由程序或用户裁定的身份候选。",
            "evidence_ids": [],
        },
    ])
    return completed_artifact, program_metrics


def _program_2_1_contract_items(
    artifact: dict[str, object],
) -> list[dict[str, object]]:
    counts = {
        int(item["through_chapter"]): int(item["active_character_count"])
        for item in artifact["program_counts"]
        if isinstance(item, dict)
    }
    function_distribution = [
        item
        for item in artifact["function_distribution"]
        if isinstance(item, dict)
    ]
    top_functions = "、".join(
        f"{item['function']} {item['count']} 人"
        for item in function_distribution[:3]
    )
    roles = [
        item for item in artifact["roles"] if isinstance(item, dict)
    ]
    introduction_points = [
        item
        for item in artifact["introduction_points"]
        if isinstance(item, dict)
    ]
    identity_candidates = [
        item
        for item in artifact["identity_duplicate_candidates"]
        if isinstance(item, dict)
    ]
    volume_counts = Counter(
        str(item.get("later_role_volume") or "")
        for item in roles
        if item.get("later_role_volume")
    )
    item_payloads = {
        "opening_character_counts": {
            "status": "SUPPORTED",
            "finding": (
                f"程序计数：前 3、10、30 章分别为 "
                f"{counts.get(3, 0)}、{counts.get(10, 0)}、"
                f"{counts.get(30, 0)} 人。"
            ),
        },
        "character_appearance_sequence": {
            "status": "SUPPORTED",
            "finding": f"完整记录 {len(roles)} 人的首次有效行动顺序、章节与事件。",
        },
        "first_scene_functions": {
            "status": "SUPPORTED",
            "finding": (
                f"模型只提交逐人功能类别，程序按序号合并并核对覆盖；"
                f"数量前三为{top_functions or '暂无'}。"
            ),
        },
        "later_role_volume": {
            "status": "SUPPORTED",
            "finding": "；".join(
                f"{label} {volume_counts[label]} 人"
                for label in ("高频参与", "持续参与", "单次行动")
                if volume_counts[label]
            ) or "当前没有可分档人物。",
        },
        "new_character_intervals": {
            "status": "SUPPORTED",
            "finding": (
                f"程序按首次有效行动章节识别出 {len(introduction_points)} 个引入批次。"
            ),
        },
        "first_function_distribution": {
            "status": "SUPPORTED",
            "finding": f"程序由完整逐人分类汇总；数量前三为{top_functions or '暂无'}。",
        },
        "identity_duplicate_risks": {
            "status": "SUPPORTED",
            "finding": f"单列 {len(identity_candidates)} 组尚未裁定的身份重复候选。",
        },
        "cross_book_comparison": {
            "status": "INSUFFICIENT_EVIDENCE",
            "finding": "缺少同品类、同商业模式作品的同口径数据，不能形成开篇标配阵容。",
        },
    }
    return [
        {
            "item_id": definition.item_id,
            **item_payloads[definition.item_id],
            "metrics": [],
            "evidence_ids": [],
            "limitations": (
                ["当前只能形成单书观察，不能外推为品类标准。"]
                if definition.item_id == "cross_book_comparison"
                else []
            ),
            "classifications": [],
        }
        for definition in LEARNING_QUESTION_ITEM_CONTRACTS["2.1"]
    ]


def _program_2_1_representative_evidence_ids(
    artifact: dict[str, object],
) -> list[str]:
    roles = [
        item for item in artifact["roles"] if isinstance(item, dict)
    ]
    selected: list[str] = []
    seen: set[str] = set()
    for role in _balanced_items(roles, "first_action_chapter"):
        for evidence_id in role.get("first_action_evidence_ids", []):
            value = str(evidence_id)
            if value and value not in seen:
                selected.append(value)
                seen.add(value)
                break
        if len(selected) >= 24:
            break
    return selected


def _program_2_1_reading_fields(
    artifact: dict[str, object],
) -> dict[str, list[str] | str]:
    counts = {
        int(item["through_chapter"]): int(item["active_character_count"])
        for item in artifact["program_counts"]
        if isinstance(item, dict)
    }
    introduction_count = len(artifact["introduction_points"])
    function_distribution = [
        item
        for item in artifact["function_distribution"]
        if isinstance(item, dict)
    ]
    top_functions = "、".join(
        f"{item['function']} {item['count']} 人"
        for item in function_distribution[:3]
    )
    volume_counts: dict[str, int] = {}
    for role in artifact["roles"]:
        if not isinstance(role, dict):
            continue
        volume = str(role.get("later_role_volume") or "")
        if volume:
            volume_counts[volume] = volume_counts.get(volume, 0) + 1
    volume_summary = "、".join(
        f"{label} {volume_counts[label]} 人"
        for label in ("高频参与", "持续参与", "单次行动")
        if volume_counts.get(label)
    )
    identity_candidate_count = len(artifact["identity_duplicate_candidates"])
    conclusion = (
        f"前 3、10、30 章分别有 {counts.get(3, 0)}、"
        f"{counts.get(10, 0)}、{counts.get(30, 0)} 名具名人物参与有效行动；"
        f"前 30 章共有 {introduction_count} 个首次行动引入批次。"
        f"经逐人首次事件与证据核对，本次首场功能分类数量前三为"
        f"{top_functions or '暂无'}；后续行动量级为{volume_summary or '暂无'}。"
        f"当前仍有 {identity_candidate_count} 组身份重复候选未裁定，"
        "且没有同口径跨书数据，因此本问只能作为单书部分回答。"
    )
    limitations = [
        (
            f"仍有 {identity_candidate_count} 组涉及前 30 章人物的身份重复候选"
            "尚未裁定；程序不会静默合并，当前人数可能偏高。"
        ),
        "缺少同品类、同商业模式作品的同口径数据，不能把本书人物规模当成品类标准。",
    ]
    reusable_lessons = [
        (
            f"本书不是一次性罗列人物，而是在前 30 章的 "
            f"{introduction_count} 个行动批次中持续引入；设计群像时可借鉴"
            "“随事件入场”，但人物数量应按自己的篇幅和识别负担决定。"
        ),
        (
            f"首场功能数量前三为{top_functions or '暂无'}。"
            "可借鉴“首次行动即承担明确叙事功能”的做法，"
            "不能照搬本书的类别比例。"
        ),
    ]
    do_not_copy = [
        (
            f"不要把前 30 章 {counts.get(30, 0)} 名有效行动人物的密度"
            "直接当成开书标准；没有稳定识别锚点时会增加读者记忆负担。"
        ),
        (
            f"在 {identity_candidate_count} 组身份重复候选尚未裁定、"
            "又缺少跨书对照时，不要把当前人数或功能比例外推为市场规律。"
        ),
    ]
    return {
        "conclusion": conclusion,
        "limitations": limitations,
        "reusable_lessons": reusable_lessons,
        "do_not_copy": do_not_copy,
    }


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


def _opening_promise_metric_by_source(
    item: LearningContractItemProposal,
) -> dict[str, LearningMetricProposal]:
    aliases = {
        "title": {"书名"},
        "description": {"简介", "内容提要"},
        "premise": {"故事前提", "前提"},
        "opening_chapters": {"前三章", "前 3 章", "前3章"},
    }
    by_source: dict[str, LearningMetricProposal] = {}
    for metric in item.metrics:
        label = metric.label.strip()
        source = next(
            (
                source
                for source, allowed_labels in aliases.items()
                if label in allowed_labels
            ),
            None,
        )
        if source is None or source in by_source:
            raise ValueError(
                "LEARNING_REPORT_1_4_PROMISE_SOURCE_METRICS_INVALID"
            )
        by_source[source] = metric
    if set(by_source) != set(aliases) or len(item.metrics) != len(aliases):
        raise ValueError(
            "LEARNING_REPORT_1_4_PROMISE_SOURCE_METRICS_INVALID"
        )
    return by_source


def _program_1_4_promise_sources(
    item: LearningContractItemProposal,
    projection: dict,
    opening_promise_sources: dict[str, object],
    opening_promise_rows: list[dict[str, object]],
) -> dict[str, object]:
    if item.status != "SUPPORTED":
        raise ValueError("LEARNING_REPORT_1_4_PROMISE_SOURCE_INVALID")
    metrics = _opening_promise_metric_by_source(item)
    preferred_title = str(
        opening_promise_sources.get("preferred_title") or ""
    ).strip()
    if preferred_title and preferred_title not in metrics["title"].value:
        raise ValueError("LEARNING_REPORT_1_4_TITLE_SOURCE_INVALID")

    description_evidence = {
        str(evidence_id)
        for evidence_id in opening_promise_sources.get(
            "description_evidence_ids",
            [],
        )
        if evidence_id
    }
    description_metric_evidence = set(metrics["description"].evidence_ids)
    if opening_promise_sources.get("description_present"):
        if (
            not description_metric_evidence.intersection(
                description_evidence
            )
            or re.search(
                r"(?:缺失|未提供|无法确认|不能确认)",
                metrics["description"].value,
            )
        ):
            raise ValueError(
                "LEARNING_REPORT_1_4_DESCRIPTION_SOURCE_INVALID"
            )

    overview = projection.get("story_overview") or {}
    overview_premise = str(overview.get("premise") or "").strip()
    overview_evidence = _evidence_ids(overview)
    premise_evidence = set(metrics["premise"].evidence_ids)
    if (
        not overview_premise
        or not overview_evidence
        or not premise_evidence
        or not premise_evidence.intersection(overview_evidence)
    ):
        raise ValueError(
            "LEARNING_REPORT_1_4_PREMISE_SOURCE_INVALID"
        )

    opening_events = [
        event
        for event in projection.get("events", [])
        if isinstance(event, dict)
    ]
    first_three_events = [
        event
        for event in opening_events
        if any(
            1 <= int(chapter) <= 3
            for chapter in event.get("chapter_ordinals", [])
        )
    ]
    first_three_evidence = _evidence_ids(first_three_events)
    first_three_body_evidence = (
        first_three_evidence - description_evidence
    )
    opening_metric_evidence = set(
        metrics["opening_chapters"].evidence_ids
    )
    known_opening_metric_evidence = opening_metric_evidence.intersection(
        first_three_evidence
        | description_evidence
        | overview_evidence
    )
    authoritative_opening_evidence = {
        str(row.get("anchor_evidence_id") or "")
        for row in opening_promise_rows
        if row.get("anchor_evidence_id")
    }
    if (
        not opening_promise_rows
        or not authoritative_opening_evidence
        or not opening_metric_evidence
        or (
            known_opening_metric_evidence
            and (
                not known_opening_metric_evidence.issubset(
                    first_three_evidence
                )
                or not known_opening_metric_evidence.intersection(
                    first_three_body_evidence
                )
                or not known_opening_metric_evidence.intersection(
                    authoritative_opening_evidence
                )
            )
        )
    ):
        raise ValueError(
            "LEARNING_REPORT_1_4_OPENING_CHAPTER_SOURCE_INVALID"
        )

    description_value = str(
        opening_promise_sources.get("description_text")
        or metrics["description"].value
    ).strip()
    description_ids = (
        sorted(description_evidence)
        if opening_promise_sources.get("description_present")
        else list(metrics["description"].evidence_ids)
    )
    opening_value = "；".join(
        (
            f"第 {int(row['chapter_ordinal'])} 章"
            f"“{str(row['chapter_title'])}”："
            f"{str(row['event_title'])}"
        )
        for row in opening_promise_rows
    )
    opening_evidence_ids = list(dict.fromkeys(
        str(row["anchor_evidence_id"])
        for row in opening_promise_rows
    ))
    compiled_metrics = [
        LearningMetricProposal(
            label="书名",
            value=preferred_title or metrics["title"].value,
            unit="",
            method="程序核对源文件前置书名。",
            evidence_ids=[],
        ),
        LearningMetricProposal(
            label="简介",
            value=description_value,
            unit="",
            method="程序直接读取源文件前置简介原文。",
            evidence_ids=description_ids,
        ),
        LearningMetricProposal(
            label="故事前提",
            value=overview_premise,
            unit="",
            method="程序读取当前故事总览前提及其完整依据。",
            evidence_ids=sorted(overview_evidence)[:16],
        ),
        LearningMetricProposal(
            label="前三章",
            value=opening_value,
            unit="",
            method="程序汇总前三章中命中 F1 的卖点候选及现场原文。",
            evidence_ids=opening_evidence_ids[:16],
        ),
    ]
    item.finding = "；".join(
        f"{metric.label}：{metric.value}"
        for metric in compiled_metrics
    )
    item.metrics = compiled_metrics
    item.evidence_ids = list(dict.fromkeys(
        evidence_id
        for metric in compiled_metrics
        for evidence_id in metric.evidence_ids
    ))[:32]
    item.limitations = []
    item.classifications = []
    item.payoff_classifications = []
    return {
        "title": {
            "value": compiled_metrics[0].value,
            "evidence_ids": [],
        },
        "description": {
            "value": compiled_metrics[1].value,
            "evidence_ids": compiled_metrics[1].evidence_ids,
        },
        "story_premise": {
            "value": compiled_metrics[2].value,
            "evidence_ids": compiled_metrics[2].evidence_ids,
        },
        "opening_chapters": {
            "value": compiled_metrics[3].value,
            "evidence_ids": compiled_metrics[3].evidence_ids,
            "allowed_first_three_evidence_count": len(
                first_three_body_evidence
            ),
        },
        "contract_validation": {
            "four_sources_compiled_by_program": True,
            "description_evidence_matched": bool(
                not opening_promise_sources.get("description_present")
                or description_metric_evidence.intersection(
                    description_evidence
                )
            ),
            "opening_chapters_use_non_description_evidence": True,
            "story_premise_compiled_from_overview": True,
            "opening_chapters_compiled_from_f1_candidates": True,
        },
    }


def _legacy_1_4_payoff_ledger(
    payoff: LearningContractItemProposal,
    projection: dict,
    evidence_by_id: dict[str, EvidenceSpan],
    chapter_by_unit_id: dict[str, dict[str, object]],
    opening_promise_sources: dict[str, object],
) -> dict[str, object]:
    artifact = _opening_payoff_candidate_artifact(
        projection,
        evidence_by_id,
        chapter_by_unit_id,
        opening_promise_sources,
    )
    candidates = [
        item
        for item in artifact.get("candidates", [])
        if isinstance(item, dict)
    ]
    candidate_by_sequence = {
        int(item["sequence_no"]): item for item in candidates
    }
    classifications = payoff.payoff_classifications
    sequence_numbers = [
        classification.sequence_no for classification in classifications
    ]
    if (
        not classifications
        or len(sequence_numbers) != len(set(sequence_numbers))
        or sequence_numbers != list(range(1, len(sequence_numbers) + 1))
        or any(
            sequence_no not in candidate_by_sequence
            for sequence_no in sequence_numbers
        )
    ):
        raise ValueError(
            "LEARNING_REPORT_1_4_PAYOFF_CLASSIFICATION_COVERAGE_INVALID"
        )
    rows: list[dict[str, object]] = []
    selected: dict[str, object] | None = None
    for classification in classifications:
        candidate = candidate_by_sequence[classification.sequence_no]
        evidence_options = {
            int(item["evidence_no"]): item
            for item in candidate.get("evidence_options", [])
            if isinstance(item, dict)
        }
        anchor = evidence_options.get(classification.anchor_evidence_no)
        if anchor is None:
            raise ValueError(
                "LEARNING_REPORT_1_4_PAYOFF_EVIDENCE_REFERENCE_INVALID"
            )
        program_exclusion = str(
            candidate.get("program_exclusion_code") or ""
        )
        if (
            program_exclusion
            and classification.exclusion_code != program_exclusion
        ):
            raise ValueError(
                "LEARNING_REPORT_1_4_PAYOFF_PROGRAM_EXCLUSION_INVALID"
            )
        is_complete = (
            set(classification.matched_facet_ids)
            == {"F1", "F2", "F3"}
            and classification.exclusion_code == "NONE"
        )
        row = {
            **classification.model_dump(mode="json"),
            "event_id": candidate.get("event_id"),
            "event_title": candidate.get("event_title"),
            "event_summary": candidate.get("event_summary"),
            "anchor_evidence_id": anchor.get("evidence_id"),
            "chapter_ordinal": anchor.get("chapter_ordinal"),
            "chapter_title": anchor.get("chapter_title"),
            "paragraph_number": anchor.get("paragraph_number"),
            "source_char_start": anchor.get("source_char_start"),
            "program_complete_payoff": is_complete,
        }
        rows.append(row)
        if is_complete and selected is None:
            selected = row
    if selected is None:
        raise ValueError("LEARNING_REPORT_1_4_COMPLETE_PAYOFF_MISSING")
    return {
        "classifications": rows,
        "selected": selected,
        "coverage": {
            "source_event_count": len(candidates),
            "scanned_candidate_count": len(rows),
            "planned_window_count": 1,
            "completed_window_count": 1,
            "all_source_events_scanned": len(rows) == len(candidates),
            "stopped_after_first_complete": (
                len(rows) < len(candidates)
            ),
            "source_sequence_contiguous": True,
            "legacy_direct_validation_only": True,
        },
    }


def _program_1_4_answer(
    answer: LearningAnswerProposal,
    projection: dict,
    *,
    opening_promise_sources: dict[str, object] | None = None,
    evidence_by_id: dict[str, EvidenceSpan] | None = None,
    chapter_by_unit_id: dict[str, dict[str, object]] | None = None,
) -> dict[str, object]:
    opening_promise_sources = opening_promise_sources or {}
    evidence_by_id = evidence_by_id or {}
    chapter_by_unit_id = chapter_by_unit_id or {}
    overview = projection.get("story_overview") or {}
    allowed = _evidence_ids({
        "story_overview": overview,
        "opening_events": projection.get("events", []),
        "opening_promise_sources": opening_promise_sources,
    })
    items = _contract_item_by_id(answer)
    selling_point = items["selling_point_card"]
    promise_sources = items["opening_promise_sources"]
    promise_source_text = "；".join((
        promise_sources.finding,
        _metric_blob(promise_sources),
    ))
    preferred_title = str(
        opening_promise_sources.get("preferred_title") or ""
    ).strip()
    if (
        preferred_title
        and (
            preferred_title not in promise_source_text
            or re.search(
                r"(?:书名|标题).{0,12}"
                r"(?:缺失|未提供|无法确认|不能确认|不是|并非|不叫)",
                promise_source_text,
            )
        )
    ):
        raise ValueError("LEARNING_REPORT_1_4_TITLE_SOURCE_INVALID")
    description_evidence = {
        str(evidence_id)
        for evidence_id in opening_promise_sources.get(
            "description_evidence_ids",
            [],
        )
        if evidence_id
    }
    actual_promise_evidence = _evidence_ids(
        promise_sources.model_dump(mode="json")
    )
    if (
        opening_promise_sources.get("description_present")
        and (
            not actual_promise_evidence.intersection(description_evidence)
            or re.search(
                r"(?:简介|内容提要).{0,12}"
                r"(?:缺失|未提供|无法确认|不能确认)",
                promise_source_text,
            )
        )
    ):
        raise ValueError("LEARNING_REPORT_1_4_DESCRIPTION_SOURCE_INVALID")
    required_source_labels = (
        ("书名",),
        ("简介", "内容提要"),
        ("故事前提", "前提"),
        ("前三章", "前 3 章", "前3章"),
    )
    if any(
        not any(label in promise_source_text for label in aliases)
        for aliases in required_source_labels
    ):
        raise ValueError("LEARNING_REPORT_1_4_PROMISE_SOURCE_INCOMPLETE")

    payoff = items["first_payoff_location"]
    source_candidate_artifact = (
        projection.get("opening_payoff_candidates_evidence") or {}
    )
    if not source_candidate_artifact and payoff.payoff_classifications:
        source_candidate_artifact = _legacy_1_4_payoff_ledger(
            payoff,
            projection,
            evidence_by_id,
            chapter_by_unit_id,
            opening_promise_sources,
        )
    elif (
        not isinstance(source_candidate_artifact, dict)
        or source_candidate_artifact.get("is_current") is False
    ):
        raise ValueError("LEARNING_REPORT_1_4_PAYOFF_LEDGER_NOT_READY")
    candidate_artifact = json.loads(json.dumps(
        source_candidate_artifact,
        ensure_ascii=False,
        default=str,
    ))
    payoff.payoff_classifications = []
    classified_rows = [
        item
        for item in candidate_artifact.get("classifications", [])
        if isinstance(item, dict)
    ]
    coverage = candidate_artifact.get("coverage", {})
    if (
        not classified_rows
        or not isinstance(coverage, dict)
        or coverage.get("source_sequence_contiguous") is not True
    ):
        raise ValueError("LEARNING_REPORT_1_4_PAYOFF_LEDGER_INVALID")
    selected_candidate = candidate_artifact.get("selected")
    if selected_candidate is not None and not isinstance(
        selected_candidate,
        dict,
    ):
        raise ValueError("LEARNING_REPORT_1_4_PAYOFF_LEDGER_INVALID")

    opening_promise_rows = [
        row
        for row in classified_rows
        if (
            "F1" in row.get("matched_facet_ids", [])
            and "F3" in row.get("matched_facet_ids", [])
            and 1 <= int(row.get("chapter_ordinal") or 0) <= 3
        )
    ]
    promise_source_artifact = _program_1_4_promise_sources(
        promise_sources,
        projection,
        opening_promise_sources,
        opening_promise_rows,
    )

    if selected_candidate is None:
        if coverage.get("all_source_events_scanned") is not True:
            raise ValueError(
                "LEARNING_REPORT_1_4_PAYOFF_LEDGER_INCOMPLETE"
            )
        scanned_count = int(
            coverage.get("scanned_candidate_count") or 0
        )
        payoff.status = "INSUFFICIENT_EVIDENCE"
        payoff.finding = (
            f"程序已按源文件顺序连续核对全书 {scanned_count} 个"
            "有效事件候选，未发现同时命中 F1、F2、F3 的完整兑现。"
        )
        payoff.metrics = []
        payoff.evidence_ids = []
        payoff.limitations = [
            "这是当前成书原文中的未发现结论，不等于卖点一定无效。"
        ]
        cross_book = items["cross_book_comparison"]
        cross_book.status = "INSUFFICIENT_EVIDENCE"
        cross_book.finding = (
            "当前缺少同品类、同商业模式作品的同口径数据，"
            "不能生成跨书卖点优劣结论。"
        )
        cross_book.metrics = []
        cross_book.evidence_ids = []
        cross_book.limitations = ["当前只能形成单书观察。"]
        scan_metric = LearningMetricProposal(
            label="连续核对候选数",
            value=str(scanned_count),
            unit="个事件",
            method="程序连续扫描到全书事件末尾。",
            evidence_ids=[],
        )
        answer.status = "PARTIAL"
        answer.conclusion = (
            f"{selling_point.finding} 当前全书连续候选中没有找到"
            "同时满足真实机制、主角直接卷入和现实行动的完整兑现。"
        )
        answer.metrics = [scan_metric]
        answer.evidence_ids = list(dict.fromkeys([
            *selling_point.evidence_ids,
            *promise_sources.evidence_ids,
        ]))[:24]
        answer.limitations = [
            "全书连续候选未发现完整兑现，不能伪造首次兑现位置。",
            "缺少同品类、同商业模式作品的同口径数据，不能形成跨书比较。",
        ]
        answer.reusable_lessons = [
            "先区分承诺被提到、机制真实出现、主角直接卷入和现实行动，未同时满足时不要强报首次兑现。",
        ]
        answer.do_not_copy = [
            "不能照搬本书的专有设定、人物、事件或原文表达。",
            "不能把本书未发现完整兑现直接写成其他作品的通用规则。",
        ]
        candidate_artifact["contract_validation"] = {
            "classification_sequence_contiguous": True,
            "all_source_events_scanned": True,
            "complete_payoff_not_fabricated": True,
            "position_compiled_by_program": True,
        }
        candidate_artifact["promise_source_contract"] = (
            promise_source_artifact
        )
        _validate_answer_evidence_subset(
            answer,
            allowed,
            error_code="LEARNING_REPORT_1_4_EVIDENCE_SCOPE_INVALID",
        )
        return candidate_artifact

    selected_sequence = int(
        selected_candidate.get("sequence_no") or 0
    )
    description_start = int(
        opening_promise_sources.get("description_source_char_start") or 0
    )
    promise_chapter = int(
        opening_promise_sources.get("promise_chapter_position") or 0
    )
    chapter = int(selected_candidate.get("chapter_ordinal") or 0)
    paragraph_number = int(
        selected_candidate.get("paragraph_number") or 0
    )
    source_char_start = int(
        selected_candidate.get("source_char_start") or 0
    )
    chapter_title = str(
        selected_candidate.get("chapter_title") or ""
    )
    selected_evidence_id = str(
        selected_candidate.get("anchor_evidence_id") or ""
    )
    if description_start <= 0:
        promise_anchor_ids = {
            *selling_point.evidence_ids,
            *promise_sources.evidence_ids,
        }
        promise_anchor_options = [
            evidence_by_id[evidence_id]
            for evidence_id in promise_anchor_ids
            if evidence_id in evidence_by_id
        ]
        promise_anchor = min(
            promise_anchor_options,
            key=lambda item: item.start_char,
            default=None,
        )
        if promise_anchor is not None:
            description_start = promise_anchor.start_char + 1
            promise_chapter = int(
                chapter_by_unit_id.get(
                    promise_anchor.source_unit_id,
                    {},
                ).get("ordinal")
                or chapter
            )
        else:
            description_start = source_char_start
            promise_chapter = chapter
    if (
        chapter <= 0
        or paragraph_number <= 0
        or source_char_start <= 0
        or not chapter_title
        or not selected_evidence_id
        or description_start <= 0
    ):
        raise ValueError("LEARNING_REPORT_1_4_PAYOFF_POSITION_INVALID")

    metrics = [
        LearningMetricProposal(
            label="首次兑现章节",
            value=str(chapter),
            unit="章",
            method="程序按连续候选分类选择第一条完整兑现。",
            evidence_ids=[selected_evidence_id],
        ),
        LearningMetricProposal(
            label="兑现段落",
            value=str(paragraph_number),
            unit="段",
            method="程序读取选中原文依据的一基段落号。",
            evidence_ids=[selected_evidence_id],
        ),
        LearningMetricProposal(
            label="兑现位置累计字符",
            value=str(source_char_start),
            unit="字符",
            method="程序读取选中原文依据的一基源文件字符起点。",
            evidence_ids=[selected_evidence_id],
        ),
        LearningMetricProposal(
            label="承诺到兑现章距",
            value=str(chapter - promise_chapter),
            unit="章",
            method="首次兑现章节减去前置简介所在位置 0。",
            evidence_ids=[selected_evidence_id],
        ),
        LearningMetricProposal(
            label="承诺到兑现字符距离",
            value=str(source_char_start - description_start),
            unit="字符",
            method="兑现原文起点减去简介承诺起点。",
            evidence_ids=[
                *sorted(description_evidence)[:1],
                selected_evidence_id,
            ],
        ),
    ]
    earlier_rows = classified_rows[
        max(0, selected_sequence - 4):selected_sequence - 1
    ]
    earlier_summary = "；".join(
        (
            f"第{row['sequence_no']}项“{row['event_title']}”"
            f"为{_PAYOFF_EXCLUSION_LABELS.get(str(row['exclusion_code']), '仅部分命中')}"
        )
        for row in earlier_rows
    )
    payoff.finding = (
        f"程序按源文件顺序连续核对前 {selected_sequence} 个事件候选，"
        f"第一条同时命中 F1、F2、F3 的完整兑现是第 {chapter} 章"
        f"“{chapter_title}”第 {paragraph_number} 段、源文件第 "
        f"{source_char_start} 个字符处的“"
        f"{selected_candidate.get('event_title')}”。"
        + (
            f"紧邻的更早候选中，{earlier_summary}，因此不算首次完整兑现。"
            if earlier_summary
            else ""
        )
    )
    payoff.metrics = metrics
    payoff.evidence_ids = [selected_evidence_id]
    payoff.limitations = []
    payoff.status = "SUPPORTED"

    cross_book = items["cross_book_comparison"]
    cross_book.status = "INSUFFICIENT_EVIDENCE"
    cross_book.finding = (
        "当前缺少同品类、同商业模式作品的同口径数据，"
        "不能生成跨书卖点优劣结论。"
    )
    cross_book.metrics = []
    cross_book.evidence_ids = []
    cross_book.limitations = ["当前只能形成单书观察。"]

    answer.status = "PARTIAL"
    answer.conclusion = (
        f"{selling_point.finding} 程序按连续候选账本确定，首次完整兑现"
        f"位于第 {chapter} 章“{chapter_title}”的“"
        f"{selected_candidate.get('event_title')}”。"
    )
    answer.metrics = metrics
    answer.evidence_ids = list(dict.fromkeys([
        *selling_point.evidence_ids,
        *promise_sources.evidence_ids,
        selected_evidence_id,
    ]))[:24]
    answer.limitations = [
        "缺少同品类、同商业模式作品的同口径数据，不能形成跨书比较。",
        (
            f"程序已连续核对选中位置之前的 {selected_sequence - 1} "
            "个候选；后续更强场面不改变“首次”位置。"
        ),
    ]
    answer.reusable_lessons = [
        (
            "先明确一句话卖点的核心异常与主要矛盾，再按原文顺序寻找"
            "第一次同时出现真实机制、主角直接卷入和现实行动的场面；"
            "后续场面更强，不能反向改写首次兑现位置。"
        ),
    ]
    answer.do_not_copy = [
        "不能照搬本书的专有设定、人物、事件或原文表达。",
        (
            f"不能把本书第 {chapter} 章的兑现位置当成通用写作阈值；"
            "其他作品必须按自己的承诺与事件顺序重新核对。"
        ),
    ]

    candidate_artifact["classifications"] = classified_rows
    candidate_artifact["selected_sequence_no"] = selected_sequence
    candidate_artifact["selected_event_id"] = selected_candidate.get(
        "event_id"
    )
    candidate_artifact["selected_evidence_id"] = selected_evidence_id
    candidate_artifact["program_position"] = {
        "chapter_ordinal": chapter,
        "chapter_title": chapter_title,
        "paragraph_number": paragraph_number,
        "source_char_start": source_char_start,
        "promise_chapter_position": promise_chapter,
        "description_source_char_start": description_start,
        "chapter_distance": chapter - promise_chapter,
        "character_distance": source_char_start - description_start,
    }
    candidate_artifact["contract_validation"] = {
        "classification_sequence_contiguous": True,
        "first_complete_selected_by_program": True,
        "position_compiled_by_program": True,
    }
    candidate_artifact["promise_source_contract"] = (
        promise_source_artifact
    )
    _validate_answer_evidence_subset(
        answer,
        allowed,
        error_code="LEARNING_REPORT_1_4_EVIDENCE_SCOPE_INVALID",
    )
    return candidate_artifact


def _validate_1_4_answer_against_projection(
    answer: LearningAnswerProposal,
    projection: dict,
    *,
    opening_promise_sources: dict[str, object] | None = None,
    evidence_by_id: dict[str, EvidenceSpan] | None = None,
    chapter_by_unit_id: dict[str, dict[str, object]] | None = None,
) -> None:
    _program_1_4_answer(
        answer,
        projection,
        opening_promise_sources=opening_promise_sources,
        evidence_by_id=evidence_by_id,
        chapter_by_unit_id=chapter_by_unit_id,
    )


def _normalized_2_2_text(value: object) -> str:
    return re.sub(
        r"[，,。；;！？!?：:“”‘’\"']+",
        "",
        "".join(str(value or "").split()).casefold(),
    )


def _trim_2_2_sentence(value: object) -> str:
    return str(value or "").strip().rstrip("，,。；;！？!?：:")


def _2_2_metric_matches_with_evidence(
    item: LearningContractItemProposal,
    aliases: tuple[str, ...],
    value: int,
    expected_evidence: set[str],
) -> bool:
    return any(
        _metric_value_matches_number(metric, value)
        and set(metric.evidence_ids) == expected_evidence
        for metric in _metrics_with_label(item, aliases)
    )


def _2_2_ordered_nodes_match(
    finding: str,
    nodes: list[dict],
    *,
    detail_keys: tuple[str, ...],
) -> bool:
    chapter_positions: list[int] = []
    cursor = 0
    for node in nodes:
        chapter = int(node.get("chapter_ordinal") or 0)
        if chapter <= 0:
            return False
        match = re.search(
            rf"第\s*{re.escape(str(chapter))}\s*章",
            finding[cursor:],
        )
        if match is None:
            return False
        chapter_start = cursor + match.start()
        chapter_positions.append(chapter_start)
        cursor += match.end()

    for index, node in enumerate(nodes):
        segment_end = (
            chapter_positions[index + 1]
            if index + 1 < len(chapter_positions)
            else len(finding)
        )
        segment = _normalized_2_2_text(
            finding[chapter_positions[index]:segment_end]
        )
        for key in detail_keys:
            expected = _normalized_2_2_text(node.get(key))
            if expected and expected not in segment:
                return False
    return True


def _program_2_2_contract_items(
    ledger: dict,
) -> list[LearningContractItemProposal]:
    field_order = (
        "surface_desire",
        "deep_desire",
        "motivation",
        "contrast",
        "boundary",
        "core_ability",
    )
    fields = {
        str(item.get("field") or ""): item
        for item in ledger.get("fields", [])
        if isinstance(item, dict)
    }
    compiled: list[LearningContractItemProposal] = []
    for field_id in field_order:
        source = fields.get(field_id)
        if not isinstance(source, dict):
            raise ValueError("LEARNING_REPORT_2_2_SOURCE_FIELD_MISSING")
        status = str(source.get("status") or "")
        explanation = _trim_2_2_sentence(source.get("explanation"))
        chapter = int(source.get("first_display_chapter_ordinal") or 0)
        value = _trim_2_2_sentence(source.get("value"))
        display_event = _trim_2_2_sentence(source.get("display_event"))
        evidence_ids = list(dict.fromkeys(
            str(evidence_id)
            for evidence_id in source.get("evidence_ids", [])
            if evidence_id
        ))
        if status == "INSUFFICIENT_EVIDENCE":
            if (
                chapter > 0
                or value
                or display_event
                or evidence_ids
                or source.get("first_display_event_id") is not None
                or not explanation
            ):
                raise ValueError(
                    f"LEARNING_REPORT_2_2_{field_id.upper()}_SOURCE_INVALID"
                )
            compiled.append(LearningContractItemProposal.model_validate({
                "item_id": field_id,
                "status": "INSUFFICIENT_EVIDENCE",
                "finding": explanation,
                "metrics": [],
                "evidence_ids": [],
                "limitations": [explanation],
                "classifications": [],
            }))
            continue
        if (
            status != "SUPPORTED"
            or chapter <= 0
            or not value
            or not display_event
            or not evidence_ids
        ):
            raise ValueError(
                f"LEARNING_REPORT_2_2_{field_id.upper()}_SOURCE_INVALID"
            )
        compiled.append(LearningContractItemProposal.model_validate({
            "item_id": field_id,
            "status": "SUPPORTED",
            "finding": f"第 {chapter} 章，{display_event}：{value}。",
            "metrics": [{
                "label": "首次展示章节",
                "value": str(chapter),
                "unit": "章",
                "method": "由主角专项证据账本确定性定位。",
                "evidence_ids": evidence_ids,
            }],
            "evidence_ids": evidence_ids,
            "limitations": [],
            "classifications": [],
        }))

    conflicts = [
        item
        for item in ledger.get("desire_conflicts", [])
        if isinstance(item, dict)
    ]
    conflicts = [
        item
        for _index, item in sorted(
            enumerate(conflicts),
            key=lambda pair: (
                int(pair[1].get("chapter_ordinal") or 0),
                pair[0],
            ),
        )
    ]
    conflict_evidence_ids = list(dict.fromkeys(
        str(evidence_id)
        for conflict in conflicts
        for evidence_id in conflict.get("evidence_ids", [])
        if evidence_id
    ))
    if any(
        int(conflict.get("chapter_ordinal") or 0) <= 0
        or not str(conflict.get("surface_desire") or "").strip()
        or not str(conflict.get("deep_desire") or "").strip()
        or not str(conflict.get("motive") or "").strip()
        or not str(conflict.get("choice") or "").strip()
        or not str(conflict.get("result") or "").strip()
        or conflict.get("sacrificed_desire") not in {"SURFACE", "DEEP"}
        or not str(conflict.get("sacrifice") or "").strip()
        or not str(conflict.get("arc_change") or "").strip()
        or not conflict.get("motive_evidence_ids")
        or not conflict.get("choice_evidence_ids")
        or not conflict.get("result_evidence_ids")
        or not conflict.get("sacrifice_evidence_ids")
        or not conflict.get("evidence_ids")
        for conflict in conflicts
    ):
        raise ValueError("LEARNING_REPORT_2_2_CONFLICT_SOURCE_INVALID")

    conflict_finding = (
        "".join(
            (
                f"第 {int(conflict['chapter_ordinal'])} 章，"
                f"表层欲望“{_trim_2_2_sentence(conflict['surface_desire'])}”与"
                f"深层欲望“{_trim_2_2_sentence(conflict['deep_desire'])}”发生冲突；"
                f"现场动机原文是“{_trim_2_2_sentence(conflict['motive'])}”；"
                f"主角选择原文是“{_trim_2_2_sentence(conflict['choice'])}”；"
                f"现场结果是{_trim_2_2_sentence(conflict['result'])}，"
                f"并付出代价“{_trim_2_2_sentence(conflict['sacrifice'])}”。"
            )
            for conflict in conflicts
        )
        or "当前专项账本未发现双层欲望冲突节点。"
    )
    arc_finding = (
        "".join(
            (
                f"第 {int(conflict['chapter_ordinal'])} 章，"
                f"{_trim_2_2_sentence(conflict['arc_change'])}。"
            )
            for conflict in conflicts
        )
        or "当前没有已核验冲突节点可形成弧光转折时间轴。"
    )
    for item_id, finding, label in (
        ("desire_conflicts", conflict_finding, "冲突节点"),
        ("arc_timeline", arc_finding, "转折节点"),
    ):
        compiled.append(LearningContractItemProposal.model_validate({
            "item_id": item_id,
            "status": "SUPPORTED",
            "finding": finding,
            "metrics": [{
                "label": label,
                "value": str(len(conflicts)),
                "unit": "个",
                "method": "由主角专项证据账本按章节顺序确定性汇总。",
                "evidence_ids": conflict_evidence_ids,
            }],
            "evidence_ids": conflict_evidence_ids,
            "limitations": [],
            "classifications": [],
        }))
    return compiled


def _apply_program_2_2_answer(
    answer: LearningAnswerProposal,
    ledger: dict,
) -> None:
    field_labels = (
        ("surface_desire", "表层欲望"),
        ("deep_desire", "深层欲望"),
        ("motivation", "动机"),
        ("contrast", "反差"),
        ("boundary", "边界"),
        ("core_ability", "核心能力"),
    )
    answer.contract_items = _program_2_2_contract_items(ledger)
    fields = {
        str(item.get("field") or ""): item
        for item in ledger.get("fields", [])
        if isinstance(item, dict)
    }
    field_summaries: list[str] = []
    insufficient_field_summaries: list[str] = []
    metrics: list[LearningMetricProposal] = []
    representative_evidence: list[str] = []
    for field_id, label in field_labels:
        source = fields[field_id]
        if source.get("status") == "INSUFFICIENT_EVIDENCE":
            explanation = _trim_2_2_sentence(source.get("explanation"))
            insufficient_field_summaries.append(
                f"{label}：{explanation}"
            )
            field_summaries.append(f"{label}证据不足：{explanation}")
            continue
        chapter = int(source["first_display_chapter_ordinal"])
        value = _trim_2_2_sentence(source["value"])
        evidence_ids = list(dict.fromkeys(
            str(evidence_id)
            for evidence_id in source.get("evidence_ids", [])
            if evidence_id
        ))
        field_summaries.append(f"{label}第 {chapter} 章立住：{value}")
        metrics.append(LearningMetricProposal(
            label=f"{label}首次展示章节",
            value=str(chapter),
            unit="章",
            method="由主角专项证据账本确定性定位。",
            evidence_ids=evidence_ids[:16],
        ))
        representative_evidence.extend(evidence_ids)

    conflicts = [
        item
        for item in ledger.get("desire_conflicts", [])
        if isinstance(item, dict)
    ]
    conflicts = [
        item
        for _index, item in sorted(
            enumerate(conflicts),
            key=lambda pair: (
                int(pair[1].get("chapter_ordinal") or 0),
                pair[0],
            ),
        )
    ]
    conflict_evidence = list(dict.fromkeys(
        str(evidence_id)
        for conflict in conflicts
        for evidence_id in conflict.get("evidence_ids", [])
        if evidence_id
    ))
    representative_evidence.extend(conflict_evidence)
    metrics.append(LearningMetricProposal(
        label="双层欲望冲突节点",
        value=str(len(conflicts)),
        unit="个",
        method="由主角专项证据账本按章节顺序确定性汇总。",
        evidence_ids=conflict_evidence[:16],
    ))
    conflict_summary = (
        "、".join(
            f"第 {int(item['chapter_ordinal'])} 章"
            for item in conflicts
        )
        if conflicts
        else "当前专项账本未发现"
    )

    answer.status = (
        "PARTIAL" if insufficient_field_summaries else "ANSWERED"
    )
    answer.conclusion = (
        (
            f"主角双层欲望与最小完整集已有 "
            f"{len(field_labels) - len(insufficient_field_summaries)}/"
            f"{len(field_labels)} 项按首次行动证据定位；"
            f"{len(insufficient_field_summaries)} 项证据不足。"
            if insufficient_field_summaries
            else "主角双层欲望与最小完整集均已按首次行动证据定位。"
        )
        + "；".join(field_summaries)
        + (
            f"。另有 {len(conflicts)} 个双层欲望冲突与弧光转折节点，"
            f"位于{conflict_summary}。"
            if conflicts
            else "。当前专项账本未发现双层欲望冲突与弧光转折节点。"
        )
    )
    answer.metrics = metrics
    answer.evidence_ids = list(dict.fromkeys(
        representative_evidence
    ))[:24]
    answer.counter_evidence_ids = []
    answer.limitations = [
        *insufficient_field_summaries,
        "本结论只覆盖当前单书的主角专项账本，不能外推为其他作品的通用章节阈值。",
    ]
    answer.reusable_lessons = [
        "先分别记录表层欲望、深层欲望、动机、反差、边界和核心能力的首次行动证据，再按冲突节点观察弧光变化。"
    ]
    answer.do_not_copy = [
        "不能照搬本书的欲望内容、人物选择或首次展示章节；其他作品必须按自己的原文证据重新定位。"
    ]


def _program_ratio_text(ratio: float) -> str:
    value = f"{ratio * 100:.2f}".rstrip("0").rstrip(".")
    return f"{value}%"


def _program_hook_label(
    aliases_by_code: dict[str, tuple[str, ...]],
    code: str,
) -> str:
    aliases = aliases_by_code.get(code, (code,))
    return aliases[1] if len(aliases) > 1 else aliases[0]


def _apply_program_4_9_answer(
    answer: LearningAnswerProposal,
    ledger: dict,
) -> None:
    chapters = [
        item
        for item in ledger.get("chapters", [])
        if isinstance(item, dict)
    ]
    coverage = ledger.get("coverage") or {}
    summary = ledger.get("summary") or {}
    all_evidence = list(dict.fromkeys(
        str(evidence_id)
        for chapter in chapters
        for evidence_id in chapter.get("ending_evidence_ids", [])
        if evidence_id
    ))
    source_chapter_count = int(coverage.get("source_chapter_count") or 0)
    window_count = int(coverage.get("window_count") or 0)
    coverage_metrics = [
        LearningMetricProposal(
            label="全书章节",
            value=str(source_chapter_count),
            unit="章",
            method="由全书连续章末钩账本确定性统计。",
            evidence_ids=all_evidence[:16],
        ),
        LearningMetricProposal(
            label="连续窗口",
            value=str(window_count),
            unit="个",
            method="由全书章末钩账本的窗口覆盖记录确定性统计。",
            evidence_ids=all_evidence[:16],
        ),
    ]
    metrics = list(coverage_metrics)

    type_fragments: list[str] = []
    type_metrics: list[LearningMetricProposal] = []
    for row in summary.get("type_distribution", []):
        if not isinstance(row, dict):
            continue
        hook_type = str(row.get("hook_type") or "")
        count = int(row.get("count") or 0)
        ratio = float(row.get("ratio") or 0)
        label = _program_hook_label(
            _CHAPTER_END_HOOK_TYPE_LABELS,
            hook_type,
        )
        evidence_ids = list(dict.fromkeys(
            str(evidence_id)
            for chapter in chapters
            if str(chapter.get("hook_type") or "") == hook_type
            for evidence_id in chapter.get("ending_evidence_ids", [])
            if evidence_id
        ))
        ratio_text = _program_ratio_text(ratio)
        type_fragments.append(f"{label} {count} 章（{ratio_text}）")
        metric = LearningMetricProposal(
            label=label,
            value=str(count),
            unit=f"章，占比 {ratio_text}",
            method="由逐章钩类型账本确定性汇总。",
            evidence_ids=evidence_ids[:16],
        )
        type_metrics.append(metric)
        metrics.append(metric)

    strength_fragments: list[str] = []
    strength_metrics: list[LearningMetricProposal] = []
    for row in summary.get("strength_distribution", []):
        if not isinstance(row, dict):
            continue
        strength = str(row.get("strength") or "")
        count = int(row.get("count") or 0)
        ratio = float(row.get("ratio") or 0)
        label = _program_hook_label(
            _CHAPTER_END_HOOK_STRENGTH_LABELS,
            strength,
        )
        evidence_ids = list(dict.fromkeys(
            str(evidence_id)
            for chapter in chapters
            if str(chapter.get("strength") or "") == strength
            for evidence_id in chapter.get("ending_evidence_ids", [])
            if evidence_id
        ))
        ratio_text = _program_ratio_text(ratio)
        strength_fragments.append(f"{label} {count} 章（{ratio_text}）")
        metric = LearningMetricProposal(
            label=label,
            value=str(count),
            unit=f"章，占比 {ratio_text}",
            method="由逐章钩强度账本确定性汇总。",
            evidence_ids=evidence_ids[:16],
        )
        strength_metrics.append(metric)
        metrics.append(metric)

    max_consecutive_strong = int(
        summary.get("max_consecutive_strong") or 0
    )
    type_transition_count = int(
        summary.get("type_transition_count") or 0
    )
    max_consecutive_same_type = int(
        summary.get("max_consecutive_same_type") or 0
    )
    no_hook_count = int(summary.get("no_hook_count") or 0)
    no_hook_ratio = float(summary.get("no_hook_ratio") or 0)
    no_hook_evidence = list(dict.fromkeys(
        str(evidence_id)
        for chapter in chapters
        if str(chapter.get("hook_type") or "") == "NONE"
        for evidence_id in chapter.get("ending_evidence_ids", [])
        if evidence_id
    ))
    strong_run_metric = LearningMetricProposal(
        label="最长连续强钩",
        value=str(max_consecutive_strong),
        unit="章",
        method="由连续章节强度序列确定性合并。",
        evidence_ids=all_evidence[:16],
    )
    transition_metric = LearningMetricProposal(
        label="类型切换",
        value=str(type_transition_count),
        unit="次",
        method="由相邻章节钩类型序列确定性比较。",
        evidence_ids=all_evidence[:16],
    )
    same_type_metric = LearningMetricProposal(
        label="同类连续上限",
        value=str(max_consecutive_same_type),
        unit="章",
        method="由连续章节钩类型序列确定性合并。",
        evidence_ids=all_evidence[:16],
    )
    no_hook_metric = LearningMetricProposal(
        label="无钩章",
        value=str(no_hook_count),
        unit=f"章，占比 {_program_ratio_text(no_hook_ratio)}",
        method="由逐章 NONE 分类确定性统计。",
        evidence_ids=no_hook_evidence[:16],
    )
    strength_metrics.append(strong_run_metric)
    metrics.extend([
        strong_run_metric,
        transition_metric,
        same_type_metric,
        no_hook_metric,
    ])

    example_fragments: list[str] = []
    example_evidence: list[str] = []
    examples_by_type = summary.get("examples_by_type") or {}
    for row in summary.get("type_distribution", []):
        if not isinstance(row, dict) or int(row.get("count") or 0) <= 0:
            continue
        hook_type = str(row.get("hook_type") or "")
        examples = [
            item
            for item in examples_by_type.get(hook_type, [])
            if isinstance(item, dict)
        ]
        if not examples:
            raise ValueError("LEARNING_REPORT_4_9_TYPE_EXAMPLES_INCOMPLETE")
        example = examples[0]
        evidence_ids = [
            str(evidence_id)
            for evidence_id in example.get("ending_evidence_ids", [])
            if evidence_id
        ]
        if not evidence_ids:
            raise ValueError("LEARNING_REPORT_4_9_TYPE_EXAMPLES_INCOMPLETE")
        label = _program_hook_label(
            _CHAPTER_END_HOOK_TYPE_LABELS,
            hook_type,
        )
        example_fragments.append(
            f"{label}：第 {int(example.get('chapter_ordinal') or 0)} 章"
            f"“{str(example.get('chapter_title') or '')}”"
        )
        example_evidence.extend(evidence_ids)
    example_evidence = list(dict.fromkeys(example_evidence))

    answer.contract_items = [
        LearningContractItemProposal(
            item_id="chapter_coverage",
            status="SUPPORTED",
            finding=(
                f"全书 {source_chapter_count} 章由 {window_count} 个连续窗口"
                "不重不漏覆盖。"
            ),
            metrics=coverage_metrics,
            evidence_ids=all_evidence[:32],
            limitations=[],
        ),
        LearningContractItemProposal(
            item_id="type_distribution",
            status="SUPPORTED",
            finding=f"类型配比：{'；'.join(type_fragments)}。",
            metrics=type_metrics,
            evidence_ids=all_evidence[:32],
            limitations=[],
        ),
        LearningContractItemProposal(
            item_id="strength_rhythm",
            status="SUPPORTED",
            finding=(
                f"强弱配比：{'；'.join(strength_fragments)}；"
                f"最长连续强钩 {max_consecutive_strong} 章。"
            ),
            metrics=strength_metrics,
            evidence_ids=all_evidence[:32],
            limitations=[],
        ),
        LearningContractItemProposal(
            item_id="type_rotation",
            status="SUPPORTED",
            finding=(
                f"相邻章节共切换类型 {type_transition_count} 次，"
                f"同类连续上限为 {max_consecutive_same_type} 章。"
            ),
            metrics=[transition_metric, same_type_metric],
            evidence_ids=all_evidence[:32],
            limitations=[],
        ),
        LearningContractItemProposal(
            item_id="no_hook_analysis",
            status="SUPPORTED",
            finding=(
                f"无钩章共 {no_hook_count} 章，占全书"
                f" {_program_ratio_text(no_hook_ratio)}。"
            ),
            metrics=[no_hook_metric],
            evidence_ids=no_hook_evidence[:32],
            limitations=[],
        ),
        LearningContractItemProposal(
            item_id="representative_examples",
            status="SUPPORTED",
            finding=(
                "每种实际出现类型均保留一条章末原文实例："
                + "；".join(example_fragments)
                + "。"
            ),
            metrics=[],
            evidence_ids=example_evidence[:32],
            limitations=[],
        ),
        LearningContractItemProposal(
            item_id="scope_boundary",
            status="SUPPORTED",
            finding=(
                "4.9 只统计全书章末钩类型与强弱节律，不追踪后续回应；"
                "前三章首次回应属于 3.4，重要悬念生命周期属于 4.10。"
            ),
            metrics=[],
            evidence_ids=[],
            limitations=[],
        ),
    ]

    answer.status = "ANSWERED"
    answer.conclusion = (
        f"全书 {source_chapter_count} 章由 {window_count} 个连续窗口"
        f"不重不漏覆盖。类型配比：{'；'.join(type_fragments)}。"
        f"强弱配比：{'；'.join(strength_fragments)}。"
        f"最长连续强钩 {max_consecutive_strong} 章，"
        f"类型切换 {type_transition_count} 次，"
        f"同类连续上限 {max_consecutive_same_type} 章，"
        f"无钩章 {no_hook_count} 章"
        f"（{_program_ratio_text(no_hook_ratio)}）。"
    )
    answer.metrics = metrics
    answer.evidence_ids = all_evidence[:24]
    answer.counter_evidence_ids = []
    answer.limitations = [
        "4.9 只统计全书章末钩类型与强弱节律，不追踪后续回应；前三章首次回应属于 3.4，重要悬念生命周期属于 4.10。"
    ]
    answer.reusable_lessons = [
        "先把章末类型与强度分开逐章记账，再核对类型切换、连续强钩、同类连续段和无钩章，不用单个强场面代替全书节律。"
    ]
    answer.do_not_copy = [
        "不能照搬本书的类型比例、强度序列或具体章末表达；其他作品必须按自己的全书章节重新统计。"
    ]


def _validate_2_2_answer_against_projection(
    answer: LearningAnswerProposal,
    projection: dict,
) -> None:
    ledger = projection.get("character_design_evidence") or {}
    allowed = _evidence_ids(ledger)
    _validate_answer_evidence_subset(
        answer,
        allowed,
        error_code="LEARNING_REPORT_2_2_EVIDENCE_SCOPE_INVALID",
    )
    items = _contract_item_by_id(answer)
    fields = {
        str(item.get("field") or ""): item
        for item in ledger.get("fields", [])
        if isinstance(item, dict)
    }
    for field_id in (
        "surface_desire",
        "deep_desire",
        "motivation",
        "contrast",
        "boundary",
        "core_ability",
    ):
        source = fields.get(field_id)
        item = items[field_id]
        if not isinstance(source, dict):
            raise ValueError("LEARNING_REPORT_2_2_SOURCE_FIELD_MISSING")
        source_status = str(source.get("status") or "")
        expected_evidence = _evidence_ids(source)
        actual_evidence = set(item.evidence_ids)
        if source_status == "INSUFFICIENT_EVIDENCE":
            explanation = _normalized_2_2_text(source.get("explanation"))
            if (
                source.get("first_display_chapter_ordinal") is not None
                or source.get("first_display_event_id") is not None
                or str(source.get("value") or "").strip()
                or str(source.get("display_event") or "").strip()
                or expected_evidence
                or not explanation
            ):
                raise ValueError(
                    f"LEARNING_REPORT_2_2_{field_id.upper()}_SOURCE_INVALID"
                )
            if (
                item.status != "INSUFFICIENT_EVIDENCE"
                or actual_evidence
                or item.metrics
                or item.classifications
                or item.payoff_classifications
                or _normalized_2_2_text(item.finding) != explanation
                or [_normalized_2_2_text(value) for value in item.limitations]
                != [explanation]
            ):
                raise ValueError(
                    f"LEARNING_REPORT_2_2_{field_id.upper()}_REFERENCE_INVALID"
                )
            continue
        expected_chapter = int(
            source.get("first_display_chapter_ordinal") or 0
        )
        finding = _normalized_2_2_text(item.finding)
        expected_value = _normalized_2_2_text(source.get("value"))
        expected_event = _normalized_2_2_text(source.get("display_event"))
        if (
            source_status != "SUPPORTED"
            or item.status != "SUPPORTED"
            or not actual_evidence
            or actual_evidence != expected_evidence
            or expected_chapter <= 0
            or re.search(
                rf"第\s*{re.escape(str(expected_chapter))}\s*章",
                item.finding,
            )
            is None
            or (expected_value and expected_value not in finding)
            or (expected_event and expected_event not in finding)
            or not _2_2_metric_matches_with_evidence(
                item,
                ("首次展示章节", "展示章节"),
                expected_chapter,
                expected_evidence,
            )
        ):
            raise ValueError(
                f"LEARNING_REPORT_2_2_{field_id.upper()}_REFERENCE_INVALID"
            )
    conflicts = [
        item
        for item in ledger.get("desire_conflicts", [])
        if isinstance(item, dict)
    ]
    conflicts = [
        item
        for _index, item in sorted(
            enumerate(conflicts),
            key=lambda pair: (
                int(pair[1].get("chapter_ordinal") or 0),
                pair[0],
            ),
        )
    ]
    conflict_evidence = _evidence_ids(conflicts)
    conflict_item = items["desire_conflicts"]
    if (
        conflict_item.status != "SUPPORTED"
        or not _metric_group_matches_count(
            conflict_item,
            ("冲突节点", "转折节点"),
            len(conflicts),
        )
    ):
        raise ValueError("LEARNING_REPORT_2_2_CONFLICT_COUNT_INVALID")
    if (
        set(conflict_item.evidence_ids) != conflict_evidence
        or not _2_2_metric_matches_with_evidence(
            conflict_item,
            ("冲突节点", "转折节点"),
            len(conflicts),
            conflict_evidence,
        )
        or not _2_2_ordered_nodes_match(
            conflict_item.finding,
            conflicts,
            detail_keys=(
                "surface_desire",
                "deep_desire",
                "motive",
                "choice",
                "result",
                "sacrifice",
            ),
        )
    ):
        raise ValueError("LEARNING_REPORT_2_2_CONFLICT_NODES_INVALID")
    arc_item = items["arc_timeline"]
    if (
        arc_item.status != "SUPPORTED"
        or set(arc_item.evidence_ids) != conflict_evidence
        or not _2_2_metric_matches_with_evidence(
            arc_item,
            ("冲突节点", "转折节点"),
            len(conflicts),
            conflict_evidence,
        )
        or not _2_2_ordered_nodes_match(
            arc_item.finding,
            conflicts,
            detail_keys=("arc_change",),
        )
    ):
        raise ValueError("LEARNING_REPORT_2_2_ARC_TIMELINE_INVALID")


def _validate_4_9_answer_against_projection(
    answer: LearningAnswerProposal,
    projection: dict,
) -> None:
    ledger = projection.get("chapter_end_hooks_evidence") or {}
    chapters = [
        item
        for item in ledger.get("chapters", [])
        if isinstance(item, dict)
    ]
    ending_evidence = {
        str(evidence_id)
        for item in chapters
        for evidence_id in item.get("ending_evidence_ids", [])
        if evidence_id
    }
    _validate_answer_evidence_subset(
        answer,
        ending_evidence,
        error_code="LEARNING_REPORT_4_9_EVIDENCE_SCOPE_INVALID",
    )
    items = _contract_item_by_id(answer)
    summary = ledger.get("summary") or {}
    coverage = ledger.get("coverage") or {}
    chapter_coverage = items["chapter_coverage"]
    source_chapter_count = int(coverage.get("source_chapter_count") or 0)
    window_count = int(coverage.get("window_count") or 0)
    if (
        not _metric_group_matches_count(
            chapter_coverage,
            ("全书章节", "覆盖章节", "章节覆盖"),
            source_chapter_count,
        )
        or not _metric_group_matches_count(
            chapter_coverage,
            ("连续窗口", "窗口数"),
            window_count,
        )
    ):
        raise ValueError("LEARNING_REPORT_4_9_COVERAGE_METRIC_INVALID")

    type_distribution = items["type_distribution"]
    type_rows = [
        row
        for row in summary.get("type_distribution", [])
        if isinstance(row, dict)
    ]
    if not type_rows or any(
        not _metric_group_matches_count(
            type_distribution,
            _CHAPTER_END_HOOK_TYPE_LABELS.get(
                str(row.get("hook_type") or ""),
                (str(row.get("hook_type") or ""),),
            ),
            int(row.get("count") or 0),
        )
        or not _metric_group_matches_ratio(
            type_distribution,
            _CHAPTER_END_HOOK_TYPE_LABELS.get(
                str(row.get("hook_type") or ""),
                (str(row.get("hook_type") or ""),),
            ),
            float(row.get("ratio") or 0),
        )
        for row in type_rows
    ):
        raise ValueError("LEARNING_REPORT_4_9_TYPE_DISTRIBUTION_INVALID")

    strength_rhythm = items["strength_rhythm"]
    strength_rows = [
        row
        for row in summary.get("strength_distribution", [])
        if isinstance(row, dict)
    ]
    max_consecutive_strong = int(
        summary.get("max_consecutive_strong") or 0
    )
    if any(
        not _metric_group_matches_count(
            strength_rhythm,
            _CHAPTER_END_HOOK_STRENGTH_LABELS.get(
                str(row.get("strength") or ""),
                (str(row.get("strength") or ""),),
            ),
            int(row.get("count") or 0),
        )
        for row in strength_rows
    ) or not _metric_group_matches_count(
        strength_rhythm,
        ("最长连续强钩",),
        max_consecutive_strong,
    ):
        raise ValueError("LEARNING_REPORT_4_9_STRENGTH_RHYTHM_INVALID")

    type_rotation = items["type_rotation"]
    if not _metric_group_matches_count(
        type_rotation,
        ("类型切换", "切换次数"),
        int(summary.get("type_transition_count") or 0),
    ) or not _metric_group_matches_count(
        type_rotation,
        ("同类连续上限", "同类型连续上限"),
        int(summary.get("max_consecutive_same_type") or 0),
    ):
        raise ValueError("LEARNING_REPORT_4_9_TYPE_ROTATION_INVALID")

    no_hook = items["no_hook_analysis"]
    no_hook_ratio = float(summary.get("no_hook_ratio") or 0)
    if (
        not _metric_group_matches_count(
            no_hook,
            ("无钩章", "无钩"),
            int(summary.get("no_hook_count") or 0),
        )
        or not _metric_group_matches_ratio(
            no_hook,
            ("无钩章", "无钩"),
            no_hook_ratio,
        )
    ):
        raise ValueError("LEARNING_REPORT_4_9_NO_HOOK_METRIC_INVALID")

    examples = items["representative_examples"]
    example_evidence = _evidence_ids(examples.model_dump(mode="json"))
    examples_by_type = summary.get("examples_by_type") or {}
    if any(
        not example_evidence.intersection(_evidence_ids(type_examples))
        for type_examples in examples_by_type.values()
        if type_examples
    ):
        raise ValueError("LEARNING_REPORT_4_9_TYPE_EXAMPLES_INCOMPLETE")

    scope_finding = items["scope_boundary"].finding
    if (
        "3.4" not in scope_finding
        or "4.10" not in scope_finding
        or not re.search(r"(?:不|不得|不再).{0,8}(?:回应|回收|追踪)", scope_finding)
    ):
        raise ValueError("LEARNING_REPORT_4_9_SCOPE_BOUNDARY_INVALID")
    if _has_out_of_scope_4_9_response_distance(answer):
        raise ValueError("LEARNING_REPORT_4_9_RESPONSE_METRIC_OUT_OF_SCOPE")


def _validate_selected_answers_against_projection(
    output: LearningReportOutput,
    projection: dict,
    *,
    opening_promise_sources: dict[str, object] | None = None,
    evidence_by_id: dict[str, EvidenceSpan] | None = None,
    chapter_by_unit_id: dict[str, dict[str, object]] | None = None,
) -> None:
    for answer in output.answers:
        if answer.question_id == "1.4":
            _validate_1_4_answer_against_projection(
                answer,
                projection,
                opening_promise_sources=opening_promise_sources,
                evidence_by_id=evidence_by_id,
                chapter_by_unit_id=chapter_by_unit_id,
            )
        elif answer.question_id == "2.2":
            _validate_2_2_answer_against_projection(answer, projection)
        elif answer.question_id == "4.9":
            _validate_4_9_answer_against_projection(answer, projection)


def persist_learning_report(
    session: Session,
    *,
    settings: Settings,
    task: Task,
    attempt_id: str,
    task_payload: dict,
    output: LearningReportOutput,
) -> PersistedLearningReport:
    run = session.get(AnalysisRun, task_payload.get("run_id"))
    if run is None:
        raise ValueError("ANALYSIS_RUN_NOT_FOUND")
    deep = session.scalar(
        select(DeepAnalysis)
        .where(DeepAnalysis.run_id == run.id)
        .order_by(DeepAnalysis.revision_no.desc())
    )
    source_deep_revision = int(task_payload.get("source_deep_revision") or 0)
    if deep is None or deep.revision_no != source_deep_revision:
        raise ValueError("LEARNING_REPORT_SOURCE_OUTDATED")
    selected_question_ids = tuple(
        str(question_id) for question_id in task_payload.get("question_ids", [])
    )
    selected_scopes = task_payload.get("question_answer_scopes") or {}
    for answer in output.answers:
        if (
            selected_scopes.get(answer.question_id) == "PARTIAL"
            and answer.status == "ANSWERED"
        ):
            raise ValueError("LEARNING_REPORT_PARTIAL_SCOPE_VIOLATION")
        if (
            selected_scopes.get(answer.question_id) == "COMPLETE"
            and answer.status != "ANSWERED"
        ):
            raise ValueError("LEARNING_REPORT_COMPLETE_SCOPE_VIOLATION")

    projection: dict | None = None
    program_1_4_artifact: dict[str, object] | None = None
    if set(selected_question_ids).intersection({"1.4", "2.1", "2.2", "4.9"}):
        from .workbench import build_workbench_projection

        projection = build_workbench_projection(session, run.id)
    if projection is not None:
        opening_promise_sources: dict[str, object] | None = None
        validation_evidence_by_id: dict[str, EvidenceSpan] = {}
        validation_chapter_by_unit_id: dict[
            str, dict[str, object]
        ] = {}
        if "1.4" in selected_question_ids:
            opening_promise_sources = _opening_promise_source_artifact(
                session,
                settings,
                run.source_version,
            )
            validation_evidence_ids = _evidence_ids({
                "opening_promise_sources": opening_promise_sources,
                "opening_events": projection.get("events", []),
            })
            validation_evidence_by_id = {
                evidence.id: evidence
                for evidence in session.scalars(
                    select(EvidenceSpan).where(
                        EvidenceSpan.source_version_id == run.source_version_id,
                        EvidenceSpan.id.in_(validation_evidence_ids),
                    )
                )
            }
            validation_chapter_by_unit_id = _chapter_index_by_unit_id(
                session,
                run.source_version_id,
            )
            answer_1_4 = next(
                answer
                for answer in output.answers
                if answer.question_id == "1.4"
            )
            program_1_4_artifact = _program_1_4_answer(
                answer_1_4,
                projection,
                opening_promise_sources=opening_promise_sources,
                evidence_by_id=validation_evidence_by_id,
                chapter_by_unit_id=validation_chapter_by_unit_id,
            )
        if "2.2" in selected_question_ids:
            character_design = (
                projection.get("character_design_evidence") or {}
            )
            for answer in output.answers:
                if answer.question_id == "2.2":
                    _apply_program_2_2_answer(answer, character_design)
        if "4.9" in selected_question_ids:
            chapter_end_hooks = (
                projection.get("chapter_end_hooks_evidence") or {}
            )
            for answer in output.answers:
                if answer.question_id == "4.9":
                    _apply_program_4_9_answer(answer, chapter_end_hooks)
        _validate_selected_answers_against_projection(
            output,
            projection,
            opening_promise_sources=opening_promise_sources,
            evidence_by_id=validation_evidence_by_id,
            chapter_by_unit_id=validation_chapter_by_unit_id,
        )
    for answer in output.answers:
        _validate_answer_user_text_boundaries(answer)

    existing = session.scalar(
        select(LearningReport).where(LearningReport.created_by_task_id == task.id)
    )
    previous_report = session.scalar(
        select(LearningReport)
        .where(
            LearningReport.run_id == run.id,
            LearningReport.created_by_task_id != task.id,
        )
        .order_by(LearningReport.revision_no.desc())
    )
    previous_payload: dict[str, object] = {}
    if (
        previous_report is not None
        and previous_report.source_deep_revision == source_deep_revision
        and _report_uses_current_contract(previous_report)
    ):
        previous_payload = json.loads(previous_report.payload_json)
    current_fingerprints = task_payload.get("current_question_source_fingerprints") or {}
    previous_fingerprints = previous_payload.get("question_source_fingerprints") or {}
    merged_answers = {
        str(item["question_id"]): item
        for item in previous_payload.get("answers", [])
        if (
            isinstance(item, dict)
            and item.get("question_id") not in selected_question_ids
            and _answer_uses_current_contract(
                previous_payload,
                str(item.get("question_id")),
            )
            and previous_fingerprints.get(str(item.get("question_id")))
            == current_fingerprints.get(str(item.get("question_id")))
        )
    }
    merged_answers.update({
        answer.question_id: answer.model_dump(mode="json") for answer in output.answers
    })
    ordered_answer_ids = [
        item.question_id
        for item in LEARNING_QUESTION_CATALOG
        if item.question_id in merged_answers
    ]
    payload = {
        "answers": [merged_answers[question_id] for question_id in ordered_answer_ids],
        "author_decisions": output.model_dump(mode="json")["author_decisions"],
        "method_candidates": output.model_dump(mode="json")["method_candidates"],
    }
    if "1.4" in selected_question_ids:
        if program_1_4_artifact is None:
            raise ValueError("LEARNING_REPORT_PROGRAM_PROJECTION_MISSING")
        persisted_answer = next(
            answer
            for answer in payload["answers"]
            if answer["question_id"] == "1.4"
        )
        persisted_answer["program_artifacts"] = {
            "opening_payoff_candidate_ledger": program_1_4_artifact,
        }
    if "2.1" in selected_question_ids:
        if projection is None:
            raise ValueError("LEARNING_REPORT_PROGRAM_PROJECTION_MISSING")
        answer_model = next(
            answer for answer in output.answers if answer.question_id == "2.1"
        )
        artifact, program_metrics = _validated_2_1_program_artifact(
            answer_model,
            projection,
        )
        persisted_answer = next(
            answer
            for answer in payload["answers"]
            if answer["question_id"] == "2.1"
        )
        persisted_answer["program_artifacts"] = {
            "opening_character_ledger": artifact,
        }
        persisted_answer["contract_items"] = _program_2_1_contract_items(
            artifact
        )
        persisted_answer["evidence_ids"] = (
            _program_2_1_representative_evidence_ids(artifact)
        )
        persisted_answer["metrics"] = program_metrics
        persisted_answer.update(_program_2_1_reading_fields(artifact))
    referenced_evidence_ids = _evidence_ids(payload)
    valid_evidence_ids = set(session.scalars(
        select(EvidenceSpan.id).where(
            EvidenceSpan.source_version_id == run.source_version_id,
            EvidenceSpan.id.in_(referenced_evidence_ids),
        )
    ))
    if referenced_evidence_ids != valid_evidence_ids:
        raise ValueError("LEARNING_REPORT_EVIDENCE_REFERENCE_INVALID")
    for answer in payload["answers"]:
        if answer["status"] in {"ANSWERED", "PARTIAL"}:
            if not answer["evidence_ids"]:
                raise ValueError("LEARNING_REPORT_ANSWER_EVIDENCE_MISSING")
            if not answer["metrics"]:
                raise ValueError("LEARNING_REPORT_METRIC_MISSING")
    payload.update({
        "catalog_version": LEARNING_QUESTION_CATALOG_VERSION,
        "batch_label": LEARNING_REPORT_BATCH_LABEL,
        "generated_question_ids": ordered_answer_ids,
        "question_source_fingerprints": {
            question_id: current_fingerprints[question_id]
            for question_id in ordered_answer_ids
        },
        "question_contract_versions": {
            question_id: LEARNING_QUESTION_CONTRACT_VERSIONS.get(
                question_id,
                "1.0.0",
            )
            for question_id in ordered_answer_ids
        },
        "source_deep_revision": source_deep_revision,
    })
    payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    if existing is None:
        revision_no = (session.scalar(
            select(func.max(LearningReport.revision_no)).where(LearningReport.run_id == run.id)
        ) or 0) + 1
        existing = LearningReport(
            run_id=run.id,
            source_version_id=run.source_version_id,
            revision_no=revision_no,
            source_deep_revision=source_deep_revision,
            payload_json=payload_json,
            prompt_id=LEARNING_REPORT_PROMPT_ID,
            prompt_version=LEARNING_REPORT_PROMPT_VERSION,
            created_by_task_id=task.id,
            created_by_attempt_id=attempt_id,
        )
        session.add(existing)
    else:
        existing.payload_json = payload_json
        existing.created_by_attempt_id = attempt_id
    session.commit()
    session.refresh(existing)
    return PersistedLearningReport(existing.id)


def enqueue_learning_report(
    session: Session,
    settings: Settings,
    run: AnalysisRun,
    *,
    force: bool = False,
    only_question_ids: tuple[str, ...] | None = None,
) -> Task | None:
    deep = session.scalar(
        select(DeepAnalysis)
        .where(DeepAnalysis.run_id == run.id)
        .order_by(DeepAnalysis.revision_no.desc())
    )
    if deep is None:
        return None
    from .workbench import build_workbench_projection

    projection = build_workbench_projection(session, run.id)
    payoff_task: Task | None = None
    if (
        only_question_ids is None
        or "1.4" in only_question_ids
    ) and projection.get(
        "opening_payoff_candidates_status"
    ) != "READY":
        from .opening_payoff_candidates import (
            enqueue_opening_payoff_candidates,
        )

        payoff_task = enqueue_opening_payoff_candidates(
            session,
            settings,
            run,
            force=force,
        )
        session.refresh(run)
        projection = build_workbench_projection(session, run.id)
    readiness = assess_learning_report_readiness(projection)
    checks_by_id = {
        str(item["question_id"]): item for item in readiness["checks"]
    }
    eligible_question_ids = [
        question_id
        for question_id in INITIAL_INCREMENTAL_QUESTION_IDS
        if (
            checks_by_id[question_id]["ready"]
            and (
                only_question_ids is None
                or question_id in only_question_ids
            )
        )
    ]
    if not eligible_question_ids:
        if payoff_task is not None:
            return payoff_task
        raise LearningReportNotReadyError(readiness)
    latest_report = session.scalar(
        select(LearningReport)
        .where(LearningReport.run_id == run.id)
        .order_by(LearningReport.revision_no.desc())
    )
    active_tasks = list(session.scalars(
        select(Task)
        .join(AnalysisRunTask, AnalysisRunTask.task_id == Task.id)
        .where(
            AnalysisRunTask.run_id == run.id,
            Task.kind == LEARNING_REPORT_TASK_KIND,
            Task.status.in_((
                TaskStatus.PENDING.value,
                TaskStatus.RUNNING.value,
                TaskStatus.RETRY_WAIT.value,
                TaskStatus.WAITING_CONFIRMATION.value,
            )),
        )
        .order_by(AnalysisRunTask.batch_index)
    ))
    active_question_ids = {
        str(question_id)
        for active_task in active_tasks
        for question_id in (
            json.loads(active_task.payload_json).get("question_ids")
            or []
        )
    }
    current_fingerprints = {
        question_id: str(checks_by_id[question_id]["source_fingerprint"])
        for question_id in INITIAL_INCREMENTAL_QUESTION_IDS
    }
    current_answer_ids: set[str] = set()
    if (
        latest_report is not None
        and latest_report.source_deep_revision == deep.revision_no
        and _report_uses_current_contract(latest_report)
    ):
        latest_payload = json.loads(latest_report.payload_json)
        report_fingerprints = latest_payload.get("question_source_fingerprints") or {}
        current_answer_ids = {
            str(item.get("question_id"))
            for item in latest_payload.get("answers", [])
            if (
                isinstance(item, dict)
                and _answer_uses_current_contract(
                    latest_payload,
                    str(item.get("question_id")),
                )
                and report_fingerprints.get(str(item.get("question_id")))
                == current_fingerprints.get(str(item.get("question_id")))
            )
        }
    selected_question_ids = (
        [
            question_id
            for question_id in eligible_question_ids
            if question_id not in active_question_ids
        ]
        if force
        else [
            question_id
            for question_id in eligible_question_ids
            if (
                question_id not in current_answer_ids
                and question_id not in active_question_ids
            )
        ]
    )
    if not selected_question_ids:
        return payoff_task or (active_tasks[0] if active_tasks else None)
    try:
        _service, profile = resolve_analysis_profile(settings, ENTITIES_EVENTS_PROFILE_ID)
    except ModelSettingsError:
        return None
    next_index = (
        max((link.batch_index for link in run.task_links), default=run.total_batches)
        + 1
    )
    created_tasks: list[Task] = []
    for offset, question_id in enumerate(selected_question_ids):
        task_payload, max_attempts = prepare_task_provider_routes(
            settings,
            {
                "run_id": run.id,
                "source_version_id": run.source_version_id,
                "source_deep_revision": deep.revision_no,
                "provider_name": "openai",
                "model_profile_id": profile.id,
                "question_ids": [question_id],
                "question_answer_scopes": {
                    question_id: checks_by_id[question_id]["answer_scope"],
                },
                "question_source_fingerprints": {
                    question_id: current_fingerprints[question_id],
                },
                "current_question_source_fingerprints": current_fingerprints,
                "catalog_version": LEARNING_QUESTION_CATALOG_VERSION,
                "answer_task_policy": "ONE_QUESTION_PER_MODEL_REQUEST",
            },
            profile.max_retries + 1,
        )
        task = Task(
            project_id=run.source_version.document.project_id,
            kind=LEARNING_REPORT_TASK_KIND,
            payload_json=json.dumps(task_payload, ensure_ascii=False, sort_keys=True),
            max_attempts=max_attempts,
        )
        session.add(task)
        session.flush()
        session.add(AnalysisRunTask(
            run_id=run.id,
            task_id=task.id,
            batch_index=next_index + offset,
        ))
        created_tasks.append(task)
    run.total_batches = next_index + len(created_tasks) - 1
    run.status = AnalysisRunStatus.PENDING.value
    session.commit()
    session.refresh(created_tasks[0])
    return payoff_task or created_tasks[0]


def build_learning_report_projection(
    session: Session,
    run_id: str,
    *,
    latest_deep_revision: int | None,
    readiness: dict[str, object] | None = None,
) -> tuple[str, dict[str, object]]:
    report = session.scalar(
        select(LearningReport)
        .where(LearningReport.run_id == run_id)
        .order_by(LearningReport.revision_no.desc())
    )
    latest_task = session.scalar(
        select(Task)
        .join(AnalysisRunTask, AnalysisRunTask.task_id == Task.id)
        .where(
            AnalysisRunTask.run_id == run_id,
            Task.kind == LEARNING_REPORT_TASK_KIND,
        )
        .order_by(AnalysisRunTask.batch_index.desc())
    )
    active_task = session.scalar(
        select(Task)
        .join(AnalysisRunTask, AnalysisRunTask.task_id == Task.id)
        .where(
            AnalysisRunTask.run_id == run_id,
            Task.kind == LEARNING_REPORT_TASK_KIND,
            Task.status.in_((
                TaskStatus.PENDING.value,
                TaskStatus.RUNNING.value,
                TaskStatus.RETRY_WAIT.value,
                TaskStatus.WAITING_CONFIRMATION.value,
            )),
        )
        .order_by(AnalysisRunTask.batch_index)
    )
    payload = json.loads(report.payload_json) if report is not None else {}
    report_base_is_current = bool(
        report is not None
        and report.source_deep_revision == latest_deep_revision
        and _report_uses_current_contract(report)
    )
    readiness_by_id = {
        str(item["question_id"]): item
        for item in (readiness or {}).get("checks", [])
    }
    report_fingerprints = payload.get("question_source_fingerprints") or {}
    current_answer_ids = {
        str(item.get("question_id"))
        for item in payload.get("answers", [])
        if (
            report_base_is_current
            and isinstance(item, dict)
            and _answer_uses_current_contract(
                payload,
                str(item.get("question_id")),
            )
            and report_fingerprints.get(str(item.get("question_id")))
            == readiness_by_id.get(str(item.get("question_id")), {}).get(
                "source_fingerprint"
            )
        )
    }
    pending_question_ids = {
        question_id
        for question_id, item in readiness_by_id.items()
        if item.get("ready") and question_id not in current_answer_ids
    }
    if active_task is not None:
        status = "GENERATING"
    elif current_answer_ids and not pending_question_ids:
        status = "READY"
    elif report is not None:
        status = "OUTDATED"
    elif latest_task is not None and latest_task.status == TaskStatus.FAILED.value:
        status = "FAILED"
    else:
        status = "NOT_GENERATED"
    answer_by_id = {
        item["question_id"]: item
        for item in payload.get("answers", [])
        if item.get("question_id") in _QUESTION_BY_ID
    }
    questions: list[dict[str, object]] = []
    for definition in LEARNING_QUESTION_CATALOG:
        answer = answer_by_id.get(definition.question_id, {})
        has_current_answer = definition.question_id in current_answer_ids
        if has_current_answer:
            question_status = answer.get("status", "NOT_GENERATED")
        elif answer:
            question_status = "OUTDATED"
        elif readiness_by_id.get(definition.question_id, {}).get("ready"):
            question_status = "READY_TO_GENERATE"
        else:
            question_status = "NOT_GENERATED"
        questions.append({
            "question_id": definition.question_id,
            "stage_id": definition.stage_id,
            "stage_name": STAGE_NAMES[definition.stage_id],
            "question": definition.question,
            "priority": "CORE",
            "analysis_requirement": definition.analysis_requirement,
            "evidence_view": definition.evidence_view,
            "output_contract": definition.output_contract,
            "measurement_requirements": definition.measurement_requirements,
            "evidence_requirements": definition.evidence_requirements,
            "scope_requirement": definition.scope_requirement,
            "external_data_policy": definition.external_data_policy,
            "recommended_rank": _RECOMMENDED_RANK.get(definition.question_id),
            "status": question_status,
            "conclusion": answer.get("conclusion", ""),
            "metrics": answer.get("metrics", []),
            "contract_items": [
                {
                    **item,
                    "label": next(
                        (
                            definition_item.label
                            for definition_item in LEARNING_QUESTION_ITEM_CONTRACTS.get(
                                definition.question_id,
                                (),
                            )
                            if definition_item.item_id == item.get("item_id")
                        ),
                        str(item.get("item_id") or ""),
                    ),
                }
                for item in answer.get("contract_items", [])
                if isinstance(item, dict)
            ],
            "program_artifacts": answer.get("program_artifacts", {}),
            "evidence_ids": answer.get("evidence_ids", []),
            "counter_evidence_ids": answer.get("counter_evidence_ids", []),
            "limitations": answer.get("limitations", []),
            "reusable_lessons": answer.get("reusable_lessons", []),
            "do_not_copy": answer.get("do_not_copy", []),
            "has_current_answer": has_current_answer,
        })
    stages: list[dict[str, object]] = []
    for stage_id, stage_name in STAGE_NAMES.items():
        stage_questions = [item for item in questions if item["stage_id"] == stage_id]
        stages.append({
            "stage_id": stage_id,
            "stage_name": stage_name,
            "total_count": len(stage_questions),
            "generated_count": sum(item["has_current_answer"] for item in stage_questions),
            "answered_count": sum(
                item["has_current_answer"] and item["status"] in {"ANSWERED", "PARTIAL"}
                for item in stage_questions
            ),
            "insufficient_count": sum(
                item["has_current_answer"] and item["status"] == "INSUFFICIENT_EVIDENCE"
                for item in stage_questions
            ),
        })
    return status, {
        "catalog_version": LEARNING_QUESTION_CATALOG_VERSION,
        "batch_label": LEARNING_REPORT_BATCH_LABEL,
        "revision": report.revision_no if report is not None else None,
        "source_deep_revision": report.source_deep_revision if report is not None else None,
        "generated_at": report.created_at if report is not None else None,
        "recommended_question_ids": list(INITIAL_INCREMENTAL_QUESTION_IDS),
        "questions": questions,
        "stages": stages,
        "author_decisions": payload.get("author_decisions", []),
        "method_candidates": payload.get("method_candidates", []),
    }
