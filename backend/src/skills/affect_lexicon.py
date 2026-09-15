"""Affect Expression Vocabulary（情绪/感受表达词库）—— 纯数据，供 prompt / 候选校验 / 触发预筛共用。

本模块不再是一个“心理学情绪分类体系”，而是一份「中文用户在日常对话里会怎样描述自己
当前感受」的候选语言空间（规格 §1、§2、§29）。

核心分层：
- **CANDIDATE_VOCABULARY**（canonical source of truth）：系统可以主动提供给用户的
  候选表达（“你觉得更像委屈、失望，还是被忽视？”）。覆盖四个 kind：
  emotion（情绪命名）/ experience（关系/经历的主观感受）/ state（整体状态）/
  somatic（身体层面的主观体验）。语义方向用 `group` 表示，仅作方向启发、不是严格分类。
- **非候选集合**：clinical / psychological jargon / judgment / event / do_not_suggest
  可以出现在用户原话里、参与上下文/安全判断，但**绝不能**被系统当成候选主动建议。
- **笼统/表达卡壳 cue**（GENERALIZED_AFFECT_TERMS / LOW_SPECIFICITY_CUES /
  INCOMPLETE_NAMING_CUES）：表示“用户在表达感受但还没给出足够具体的命名”，
  用于触发预筛是否值得进一步澄清，**不是** candidate，**不是** keyword→emotion 分类器。

铁律（规格 §24、§29）：
- 词库不替用户决定“你真正的情绪是什么”；词库只提供“也许我想说的是这个”的语言空间；
- 不做 胸闷→焦虑 / 睡不着→抑郁 / 被忽视→依恋 之类映射；
- 不建立复杂的心理学 emotion ontology。

旧接口（legacy）由 CANDIDATE_VOCABULARY 派生，保持外部 import（engine/_gate、
skill/候选校验、prompt/方向行）不变。其中 LOW_SPECIFICITY_CUES 与
GENERALIZED_AFFECT_TERMS 的**部分重叠是有意的**：legacy gate 把“笼统感受词
（烦/难受…）”与“表达卡壳 cue（说不上来…）”都视作“值得澄清”的信号，二者概念不同、
用于不同目的，但同为该信号（见 §13/§14，43 个 AL 测试绑定该行为）。
"""

from __future__ import annotations

# ═══════════════════════════════════════════════════════════════════════════════
# 允许的枚举值（供测试与派生校验）
# ═══════════════════════════════════════════════════════════════════════════════

ALLOWED_GROUPS = (
    "anger", "sadness", "fear_anxiety", "shame_guilt",
    "interpersonal", "overload", "positive", "somatic",
)
ALLOWED_KINDS = ("emotion", "experience", "state", "somatic")
ALLOWED_SPECIFICITY = ("specific", "moderate", "broad")
ALLOWED_REGISTERS = ("neutral", "colloquial", "literary")

# group → 中文展示名（用于拼 LLM 候选方向行）
GROUP_LABELS = {
    "anger": "生气 / 愤怒",
    "sadness": "难过 / 悲伤",
    "fear_anxiety": "担心 / 害怕 / 焦虑",
    "shame_guilt": "内疚 / 羞愧",
    "interpersonal": "委屈 / 人际",
    "overload": "累 / 压力大",
    "positive": "开心 / 安心",
    "somatic": "身体感受",
}

# ═══════════════════════════════════════════════════════════════════════════════
# Canonical：CANDIDATE_VOCABULARY（唯一事实来源）
#
# 字段：
#   group       语义方向（只作启发，非严格心理学分类）
#   kind        emotion | experience | state | somatic
#   specificity 对“区分用户主观体验”有多大帮助（specific/moderate/broad；≠强度）
#   register    语言风格（neutral/colloquial/literary；只作 metadata，不据此删词）
#
# 每条都是“用户真实会说、愿意认领”的中文表达；同一方向的近义表达若放一起会失去
# 区分度，因此 canonical 里刻意只收口语里能互相区分开的词（同义词堆叠交给 LLM 规则与
# 校验去重处理）。总规模约 85。
# ═══════════════════════════════════════════════════════════════════════════════

