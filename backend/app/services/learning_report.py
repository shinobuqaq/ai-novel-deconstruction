from __future__ import annotations

import json
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


LEARNING_REPORT_TASK_KIND = "analysis.learning_report"
LEARNING_REPORT_PROMPT_ID = "learning_report"
LEARNING_REPORT_PROMPT_VERSION = "1.1.0"
LEARNING_QUESTION_CATALOG_VERSION = "1.1.0"
LEARNING_REPORT_BATCH_LABEL = "内部候选批次（7/42，非 Fable 固定批次）"


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
        "主角双层欲望卡、最小完整集交代节奏表（要素、首次展示章节、展示事件）和弧光转折时间轴；配角仅做轻量版。",
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
        "逐章或抽样至少 50 章；统计类型占比、连续强钩和同类钩上限、无钩比例及回应距离。",
        "必须保存每个样本章的最后有效段落及后续回应证据；剧情阶段悬念和普通场景分析不能替代章末证据。",
        "少于 50 章的作品覆盖全部章节；更长作品可全量或采用覆盖开中后、各卷边界的 50 章以上预注册样本。",
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

PROTOTYPE_BATCH_QUESTION_IDS = ("1.4", "2.1", "2.2", "4.9", "5.3", "6.4", "6.5")
_RECOMMENDED_RANK = {question_id: rank for rank, question_id in enumerate(PROTOTYPE_BATCH_QUESTION_IDS, start=1)}
_QUESTION_BY_ID = {item.question_id: item for item in LEARNING_QUESTION_CATALOG}

if len(LEARNING_QUESTION_CATALOG) != 42 or len(_QUESTION_BY_ID) != 42:
    raise RuntimeError("LEARNING_QUESTION_CATALOG_MUST_CONTAIN_42_UNIQUE_QUESTIONS")
if set(LEARNING_QUESTION_CONTRACTS) != set(_QUESTION_BY_ID):
    raise RuntimeError("EVERY_LEARNING_QUESTION_MUST_HAVE_ONE_FABLE_OUTPUT_CONTRACT")


class LearningMetricProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1, max_length=160)
    value: str = Field(min_length=1, max_length=160)
    unit: str = Field(default="", max_length=60)
    method: str = Field(min_length=1, max_length=500)
    evidence_ids: list[str] = Field(default_factory=list, max_length=16)


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

    answers: list[LearningAnswerProposal] = Field(min_length=7, max_length=7)
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
            if key in {"evidence_ids", "counter_evidence_ids"} and isinstance(item, list):
                found.update(str(entry) for entry in item if entry)
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


def _request_budget_chars(profile: Any) -> int:
    output_reserve = max(1, int(getattr(profile, "max_output_tokens", 16_000)))
    context_window = getattr(profile, "context_window_tokens", None)
    if context_window is not None:
        return min(160_000, max(24_000, int(context_window) - output_reserve - 4_096))
    return max(48_000, min(160_000, output_reserve * 3))


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
            "text": evidence.text_snapshot,
        })
    return records


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


def _source_materials(projection: dict) -> list[tuple[int, str, dict]]:
    deep = projection.get("deep_analysis") or {}
    materials: list[tuple[int, str, dict]] = []
    overview = projection.get("story_overview")
    if isinstance(overview, dict):
        materials.append((100, "story_overview", overview))
    for item in projection.get("characters", []):
        materials.append((92, "character", item))
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


def _readiness_check(
    question_id: str,
    *,
    ready: bool,
    observed: dict[str, object],
    gaps: list[str],
    required_artifact: str,
) -> dict[str, object]:
    return {
        "question_id": question_id,
        "question": _QUESTION_BY_ID[question_id].question,
        "ready": ready,
        "observed": observed,
        "gaps": gaps,
        "required_artifact": required_artifact,
    }


