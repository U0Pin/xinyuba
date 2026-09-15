import os
from dotenv import load_dotenv

load_dotenv()

_AGENT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class Config:
    # ── 旧字段（保持不变，旧架构继续使用） ───────────────────────────
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    OPENAI_BASE_URL: str = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    MODEL_NAME: str = os.getenv("MODEL_NAME", "gpt-4o")
    GATING_MODEL_NAME: str = os.getenv("GATING_MODEL_NAME", "qwen-turbo")
    TEMPERATURE: float = float(os.getenv("TEMPERATURE", 0.7))
    MAX_TOKENS: int = int(os.getenv("MAX_TOKENS", 4096))

    # ── 新架构参数（refactor v2，Phase 1 起生效） ────────────────────
    # 决策线廉价模型（安全决策等）
    CHEAP_MODEL_NAME: str = os.getenv("CHEAP_MODEL_NAME", GATING_MODEL_NAME)
    # 疗法决策滑动窗口大小（轮）
    WINDOW_SIZE: int = int(os.getenv("WINDOW_SIZE", "5"))
    # 疗法决策 token 触发阈值：正数 = 累计用户输入 token 达到该值才评估；
    # 0 = 关闭限制（每条消息都评估最新窗口）。
    # ⚠️ 阈值只控制「评估频率」，不控制「是否触发」——是否触发始终由疗法
    # 决策器按信号规则判断（闲聊/仅需倾听 → 不需要疗法，不触发）。
    # 注意：token 估算口径为 字符数/2（中文 1 字≈0.6-0.7 token）。
    # 默认 0；生产如需减少决策器调用成本可调大。
    WINDOW_TOKEN_THRESHOLD: int = int(os.getenv("WINDOW_TOKEN_THRESHOLD", "0"))
    # 疗程工作轮数上限
    THERAPY_MAX_ROUNDS: int = int(os.getenv("THERAPY_MAX_ROUNDS", "10"))
    # 对话线读取的近 N 轮窗口
    RECENT_TURNS: int = int(os.getenv("RECENT_TURNS", "3"))
    # 摘要重生成 token 阈值（估算口径同 WINDOW_TOKEN_THRESHOLD：字符数/2）
    SUMMARY_TOKEN_THRESHOLD: int = int(os.getenv("SUMMARY_TOKEN_THRESHOLD", "1500"))

    # ── 模型单价（USD / 1M tokens），用于 estimated_cost 记账 ────────
    # 单价为可调估计值，随服务商调价更新即可。
    MODEL_PRICES: dict = {
        "deepseek-chat": (0.27, 1.10),   # input, output
        "qwen-turbo": (0.30, 1.20),
        "gpt-4o": (2.50, 10.00),
    }

    # ── 存盘与日志路径（新架构） ────────────────────────────────────
    DATA_ROOT: str = os.getenv("DATA_ROOT", os.path.join(_AGENT_DIR, "data"))
    LOG_DIR: str = os.getenv("LOG_DIR", os.path.join(_AGENT_DIR, "logs"))

    @classmethod
    def model_price(cls, model: str) -> tuple[float, float]:
        """Return (input_usd_per_1M, output_usd_per_1M)；未知模型按 0 计。"""
        return cls.MODEL_PRICES.get(model, (0.0, 0.0))


config = Config()