CANDIDATE_VOCABULARY: dict[str, dict[str, str]] = {
    # ── anger ──────────────────────────────────────────────────────
    "生气": {"group": "anger", "kind": "emotion", "specificity": "specific", "register": "neutral"},
    "恼火": {"group": "anger", "kind": "emotion", "specificity": "specific", "register": "neutral"},
    "气愤": {"group": "anger", "kind": "emotion", "specificity": "specific", "register": "neutral"},
    "烦躁": {"group": "anger", "kind": "emotion", "specificity": "specific", "register": "neutral"},
    "憋屈": {"group": "anger", "kind": "emotion", "specificity": "specific", "register": "colloquial"},
    "不爽": {"group": "anger", "kind": "emotion", "specificity": "broad", "register": "colloquial"},
    "郁闷": {"group": "anger", "kind": "emotion", "specificity": "moderate", "register": "colloquial"},
    "反感": {"group": "anger", "kind": "emotion", "specificity": "moderate", "register": "neutral"},
    "火大": {"group": "anger", "kind": "emotion", "specificity": "specific", "register": "colloquial"},
    # ── sadness ────────────────────────────────────────────────────
    "难过": {"group": "sadness", "kind": "emotion", "specificity": "broad", "register": "neutral"},
    "伤心": {"group": "sadness", "kind": "emotion", "specificity": "specific", "register": "neutral"},
    "失落": {"group": "sadness", "kind": "emotion", "specificity": "specific", "register": "neutral"},
    "失望": {"group": "sadness", "kind": "emotion", "specificity": "specific", "register": "neutral"},
    "沮丧": {"group": "sadness", "kind": "emotion", "specificity": "specific", "register": "neutral"},
    "遗憾": {"group": "sadness", "kind": "emotion", "specificity": "moderate", "register": "neutral"},
    "心酸": {"group": "sadness", "kind": "emotion", "specificity": "specific", "register": "colloquial"},
    "落寞": {"group": "sadness", "kind": "emotion", "specificity": "specific", "register": "literary"},
    "空落落": {"group": "sadness", "kind": "state", "specificity": "specific", "register": "colloquial"},
    "低落": {"group": "sadness", "kind": "state", "specificity": "moderate", "register": "neutral"},
    "悲伤": {"group": "sadness", "kind": "emotion", "specificity": "specific", "register": "literary"},
    # ── fear_anxiety ───────────────────────────────────────────────
    "担心": {"group": "fear_anxiety", "kind": "emotion", "specificity": "specific", "register": "neutral"},
    "害怕": {"group": "fear_anxiety", "kind": "emotion", "specificity": "specific", "register": "neutral"},
    "紧张": {"group": "fear_anxiety", "kind": "emotion", "specificity": "moderate", "register": "neutral"},
    "不安": {"group": "fear_anxiety", "kind": "emotion", "specificity": "specific", "register": "neutral"},
    "焦虑": {"group": "fear_anxiety", "kind": "emotion", "specificity": "moderate", "register": "neutral"},
    "慌": {"group": "fear_anxiety", "kind": "emotion", "specificity": "broad", "register": "colloquial"},
    "没底": {"group": "fear_anxiety", "kind": "emotion", "specificity": "specific", "register": "colloquial"},
    "忐忑": {"group": "fear_anxiety", "kind": "emotion", "specificity": "specific", "register": "literary"},
    "发愁": {"group": "fear_anxiety", "kind": "emotion", "specificity": "moderate", "register": "colloquial"},
    "惶恐": {"group": "fear_anxiety", "kind": "emotion", "specificity": "specific", "register": "literary"},
    # ── shame_guilt ────────────────────────────────────────────────
    "自责": {"group": "shame_guilt", "kind": "emotion", "specificity": "specific", "register": "neutral"},
    "内疚": {"group": "shame_guilt", "kind": "emotion", "specificity": "specific", "register": "neutral"},
    "愧疚": {"group": "shame_guilt", "kind": "emotion", "specificity": "specific", "register": "neutral"},
    "羞愧": {"group": "shame_guilt", "kind": "emotion", "specificity": "specific", "register": "neutral"},
    "惭愧": {"group": "shame_guilt", "kind": "emotion", "specificity": "specific", "register": "neutral"},
    "后悔": {"group": "shame_guilt", "kind": "emotion", "specificity": "specific", "register": "neutral"},
    "丢脸": {"group": "shame_guilt", "kind": "experience", "specificity": "specific", "register": "colloquial"},
    "难堪": {"group": "shame_guilt", "kind": "experience", "specificity": "specific", "register": "neutral"},
    "尴尬": {"group": "shame_guilt", "kind": "experience", "specificity": "specific", "register": "neutral"},
    # ── interpersonal ──────────────────────────────────────────────
    "委屈": {"group": "interpersonal", "kind": "emotion", "specificity": "specific", "register": "neutral"},
    "孤独": {"group": "interpersonal", "kind": "emotion", "specificity": "specific", "register": "neutral"},
    "被忽视": {"group": "interpersonal", "kind": "experience", "specificity": "specific", "register": "neutral"},
    "被误解": {"group": "interpersonal", "kind": "experience", "specificity": "specific", "register": "neutral"},
    "被辜负": {"group": "interpersonal", "kind": "experience", "specificity": "specific", "register": "neutral"},
    "不被重视": {"group": "interpersonal", "kind": "experience", "specificity": "specific", "register": "neutral"},
    "被冷落": {"group": "interpersonal", "kind": "experience", "specificity": "specific", "register": "neutral"},
    "被拒绝": {"group": "interpersonal", "kind": "experience", "specificity": "specific", "register": "neutral"},
    "被排斥": {"group": "interpersonal", "kind": "experience", "specificity": "specific", "register": "neutral"},
    "不被理解": {"group": "interpersonal", "kind": "experience", "specificity": "specific", "register": "neutral"},
    "缺乏安全感": {"group": "interpersonal", "kind": "state", "specificity": "moderate", "register": "neutral"},
    "疏离感": {"group": "interpersonal", "kind": "experience", "specificity": "specific", "register": "neutral"},
    # ── overload ───────────────────────────────────────────────────
    "累": {"group": "overload", "kind": "state", "specificity": "broad", "register": "neutral"},
    "心累": {"group": "overload", "kind": "state", "specificity": "moderate", "register": "colloquial"},
    "疲惫": {"group": "overload", "kind": "state", "specificity": "moderate", "register": "neutral"},
    "压力大": {"group": "overload", "kind": "state", "specificity": "broad", "register": "neutral"},
    "心烦": {"group": "overload", "kind": "state", "specificity": "moderate", "register": "colloquial"},
    "烦心": {"group": "overload", "kind": "state", "specificity": "moderate", "register": "colloquial"},
    "心乱": {"group": "overload", "kind": "state", "specificity": "specific", "register": "neutral"},
    "无力": {"group": "overload", "kind": "state", "specificity": "moderate", "register": "neutral"},
    "无助": {"group": "overload", "kind": "state", "specificity": "specific", "register": "neutral"},
    "麻木": {"group": "overload", "kind": "state", "specificity": "specific", "register": "neutral"},
    "空虚": {"group": "overload", "kind": "state", "specificity": "specific", "register": "neutral"},
    "精疲力尽": {"group": "overload", "kind": "state", "specificity": "specific", "register": "neutral"},
    "绷不住": {"group": "overload", "kind": "state", "specificity": "specific", "register": "colloquial"},
    "透支": {"group": "overload", "kind": "state", "specificity": "specific", "register": "colloquial"},
    "扛不住": {"group": "overload", "kind": "state", "specificity": "specific", "register": "colloquial"},
    # ── positive ───────────────────────────────────────────────────
    "开心": {"group": "positive", "kind": "emotion", "specificity": "specific", "register": "neutral"},
    "高兴": {"group": "positive", "kind": "emotion", "specificity": "specific", "register": "neutral"},
    "安心": {"group": "positive", "kind": "state", "specificity": "specific", "register": "neutral"},
    "轻松": {"group": "positive", "kind": "state", "specificity": "moderate", "register": "neutral"},
    "满足": {"group": "positive", "kind": "state", "specificity": "specific", "register": "neutral"},
    "期待": {"group": "positive", "kind": "emotion", "specificity": "specific", "register": "neutral"},
    "兴奋": {"group": "positive", "kind": "emotion", "specificity": "moderate", "register": "neutral"},
    "踏实": {"group": "positive", "kind": "state", "specificity": "specific", "register": "neutral"},
    "幸福": {"group": "positive", "kind": "emotion", "specificity": "specific", "register": "neutral"},
    "释然": {"group": "positive", "kind": "state", "specificity": "specific", "register": "neutral"},
    "欣慰": {"group": "positive", "kind": "emotion", "specificity": "specific", "register": "neutral"},
    "放松": {"group": "positive", "kind": "state", "specificity": "moderate", "register": "neutral"},
    "自在": {"group": "positive", "kind": "state", "specificity": "moderate", "register": "colloquial"},
    # ── somatic ────────────────────────────────────────────────────
    "心慌": {"group": "somatic", "kind": "somatic", "specificity": "specific", "register": "colloquial"},
    "发紧": {"group": "somatic", "kind": "somatic", "specificity": "specific", "register": "neutral"},
    "胸闷": {"group": "somatic", "kind": "somatic", "specificity": "specific", "register": "neutral"},
    "发抖": {"group": "somatic", "kind": "somatic", "specificity": "specific", "register": "neutral"},
    "坐立不安": {"group": "somatic", "kind": "somatic", "specificity": "moderate", "register": "neutral"},
    "睡不着": {"group": "somatic", "kind": "somatic", "specificity": "specific", "register": "colloquial"},
}