def assess_learning_report_readiness(projection: dict) -> dict[str, object]:
    """Audit whether the prototype batch has the Fable-required source artifacts.

    This is intentionally stricter than checking that a deep-analysis row exists.
    A model task is useful only after the supporting counts and evidence ledgers exist.
    """

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
    checks: dict[str, dict[str, object]] = {}
    checks["1.4"] = _readiness_check(
        "1.4",
        ready=not selling_point_gaps,
        observed={
            "chapter_count": chapter_count,
            "opening_event_count": len(opening_events),
            "overview_evidence_count": len(overview.get("evidence_ids", [])),
        },
        gaps=selling_point_gaps,
        required_artifact="开篇承诺与首次兑现证据表",
    )

    action_gaps: list[str] = []
    if chapter_count < 30:
        action_gaps.append(f"当前只有 {chapter_count} 章，无法完成前 3/10/30 章三个固定口径。")
    for item in action_counts:
        if int(item["active_character_count"]) == 0:
            action_gaps.append(
                f"前 {item['through_chapter']} 章没有可由事件证据确认的有效行动人物。"
            )
    checks["2.1"] = _readiness_check(
        "2.1",
        ready=not action_gaps,
        observed={"program_counts": action_counts},
        gaps=action_gaps,
        required_artifact="前 3/10/30 章有效行动人物程序计数表",
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
    if protagonist is None:
        character_gaps.append("尚未确认主角。")
    if character_design and character_design.get("is_current") is False:
        character_gaps.append("主角证据表基于旧人物或旧拆解结果，需要重新生成。")
    coverage = character_design.get("coverage", {}) if isinstance(character_design, dict) else {}
    if character_design and coverage.get("event_coverage_complete") is not True:
        character_gaps.append("主角证据表没有覆盖当前运行的全部主角事件。")
    for key, label in required_character_fields.items():
        item = character_design_by_field.get(key)
        if (
            not isinstance(item, dict)
            or item.get("status", "SUPPORTED") != "SUPPORTED"
            or not item.get("first_display_chapter_ordinal")
            or not item.get("first_display_event_id")
            or not item.get("evidence_ids")
        ):
            character_gaps.append(f"缺少{label}的首次展示事件与原文。")
    if character_design and not str(character_design.get("arc_summary") or "").strip():
        character_gaps.append("主角证据表缺少全书人物弧光总结。")
    checks["2.2"] = _readiness_check(
        "2.2",
        ready=not character_gaps,
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
            "desire_conflict_count": len(character_design.get("desire_conflicts", []))
            if isinstance(character_design, dict)
            else 0,
            "event_coverage_complete": coverage.get("event_coverage_complete"),
        },
        gaps=character_gaps,
        required_artifact="主角双层欲望与最小完整集证据表",
    )

    chapter_end_hooks_evidence = projection.get("chapter_end_hooks_evidence") or {}
    chapter_end_hooks = projection.get("chapter_end_hooks") or []
    required_hook_sample = chapter_count if chapter_count <= 50 else 50
    valid_hook_samples = [
        item
        for item in chapter_end_hooks
        if item.get("ending_evidence_ids")
        and item.get("hook_type")
        and item.get("strength")
        and (
            item.get("hook_type") == "NONE"
            or item.get("response_status") in {"RESOLVED", "PARTIAL", "UNRESOLVED"}
        )
        and (
            item.get("response_status") not in {"RESOLVED", "PARTIAL"}
            or (
                item.get("response_evidence_ids")
                and item.get("response_chapter_ordinal")
                and item.get("response_distance") is not None
            )
        )
    ]
    hook_gaps: list[str] = []
    if chapter_end_hooks_evidence and chapter_end_hooks_evidence.get("is_current") is False:
        hook_gaps.append("章末钩账本对应旧版拆解或旧版正文，需要重新生成。")
    if len(valid_hook_samples) < required_hook_sample:
        hook_gaps.append(
            f"需要 {required_hook_sample} 章真实章末证据与分类，当前只有 {len(valid_hook_samples)} 章。"
        )
    coverage = (
        chapter_end_hooks_evidence.get("coverage", {})
        if isinstance(chapter_end_hooks_evidence, dict)
        else {}
    )
    if chapter_end_hooks and coverage.get("ending_evidence_complete") is not True:
        hook_gaps.append("章末钩账本没有通过程序的逐章结尾证据覆盖检查。")
    if chapter_end_hooks and coverage.get("response_reference_complete") is not True:
        hook_gaps.append("章末钩账本存在未核准的回应章节或回应原文。")
    checks["4.9"] = _readiness_check(
        "4.9",
        ready=not hook_gaps,
        observed={
            "chapter_count": chapter_count,
            "required_sample_count": required_hook_sample,
            "valid_chapter_end_sample_count": len(valid_hook_samples),
            "sample_policy": coverage.get("sample_policy"),
            "resolved_or_partial_count": (
                chapter_end_hooks_evidence.get("summary", {}).get(
                    "resolved_or_partial_count", 0
                )
                if isinstance(chapter_end_hooks_evidence, dict)
                else 0
            ),
            "generic_scene_analysis_count": len(deep.get("scene_analysis", [])),
        },
        gaps=hook_gaps,
        required_artifact="逐章章末钩与回应账本",
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

    ordered_checks = [checks[question_id] for question_id in PROTOTYPE_BATCH_QUESTION_IDS]
    blocking_checks = [item for item in ordered_checks if not item["ready"]]
    return {
        "ready": not blocking_checks,
        "policy": "全部 7 问满足各自数据合同后才允许创建在线模型任务。",
        "ready_question_count": len(ordered_checks) - len(blocking_checks),
        "total_question_count": len(ordered_checks),
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
    if report.prompt_version != LEARNING_REPORT_PROMPT_VERSION:
        return False
    try:
        payload = json.loads(report.payload_json)
    except (TypeError, json.JSONDecodeError):
        return False
    return payload.get("catalog_version") == LEARNING_QUESTION_CATALOG_VERSION


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
    if not readiness["ready"]:
        raise LearningReportNotReadyError(readiness)
    _service, profile = resolve_analysis_profile(
        settings,
        str(task_payload.get("model_profile_id") or ENTITIES_EVENTS_PROFILE_ID),
    )
    chapter_units = list(session.scalars(
        select(SourceUnit)
        .where(
            SourceUnit.source_version_id == version.id,
            SourceUnit.unit_type == "CHAPTER",
        )
        .order_by(SourceUnit.ordinal)
    ))
    chapter_by_unit_id = {
        unit.id: {"ordinal": ordinal, "title": unit.title}
        for ordinal, unit in enumerate(chapter_units, start=1)
    }
    source_materials = _source_materials(projection)
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
        for question_id in PROTOTYPE_BATCH_QUESTION_IDS
    ]
    character_index = [
        {
            key: character.get(key)
            for key in (
                "id", "name", "aliases", "role", "role_reason", "goals",
                "motivations", "abilities", "first_chapter_ordinal",
                "last_chapter_ordinal", "appearance_count", "event_ids", "evidence_ids",
            )
        }
        for character in projection.get("characters", [])
    ]
    fixed_input = {
        "catalog_version": LEARNING_QUESTION_CATALOG_VERSION,
        "batch_label": LEARNING_REPORT_BATCH_LABEL,
        "question_catalog": question_catalog,
        "chapter_catalog": [
            {"ordinal": ordinal, "title": unit.title}
            for ordinal, unit in enumerate(chapter_units, start=1)
        ],
        "character_index": character_index,
        "program_metrics": {
            "opening_action_character_counts": _opening_action_counts(projection),
        },
    }
    fixed_chars = len(json.dumps(fixed_input, ensure_ascii=False, separators=(",", ":")))
    material_budget = max(0, _request_budget_chars(profile) - fixed_chars - 4_000)
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
        size = len(json.dumps(bundle, ensure_ascii=False, separators=(",", ":")))
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
        "input": json.dumps(input_payload, ensure_ascii=False, separators=(",", ":")),
        "output_schema": _inline_model_schema(LearningReportOutput),
        "model_profile_id": str(task_payload.get("model_profile_id") or ENTITIES_EVENTS_PROFILE_ID),
        "prompt_id": LEARNING_REPORT_PROMPT_ID,
        "prompt_version": LEARNING_REPORT_PROMPT_VERSION,
        "source_version_id": version.id,
        "source_char_start": 0,
        "source_char_end": version.total_chars,
        "context_manifest": input_payload["coverage_manifest"],
    }


