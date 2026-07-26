# -*- coding: utf-8 -*-
"""生成 F-01 章节标题识别格式测试样本库:samples/*.txt + manifest.json 同源产出。"""
import json, os, sys

OUT = r"D:\Document\Downloads\AI拆书器研究\process\f01_chapter_format_samples"

P = [
    "夜色沉下来,城市的灯一盏盏亮起。他把外套搭在肩上,沿着河堤慢慢往回走,影子被路灯拉得很长。",
    "雨停了,屋檐还在滴水。她数着台阶上的水洼,忽然想起很多年前也是这样一个傍晚。",
    "码头的风带着咸味。老周蹲在缆桩上抽烟,烟头的红光在暮色里一明一灭。",
    "巷子尽头的杂货铺还开着门,收音机里放着旧戏文。少年攥紧了口袋里那枚硬币。",
    "火车穿过隧道,窗外的光一格一格闪过去。对面的人始终没有醒,帽檐压得很低。",
    "食堂的窗口已经收摊了,只剩最后一屉冷掉的包子。他买了两个,一个塞进书包。",
]

def body(n, start=0):
    return "\n".join(P[(start + i) % len(P)] for i in range(n))

# 每个样本: id, 描述, 文件内容, 现状(基于 source_import.py _CHAPTER_LINE/_HEADING 实测正则), 期望单元与断言
SAMPLES = []

def add(sid, desc, content, current, expected_units, assertions):
    SAMPLES.append({
        "id": sid, "file": f"samples/{sid}.txt", "description": desc,
        "current_recognizer": current, "expected_units": expected_units,
        "assertions": assertions,
    })
    path = os.path.join(OUT, "samples", sid + ".txt")
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(content)

add("s01_zh_numeral", "中文数字章号+标题(最常见网文格式)",
    f"第一章 我重生了\n\n{body(2)}\n\n第二章 旧债\n\n{body(2,2)}\n",
    "认",
    [{"type": "chapter", "title_contains": "第一章", "confidence": "high"},
     {"type": "chapter", "title_contains": "第二章", "confidence": "high"}],
    ["两章齐全", "无正文被并入标题单元"])

add("s02_arabic", "阿拉伯数字章号",
    f"第1章 我重生了\n\n{body(2)}\n\n第2章 旧债\n\n{body(2,3)}\n",
    "认",
    [{"type": "chapter", "title_contains": "第1章", "confidence": "high"},
     {"type": "chapter", "title_contains": "第2章", "confidence": "high"}],
    ["两章齐全"])

add("s03_bare_number_inline", "裸数字+空格+标题同行(「1 我重生了」)",
    f"1 我重生了\n\n{body(2)}\n\n2 旧债\n\n{body(2,1)}\n\n3 归途\n\n{body(2,4)}\n",
    "不认",
    [{"type": "chapter", "title_contains": "我重生了", "confidence": "high_if_sequence"},
     {"type": "chapter", "title_contains": "旧债", "confidence": "high_if_sequence"},
     {"type": "chapter", "title_contains": "归途", "confidence": "high_if_sequence"}],
    ["连续递增数列(1,2,3)+间隔规律 → 可升高置信;孤立出现时必须走待确认"])

add("s04_bare_number_line", "裸数字独占一行,标题在下一行",
    f"1\n我重生了\n\n{body(2)}\n\n2\n旧债\n\n{body(2,2)}\n",
    "不认",
    [{"type": "chapter", "title_contains": "我重生了", "confidence": "candidate"},
     {"type": "chapter", "title_contains": "旧债", "confidence": "candidate"}],
    ["数字行+短标题行合并为一个章标题", "拿不准时进待确认,不得静默硬切"])

add("s05_longzu_regression", "龙族回归样本:引子无分隔符直连标题+书名号尾声(本次验收实际踩坑格式)",
    f"引子雨流狂落之暗\n\n{body(3)}\n\n第一幕 白帝城\n\n{body(2,1)}\n\n尾声《每个人心里都有一个死小孩》\n\n{body(2,4)}\n",
    "不认(引子/尾声),认(第一幕)",
    [{"type": "chapter", "title_contains": "引子", "confidence": "high"},
     {"type": "chapter", "title_contains": "第一幕", "confidence": "high"},
     {"type": "chapter", "title_contains": "尾声", "confidence": "high"}],
    ["引子/尾声不得被吞进相邻单元(本次 2.5 万字事故的回归用例)", "书名号《》内容保留为标题"])