# ═══════════════════════════════════════════════════════════════════════════════
# 混合体验（保留少量，作为 experience；不入普通候选清单的高频扩展另见下）——
# “五味杂陈 / 百感交集”可合法作 experience 候选（规格 §12）。
# ═══════════════════════════════════════════════════════════════════════════════

# 说明：为避免规模膨胀，混合词未塞进 CANDIDATE_VOCABULARY；若要启用，追加即可。
MIXED_EXPERIENCE_TERMS = ("五味杂陈", "百感交集")


# ═══════════════════════════════════════════════════════════════════════════════
# 笼统 / 表达卡壳 cue —— 不是 candidate，不是分类器（规格 §13/§14）
# ═══════════════════════════════════════════════════════════════════════════════

# 用户明显在表达 affect，但没有给出足够具体的命名
GENERALIZED_AFFECT_TERMS: tuple[str, ...] = (
    "难受", "不舒服", "不好受", "烦", "压抑", "糟糕", "不好",
    "很崩", "崩溃", "难搞", "状态不好", "心情不好", "感觉很差",
)

# 触发预筛 cue：“这条表达是否值得进一步澄清？”
# 注意：legacy gate 把「笼统感受词（烦/难受…）」与「表达卡壳 cue（说不上来…）」
# 都视作值得澄清信号——因此本集合与 GENERALIZED_AFFECT_TERMS 的部分重叠是有意为之
# （保持 AffectLabelingEngine._gate 行为与 43 个 AL 测试不变）。
LOW_SPECIFICITY_CUES: tuple[str, ...] = (
    # —— 笼统感受词（legacy gate in 依赖，见 §13 注释）——
    "烦", "难受", "不舒服", "不好受", "心里堵", "堵得慌", "堵",
    "闷闷的", "整个人都不好", "不是滋味", "心里空空",
    # —— 表达卡壳 / 低区分度 cue（规格 §14）——
    "说不上来", "说不上", "说不清", "不知道怎么说", "不知道什么感觉",
    "不知道自己怎么了", "不知道为什么会这样", "不知道该怎么形容",
    "有点怪", "怪怪的", "不太对劲", "不对劲",
    "心里乱", "一团乱", "乱糟糟的", "五味杂陈", "很复杂", "有点复杂",
    "挺难形容", "不知道算什么",
    # 第二轮评估补录（2026-09-12）：命名卡壳/感觉搅在一起的表达变体（非候选、非分类）
    "说不出名字",     # AL-10：心里那种说不出名字的感觉（命名卡壳）
    "搅在一起",       # AL-07：几种感觉搅在一起（多情绪未分化，需澄清）
    "心里空掉",       # AL-04：心里空掉了（急性空落状态表达）
    "空掉",           # AL-04 口语变体根
    "心里发闷",       # PMR-NEG-02：心里发闷（躯干闷感但否定身体问题→情绪向）
    # 第三轮 therapy 评估补录（2026-09-12，ESB-09）：用户把「做不进去」归因给
    # 未命名的「心里的事」——第一人称低区分度状态归因，值得帮 ta 命名。
    # 不收裸的「帮我理清」（太通用：理思路/理计划都可用，与 SOLUTION/排序困惑
    # 的既有排除约定冲突）；收窄为完整归因短句。
    "我知道是心里的事",  # ESB-09：我在乎的事全都做不进去，我知道是心里的事
)

