"""画像页评估 prompt：ACT 六维心理指标 + 九型人格判定。

输入为沉淀线维护的用户画像（长期稳定特征），输出供画像页雷达图与人格卡。
"""

# 九型人格枚举（与客户端 ProfileScreen 展示一一对应）
PERSONALITY_TYPES = [
    "beginner",        # 初识者
    "night_watcher",   # 守夜人
    "watcher",         # 守望者
    "storm_rider",     # 风暴骑手
    "navigator",       # 航海者
    "anchor",          # 定锚者
    "light_weaver",    # 织光者
    "lone_wanderer",   # 独行者
    "wind_messenger",  # 风信使
]

# ACT 六维（0~1 浮点；cognitive_fusion / experiential_avoidance 为困扰向，越高越困扰）
ACT_DIMENSIONS = [
    "psychological_flexibility",  # 走出情绪
    "emotional_openness",         # 接纳感受
    "cognitive_readiness",        # 适合深聊
    "cognitive_fusion",           # 想法绑架
    "experiential_avoidance",     # 躲避情绪
    "values_alignment",           # 活出自己
]

PORTRAIT_EVAL_PROMPT = """你是用户心理画像评估模块。

基于用户画像（从长期对话中沉淀的稳定特征），评估六项心理指标并判定人格类型。

六项指标均为 0~1 浮点：
- psychological_flexibility：多容易从坏情绪里走出来继续生活（越高越好）；
- emotional_openness：愿不愿意承认和接纳自己的感受（越高越好）；
- cognitive_readiness：当前状态适不适合做深度自我剖析（越高越好）；
- cognitive_fusion：多容易把负面念头当真、被它牵着走（越高越困扰）；
- experiential_avoidance：多习惯用刷手机/忙起来等方式躲开情绪（越高越困扰）；
- values_alignment：日常行为与自己在乎的东西的对齐程度（越高越好）。

人格类型九选一：beginner（初识者）/ night_watcher（守夜人）/ watcher（守望者）/
storm_rider（风暴骑手）/ navigator（航海者）/ anchor（定锚者）/
light_weaver（织光者）/ lone_wanderer（独行者）/ wind_messenger（风信使）。
依据画像整体气质判定：情绪困扰重、回避与融合偏高者偏 night_watcher / storm_rider /
lone_wanderer；灵活性与价值对齐高者偏 navigator / anchor / light_weaver；
信息不足、特征未显时判 beginner。

再输出 value_words：用户在乎的东西的价值词（如 家庭、自由、成长、稳定），
从画像与对话中提取，0~5 个，信息不足时给空列表。

画像信息不足时也要给出保守估计（中间值附近），不要拒绝输出。

Output only the JSON object. No other text."""

PORTRAIT_OUTPUT_SCHEMA = """
{
  "act_metrics": {
    "psychological_flexibility": 0.5,
    "emotional_openness": 0.5,
    "cognitive_readiness": 0.5,
    "cognitive_fusion": 0.5,
    "experiential_avoidance": 0.5,
    "values_alignment": 0.5
  },
  "personality": "beginner",
  "value_words": ["家庭", "成长"]
}
"""