add("s06_wrapper_keywords", "结构词全家桶:楔子/序章/终章/番外/后记 + 间隔号变体",
    f"楔子\n\n{body(2)}\n\n序章·风起\n\n{body(2,1)}\n\n第一章 出发\n\n{body(2,2)}\n\n终章 归来\n\n{body(2,3)}\n\n番外 小城旧事\n\n{body(2,4)}\n\n后记\n\n{body(1,5)}\n",
    "不认(全部结构词)",
    [{"type": "chapter", "title_contains": "楔子", "confidence": "high"},
     {"type": "chapter", "title_contains": "序章", "confidence": "high"},
     {"type": "chapter", "title_contains": "第一章", "confidence": "high"},
     {"type": "chapter", "title_contains": "终章", "confidence": "high"},
     {"type": "chapter", "title_contains": "番外", "confidence": "high"},
     {"type": "chapter", "title_contains": "后记", "confidence": "high"}],
    ["单独结构词与「结构词·标题」「结构词 标题」均识别", "番外/后记应可标记为非正文主线单元(类型或标签区分)"])

add("s07_hua_counter", "「第X话」量词(现行字符类缺「话」)",
    f"第1话 转学生\n\n{body(2)}\n\n第2话 社团\n\n{body(2,3)}\n",
    "不认",
    [{"type": "chapter", "title_contains": "第1话", "confidence": "high"},
     {"type": "chapter", "title_contains": "第2话", "confidence": "high"}],
    ["「话」并入既有量词字符类即可,风险最低的一类"])

add("s08_fullwidth_digits", "全角数字章号",
    f"第１２章 灯下\n\n{body(2)}\n\n第１３章 影子\n\n{body(2,2)}\n",
    "认",
    [{"type": "chapter", "title_contains": "１２", "confidence": "high"},
     {"type": "chapter", "title_contains": "１３", "confidence": "high"}],
    ["全角数字不退化"])

add("s09_uppercase_zh_numeral", "大写中文数字(壹贰叁,现行字符类不含)",
    f"第壹章 开局\n\n{body(2)}\n\n第贰章 对弈\n\n{body(2,1)}\n",
    "不认",
    [{"type": "chapter", "title_contains": "第壹章", "confidence": "high"},
     {"type": "chapter", "title_contains": "第贰章", "confidence": "high"}],
    ["壹贰叁肆伍陆柒捌玖拾佰仟并入数字字符类"])

add("s10_roman_bare", "裸罗马数字编号(无 chapter 前缀)",
    f"I. 初雪\n\n{body(2)}\n\nII. 融冰\n\n{body(2,2)}\n\nIII. 春汛\n\n{body(2,4)}\n",
    "不认",
    [{"type": "chapter", "title_contains": "初雪", "confidence": "candidate"},
     {"type": "chapter", "title_contains": "融冰", "confidence": "candidate"},
     {"type": "chapter", "title_contains": "春汛", "confidence": "candidate"}],
    ["罗马数字易与正文英文混淆,默认候选置信,连续数列可升高"])

add("s11_chapter_en", "英文 Chapter N",
    f"Chapter 5 The Return\n\n{body(2)}\n\nChapter 6 The Gate\n\n{body(2,3)}\n",
    "认",
    [{"type": "chapter", "title_contains": "Chapter 5", "confidence": "high"},
     {"type": "chapter", "title_contains": "Chapter 6", "confidence": "high"}],
    ["现状保持"])

add("s12_volume_chapter", "卷+章两级结构",
    f"卷一 火之晨曦\n\n第一章 出发\n\n{body(2)}\n\n第二章 抵达\n\n{body(2,2)}\n\n卷二 悼亡者之瞳\n\n第一章 重逢\n\n{body(2,4)}\n",
    "认",
    [{"type": "volume", "title_contains": "卷一", "confidence": "high"},
     {"type": "chapter", "title_contains": "出发", "confidence": "high"},
     {"type": "chapter", "title_contains": "抵达", "confidence": "high"},
     {"type": "volume", "title_contains": "卷二", "confidence": "high"},
     {"type": "chapter", "title_contains": "重逢", "confidence": "high"}],
    ["卷标题单元不吞正文", "跨卷章号重置(两个第一章)不报错"])

add("s13_markdown", "Markdown 标题层级",
    f"# 卷一\n\n## 第一章 出发\n\n{body(2)}\n\n## 第二章 抵达\n\n{body(2,1)}\n",
    "认",
    [{"type": "volume", "title_contains": "卷一", "confidence": "high"},
     {"type": "chapter", "title_contains": "出发", "confidence": "high"},
     {"type": "chapter", "title_contains": "抵达", "confidence": "high"}],
    ["现状保持"])