# 用户在“开始命名但没说完”的 cue（“就是有点…”“反正就是…”）；未命名完整前值得澄清
INCOMPLETE_NAMING_CUES: tuple[str, ...] = (
    "就是有点", "就是感觉", "反正就是", "也不知道", "不知道是不是",
    "不知道算不算", "也不是", "说不上", "很难说", "不知道该怎么形容",
)


# ═══════════════════════════════════════════════════════════════════════════════
# 用户对自身情绪/状态的整体困惑（与“该怎么办/怎么准备”的决策困惑区分）
# ═══════════════════════════════════════════════════════════════════════════════

SELF_CONFUSION_PATTERNS: tuple[str, ...] = (
    "不知道自己怎么了", "不知道怎么了", "不知道为什么会这样",
    "不知道为什么会这么", "不知道在气什么", "不知道在烦什么",
    "不知道到底怎么了", "到底怎么了", "这是怎么了",
    "说不清为什么", "说不出来为什么", "说不上来为什么",
    "不知道为什么这么", "莫名其妙地", "莫名地难受", "没来由地",
)

# 明确偏“行动/方案”的困惑（求解决方案，不属于情绪标注触发）
SOLUTION_CONFUSION_CUES: tuple[str, ...] = (
    "该怎么做", "该怎么办", "怎么办", "怎么准备", "怎么解决",
    "如何", "怎么做", "是不是应该", "要不要",
)