def parse_learning_report(value: dict) -> LearningReportOutput:
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
    if set(question_ids) != set(PROTOTYPE_BATCH_QUESTION_IDS):
        raise LearningReportValidationError(
            "LEARNING_REPORT_QUESTION_COVERAGE_INVALID",
            [{"path": ["answers"], "type": "value_error", "message": "必须逐一回答当前 7 个问题"}],
        )
    return output


def persist_learning_report(
    session: Session,
    *,
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
    payload = output.model_dump(mode="json")
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
        "generated_question_ids": list(PROTOTYPE_BATCH_QUESTION_IDS),
        "source_deep_revision": source_deep_revision,
    })
    payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    existing = session.scalar(
        select(LearningReport).where(LearningReport.created_by_task_id == task.id)
    )
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
) -> Task | None:
    deep = session.scalar(
        select(DeepAnalysis)
        .where(DeepAnalysis.run_id == run.id)
        .order_by(DeepAnalysis.revision_no.desc())
    )
    if deep is None:
        return None
    from .workbench import build_workbench_projection

    readiness = assess_learning_report_readiness(
        build_workbench_projection(session, run.id)
    )
    if not readiness["ready"]:
        raise LearningReportNotReadyError(readiness)
    latest_report = session.scalar(
        select(LearningReport)
        .where(LearningReport.run_id == run.id)
        .order_by(LearningReport.revision_no.desc())
    )
    latest_task = session.scalar(
        select(Task)
        .join(AnalysisRunTask, AnalysisRunTask.task_id == Task.id)
        .where(
            AnalysisRunTask.run_id == run.id,
            Task.kind == LEARNING_REPORT_TASK_KIND,
        )
        .order_by(AnalysisRunTask.batch_index.desc())
    )
    if latest_task is not None and latest_task.status in {
        TaskStatus.PENDING.value,
        TaskStatus.RUNNING.value,
        TaskStatus.RETRY_WAIT.value,
        TaskStatus.WAITING_CONFIRMATION.value,
    }:
        return latest_task
    if (
        latest_report is not None
        and latest_report.source_deep_revision == deep.revision_no
        and _report_uses_current_contract(latest_report)
        and not force
    ):
        return latest_task
    try:
        _service, profile = resolve_analysis_profile(settings, ENTITIES_EVENTS_PROFILE_ID)
    except ModelSettingsError:
        return None
    task_payload, max_attempts = prepare_task_provider_routes(
        settings,
        {
            "run_id": run.id,
            "source_version_id": run.source_version_id,
            "source_deep_revision": deep.revision_no,
            "provider_name": "openai",
            "model_profile_id": profile.id,
            "question_ids": list(PROTOTYPE_BATCH_QUESTION_IDS),
            "catalog_version": LEARNING_QUESTION_CATALOG_VERSION,
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
    next_index = max((link.batch_index for link in run.task_links), default=run.total_batches) + 1
    session.add(AnalysisRunTask(run_id=run.id, task_id=task.id, batch_index=next_index))
    run.total_batches = next_index
    run.status = AnalysisRunStatus.PENDING.value
    session.commit()
    session.refresh(task)
    return task


def build_learning_report_projection(
    session: Session,
    run_id: str,
    *,
    latest_deep_revision: int | None,
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
    active_statuses = {
        TaskStatus.PENDING.value,
        TaskStatus.RUNNING.value,
        TaskStatus.RETRY_WAIT.value,
        TaskStatus.WAITING_CONFIRMATION.value,
    }
    payload = json.loads(report.payload_json) if report is not None else {}
    report_is_current = bool(
        report is not None
        and report.source_deep_revision == latest_deep_revision
        and _report_uses_current_contract(report)
    )
    if latest_task is not None and latest_task.status in active_statuses:
        status = "GENERATING"
    elif report_is_current:
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
            "status": answer.get("status", "NOT_GENERATED"),
            "conclusion": answer.get("conclusion", ""),
            "metrics": answer.get("metrics", []),
            "evidence_ids": answer.get("evidence_ids", []),
            "counter_evidence_ids": answer.get("counter_evidence_ids", []),
            "limitations": answer.get("limitations", []),
            "reusable_lessons": answer.get("reusable_lessons", []),
            "do_not_copy": answer.get("do_not_copy", []),
        })
    stages: list[dict[str, object]] = []
    for stage_id, stage_name in STAGE_NAMES.items():
        stage_questions = [item for item in questions if item["stage_id"] == stage_id]
        stages.append({
            "stage_id": stage_id,
            "stage_name": stage_name,
            "total_count": len(stage_questions),
            "generated_count": sum(item["status"] != "NOT_GENERATED" for item in stage_questions),
            "answered_count": sum(item["status"] in {"ANSWERED", "PARTIAL"} for item in stage_questions),
            "insufficient_count": sum(item["status"] == "INSUFFICIENT_EVIDENCE" for item in stage_questions),
        })
    return status, {
        "catalog_version": LEARNING_QUESTION_CATALOG_VERSION,
        "batch_label": LEARNING_REPORT_BATCH_LABEL,
        "revision": report.revision_no if report is not None else None,
        "source_deep_revision": report.source_deep_revision if report is not None else None,
        "generated_at": report.created_at if report is not None else None,
        "recommended_question_ids": list(PROTOTYPE_BATCH_QUESTION_IDS),
        "questions": questions,
        "stages": stages,
        "author_decisions": payload.get("author_decisions", []),
        "method_candidates": payload.get("method_candidates", []),
    }