add("s14_false_positive_guard", "防误切:正文内孤立数字行与类标题行",
    f"第一章 倒计时\n\n{body(1)}\n他盯着屏幕上的数字。\n3\n2\n1\n爆炸没有发生。\n{body(1,3)}\n\n第二章 复盘\n\n{body(1,4)}\n会议记录写着:\n一、检查线路。\n二、更换保险。\n{body(1,5)}\n",
    "现状不误切(因为根本不认裸数字);改造后必须仍不误切",
    [{"type": "chapter", "title_contains": "倒计时", "confidence": "high"},
     {"type": "chapter", "title_contains": "复盘", "confidence": "high"}],
    ["正文中的 3/2/1 倒数与「一、二、」列表不得切章(总单元数=2)",
     "统计启发:候选切分导致单章异常短(<数百字)时自动降置信"])

add("s15_physical_book", "实体书形态:无卷、章极长、含后记",
    f"第一章\n\n{body(6)}\n\n{body(6,1)}\n\n第二章\n\n{body(6,2)}\n\n{body(6,3)}\n\n后记\n\n{body(2,5)}\n",
    "认(章),不认(后记)",
    [{"type": "chapter", "title_contains": "第一章", "confidence": "high"},
     {"type": "chapter", "title_contains": "第二章", "confidence": "high"},
     {"type": "chapter", "title_contains": "后记", "confidence": "high"}],
    ["无标题文字的裸「第一章」可识别", "长章不触发密度告警(长度启发用相对分布而非绝对阈值)"])

add("s16_dense_webnovel", "网文形态:短章高频(按比例缩小的 3000-4000 字/章)",
    "\n\n".join(f"第{n}章 第{n}天\n\n{body(3, n)}" for n in range(1, 7)) + "\n",
    "认",
    [{"type": "chapter", "title_contains": f"第{n}章", "confidence": "high"} for n in range(1, 7)],
    ["6 章齐全且间隔规律", "章长规律性可作为其他候选的交叉验证信号"])

add("s17_front_matter", "书名/作者/简介前置区(不属于任何章节)",
    f"《河堤往事》\n作者:佚名\n\n内容简介:一座小城,三代人,四十年。\n\n第一章 出发\n\n{body(2)}\n\n第二章 抵达\n\n{body(2,3)}\n",
    "前置区被并入首个单元(现状无前言概念)",
    [{"type": "front_matter", "title_contains": "河堤往事", "confidence": "candidate"},
     {"type": "chapter", "title_contains": "出发", "confidence": "high"},
     {"type": "chapter", "title_contains": "抵达", "confidence": "high"}],
    ["书名/作者/简介归入前置单元或待确认,不得算进第一章正文"])

add("s18_number_blank_title", "数字行与标题行之间隔空行",
    f"3\n\n第三个夏天\n\n{body(2)}\n\n4\n\n最后一场雨\n\n{body(2,2)}\n",
    "不认",
    [{"type": "chapter", "title_contains": "第三个夏天", "confidence": "candidate"},
     {"type": "chapter", "title_contains": "最后一场雨", "confidence": "candidate"}],
    ["跨空行的数字+标题组合仅作候选,必须过待确认"])

manifest = {
    "name": "F-01 章节标题识别格式测试样本库",
    "created": "2026-07-26",
    "origin": "预注册验收单(process/acceptance_2026-07-26/预注册验收单.md)第 5 节 F-01;用户 2026-07-26 反馈",
    "current_recognizer_source": "backend/app/services/source_import.py _CHAPTER_LINE/_HEADING(2026-07-26 实测)",
    "acceptance_rule": "改造识别器后,以下每个样本导入结果必须满足 expected_units 与 assertions 全绿;confidence=candidate 的单元必须出现在 4.3 待确认通道而非静默硬切;current_recognizer 字段仅为改造前基线记录,不参与判定。",
    "confidence_semantics": {
        "high": "直接切章,无需人工确认",
        "candidate": "切分建议进待确认队列,用户裁决后生效",
        "high_if_sequence": "孤立出现为 candidate;与相邻候选构成连续编号且间隔规律时升为 high"
    },
    "samples": SAMPLES,
}

os.makedirs(os.path.join(OUT, "samples"), exist_ok=True)
with open(os.path.join(OUT, "manifest.json"), "w", encoding="utf-8", newline="\n") as f:
    json.dump(manifest, f, ensure_ascii=False, indent=2)

print(f"OK 样本 {len(SAMPLES)} 个 + manifest.json 已写入 {OUT}")
for s in SAMPLES:
    print(" -", s["id"], "|", s["current_recognizer"])