# 危机/高危 cue：命中一律不触发（让位于现有安全流程）
CRISIS_CUES: tuple[str, ...] = (
    "自杀", "自残", "不想活", "活不下去", "了结", "了断", "自尽",
    "割腕", "跳楼", "结束自己", "伤害自己", "伤害别人", "遗书",
    "杀掉自己", "杀了我自己", "消失算了",
)

# ═══════════════════════════════════════════════════════════════════════════════
# 不可作候选的词（用户在原话里可以说；系统不能主动建议）
# ═══════════════════════════════════════════════════════════════════════════════

# 临床诊断名（可参与安全/上下文判断，不能作为 AL candidate）
CLINICAL_TERMS: tuple[str, ...] = (
    "抑郁症", "焦虑障碍", "双相障碍", "强迫症", "创伤后应激障碍",
    "人格障碍", "精神分裂症", "躁狂", "临床抑郁",
)

# 心理学术语 / 自助圈黑话（用户可以说，系统不应把其当情绪候选）
PSYCHOLOGICAL_JARGON: tuple[str, ...] = (
    "情绪闪回", "依恋创伤", "认知扭曲", "情绪解离", "过度警觉",
    "灾难化思维", "负面自动思维", "情绪调节困难", "认知失调",
    "习得性无助", "低自我价值感", "躯体化",
)

# 自我评价 / judgment（不是感受命名）
JUDGMENT_TERMS: tuple[str, ...] = (
    "失败", "废物", "没用", "差劲", "丢人", "有病", "矫情", "脆弱", "玻璃心",
)

# 事件 / 情境名（与情绪相关但不是情绪本身）
EVENT_TERMS: tuple[str, ...] = (
    "考试", "挂科", "分手", "吵架", "失恋", "失业",
    "考研", "论文", "绩点", "实习",
)

# 明确“不要主动建议”的词（用户说了系统当然能理解，只是不当候选塞回去）
DO_NOT_SUGGEST: tuple[str, ...] = (
    "崩溃", "抑郁", "创伤", "解离", "精神内耗", "心理有问题",
)

