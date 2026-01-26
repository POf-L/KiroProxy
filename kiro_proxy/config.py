"""配置模块"""
from pathlib import Path

KIRO_API_URL = "https://q.us-east-1.amazonaws.com/generateAssistantResponse"
MODELS_URL = "https://q.us-east-1.amazonaws.com/ListAvailableModels"

# 统一数据目录 (所有配置文件都在这里)
DATA_DIR = Path.home() / ".kiro-proxy"

# Token 存储目录
TOKEN_DIR = DATA_DIR / "tokens"

# 默认 Token 路径 (兼容旧代码)
TOKEN_PATH = TOKEN_DIR / "kiro-auth-token.json"

# 配额管理配置
QUOTA_COOLDOWN_SECONDS = 300  # 配额超限冷却时间（秒）

# 模型映射
MODEL_MAPPING = {
    # Claude 3.5 -> Kiro Claude 4
    "claude-3-5-sonnet-20241022": "claude-sonnet-4",
    "claude-3-5-sonnet-latest": "claude-sonnet-4",
    "claude-3-5-sonnet": "claude-sonnet-4",
    "claude-3-5-haiku-20241022": "claude-haiku-4.5",
    "claude-3-5-haiku-latest": "claude-haiku-4.5",
    # Claude 3
    "claude-3-opus-20240229": "claude-sonnet-4.5",
    "claude-3-opus-latest": "claude-sonnet-4.5",
    "claude-3-sonnet-20240229": "claude-sonnet-4",
    "claude-3-haiku-20240307": "claude-haiku-4.5",
    # Claude 4
    "claude-4-sonnet": "claude-sonnet-4",
    "claude-4-opus": "claude-sonnet-4.5",
    # OpenAI GPT -> Claude
    "gpt-4o": "claude-sonnet-4",
    "gpt-4o-mini": "claude-haiku-4.5",
    "gpt-4-turbo": "claude-sonnet-4",
    "gpt-4": "claude-sonnet-4",
    "gpt-3.5-turbo": "claude-haiku-4.5",
    # OpenAI o1 -> Claude Opus
    "o1": "claude-sonnet-4.5",
    "o1-preview": "claude-sonnet-4.5",
    "o1-mini": "claude-sonnet-4",
    # Gemini -> Claude
    "gemini-2.0-flash": "claude-sonnet-4",
    "gemini-2.0-flash-thinking": "claude-sonnet-4.5",
    "gemini-1.5-pro": "claude-sonnet-4.5",
    "gemini-1.5-flash": "claude-sonnet-4",
    # 别名
    "sonnet": "claude-sonnet-4",
    "haiku": "claude-haiku-4.5",
    "opus": "claude-sonnet-4.5",
}

KIRO_MODELS = {"auto", "claude-sonnet-4.5", "claude-sonnet-4", "claude-haiku-4.5"}

def get_best_model_by_tier(tier: str, available_models: set = None) -> str:
    """根据等级获取最佳可用模型（等级对等 + 智能降级）"""
    if available_models is None:
        available_models = KIRO_MODELS

    # 等级对等映射 + 降级路径
    TIER_PRIORITIES = {
        # Opus: 最强 → 次强 → 快速 → 自动
        "opus": ["claude-sonnet-4.5", "claude-sonnet-4", "claude-haiku-4.5", "auto"],

        # Sonnet: 高性能 → 最强 → 标准 → 快速 → 自动
        "sonnet": ["claude-sonnet-4.5", "claude-sonnet-4", "claude-haiku-4.5", "auto"],

        # Haiku: 快速 → 标准 → 高性能 → 自动
        "haiku": ["claude-haiku-4.5", "claude-sonnet-4", "claude-sonnet-4.5", "auto"],
    }

    priorities = TIER_PRIORITIES.get(tier, TIER_PRIORITIES["sonnet"])

    # 选择第一个可用的模型
    for model in priorities:
        if model in available_models:
            return model

    return "auto"  # 最终回退


def detect_model_tier(model: str) -> str:
    """智能检测模型等级"""
    if not model:
        return "sonnet"  # 默认中等

    model_lower = model.lower()

    # 特殊模型优先检测（避免被通用关键词误判）
    if "gemini" in model_lower:
        if any(keyword in model_lower for keyword in ["1.5-pro", "pro"]):
            return "opus"
        elif any(keyword in model_lower for keyword in ["2.0", "flash"]):
            return "sonnet"  # Gemini 2.0 和 flash 系列归为 sonnet

    # 等级关键词检测（优先级从高到低）
    # Opus 等级 - 最强模型
    if any(keyword in model_lower for keyword in ["opus", "o1", "max", "ultra", "premium"]):
        return "opus"

    # Haiku 等级 - 快速模型（需要排除 sonnet 中的 3.5）
    if any(keyword in model_lower for keyword in ["haiku", "mini", "light", "fast", "turbo"]):
        return "haiku"
    # 特殊处理：gpt-3.5 系列属于 haiku
    if "3.5" in model_lower and "sonnet" not in model_lower:
        return "haiku"

    # Sonnet 等级 - 平衡模型
    if any(keyword in model_lower for keyword in ["sonnet", "4o", "4", "standard", "base"]):
        return "sonnet"

    return "sonnet"  # 默认中等


def map_model_name(model: str, available_models: set = None) -> str:
    """将外部模型名称映射到 Kiro 支持的名称（支持动态模型选择）"""
    if not model:
        return "auto"

    # 1. 精确匹配优先
    if model in MODEL_MAPPING:
        return MODEL_MAPPING[model]
    if model in KIRO_MODELS:
        return model

    # 2. 智能等级检测 + 动态选择
    tier = detect_model_tier(model)
    best_model = get_best_model_by_tier(tier, available_models)

    return best_model