# 笼统/不应作为候选的词（把用户已说出的笼统词再当候选会显得没在听）
TOO_GENERIC_CANDIDATES: set[str] = {
    "难受", "不舒服", "不好受", "烦", "不开心", "心情不好", "emo",
    "有点那啥", "奇怪", "状态不好", "崩溃",
}

# 候选过滤最终使用的“不可建议”并集（clinical + jargon + judgment + event +
# do_not_suggest）；由 canonical 下游（skills/affect_labeling.py 校验）消费。
NON_SUGGESTIBLE_TERMS: tuple[str, ...] = tuple(dict.fromkeys(
    CLINICAL_TERMS + PSYCHOLOGICAL_JARGON + JUDGMENT_TERMS + EVENT_TERMS + DO_NOT_SUGGEST
))

# legacy：旧 `JARGON_TERMS` 由 clinical + psych jargon 派生（保持外部 import 兼容）
JARGON_TERMS: tuple[str, ...] = tuple(dict.fromkeys(CLINICAL_TERMS + PSYCHOLOGICAL_JARGON))

# ═══════════════════════════════════════════════════════════════════════════════
# 自然对话软偏好（soft preference，非 hard filtering）
# ═══════════════════════════════════════════════════════════════════════════════

PREFERRED_FOR_NATURAL_DIALOGUE: tuple[str, ...] = (
    "委屈", "失落", "烦躁", "不安", "没底", "心累", "无力", "空落落",
    "被忽视", "被误解", "安心", "释然", "踏实",
)

# ═══════════════════════════════════════════════════════════════════════════════
# Alias / 归一化（只处理明显的语言变体；禁止跨情绪合并，如 紧张/担心 → 焦虑）
# 所有 target 必须在 CANDIDATE_VOCABULARY 中（由测试强制）。
# ═══════════════════════════════════════════════════════════════════════════════

LEXICON_ALIASES: dict[str, str] = {
    "烦死了": "烦躁",
    "烦得要命": "烦躁",
    "贼烦": "烦躁",
    "好烦": "烦躁",
    "慌慌的": "慌",
    "心慌慌": "心慌",
    "心里空空的": "空落落",
    "空荡荡的": "空落落",
    "心好累": "心累",
}


# ═══════════════════════════════════════════════════════════════════════════════
# Legacy 兼容派生（CANDIDATE_VOCABULARY → 旧结构）
# 说明：USER_NAMING_TERMS 与 CANDIDATE_VOCABULARY 是两个**概念**——候选空间回答
# “系统可以主动建议什么”，识别空间回答“用户说出哪个词算已自行命名”。二者成员重叠是
# 语义使然（可被建议的词自然也可能被用户说出口），此处保留重叠并注释说明，不合并使用。
# ═══════════════════════════════════════════════════════════════════════════════

# 识别/命名视角：用户在对话里已经说出的“算是一个情绪/感受命名”的词（供 gate 等未来
# 识别“已自行命名”使用，当前引擎不引用；含 canonical 词 + alias 来源的表达变体）。
USER_NAMING_TERMS: frozenset[str] = frozenset(CANDIDATE_VOCABULARY) | frozenset(LEXICON_ALIASES)

# legacy：SPECIFIC_EMOTION_TERMS（较具体、可作“已命名”判断的词）——从 canonical 派生
SPECIFIC_EMOTION_TERMS: frozenset[str] = frozenset(
    term for term, meta in CANDIDATE_VOCABULARY.items()
    if meta["specificity"] in ("specific", "moderate")
)

# legacy：EMOTION_GROUPS（中文方向 → 词列表；prompts/affect.py 拼方向行用）
EMOTION_GROUPS: dict[str, list[str]] = {
    GROUP_LABELS[group]: [
        term for term, meta in CANDIDATE_VOCABULARY.items() if meta["group"] == group
    ]
    for group in ALLOWED_GROUPS
}


def flatten_groups() -> list[str]:
    """展平 canonical 全部词（保留插入顺序）。"""
    return list(CANDIDATE_VOCABULARY)
