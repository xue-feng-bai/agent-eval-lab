"""配置加载：.env 解析（纯 stdlib，无 python-dotenv 依赖）、configs/*.json 加载、配置快照。

设计要点：
- .env 只支持最简单的 KEY=VALUE（# 注释、空行、export 前缀、值两侧引号），
  覆盖 99% 使用场景，且语义透明可预期；
- 环境变量优先级：真实环境变量 > .env 文件（不覆盖已有环境）；
- 配置快照用于 meta.json，API Key 一律脱敏（只记录"是否已配置"）。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIGS_DIR = PROJECT_ROOT / "configs"


def parse_env_file(path: str | Path) -> dict[str, str]:
    """解析 .env 文件为 dict；文件不存在返回空 dict（不报错）。"""
    path = Path(path)
    result: dict[str, str] = {}
    if not path.exists():
        return result
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        if "=" not in line:
            raise ValueError(f"{path.name}:{lineno}: 非法行（缺少 '='）: {raw!r}")
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        # 去掉成对的包围引号
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        result[key] = value
    return result


def load_env(root: str | Path | None = None) -> dict[str, str]:
    """把 .env 载入 os.environ（不覆盖已存在的环境变量），返回合并后的视图。"""
    root = Path(root) if root else PROJECT_ROOT
    file_vars = parse_env_file(root / ".env")
    for key, value in file_vars.items():
        os.environ.setdefault(key, value)
    merged = dict(file_vars)
    merged.update(os.environ)
    return merged


def load_json_config(name: str) -> dict:
    """加载 configs/<name>.json（name 不带后缀）。"""
    path = CONFIGS_DIR / f"{name}.json"
    if not path.exists():
        raise FileNotFoundError(f"配置文件不存在: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def get_llm_config(role: str = "agent", env: dict[str, str] | None = None) -> dict:
    """取 LLM 接入配置。role='judge' 时优先 JUDGE_*，留空则回退 LLM_*。"""
    env = env or load_env()
    base = {
        "api_key": env.get("LLM_API_KEY", ""),
        "base_url": env.get("LLM_BASE_URL", ""),
        "model": env.get("LLM_MODEL", ""),
    }
    if role == "judge":
        return {
            "api_key": env.get("JUDGE_API_KEY", "") or base["api_key"],
            "base_url": env.get("JUDGE_BASE_URL", "") or base["base_url"],
            "model": env.get("JUDGE_MODEL", "") or base["model"],
        }
    return base


def resolve_model_prices(model_name: str | None) -> dict[str, float]:
    """按 models.json 查单价；未注册模型按 0 计价（并在调用方注明）。"""
    if not model_name:
        return {"prompt_price_per_1m": 0.0, "completion_price_per_1m": 0.0}
    models = load_json_config("models").get("models", {})
    entry = models.get(model_name.lower()) or models.get(model_name)
    if not entry:
        return {"prompt_price_per_1m": 0.0, "completion_price_per_1m": 0.0}
    return {
        "prompt_price_per_1m": float(entry.get("prompt_price_per_1m", 0.0)),
        "completion_price_per_1m": float(entry.get("completion_price_per_1m", 0.0)),
    }


def compute_cost_usd(model_name: str | None, prompt_tokens: int, completion_tokens: int) -> float:
    """按 models.json 单价折算一次调用的美元成本。"""
    prices = resolve_model_prices(model_name)
    return round(
        prompt_tokens * prices["prompt_price_per_1m"] / 1_000_000
        + completion_tokens * prices["completion_price_per_1m"] / 1_000_000,
        8,
    )


def snapshot_config(extra: dict | None = None) -> dict:
    """生成 meta.json 用的配置快照；API Key 只记录是否已配置，绝不落盘明文。"""
    env = load_env()
    snap = {
        "llm": {
            "base_url": env.get("LLM_BASE_URL", ""),
            "model": env.get("LLM_MODEL", ""),
            "api_key_configured": bool(env.get("LLM_API_KEY")),
        },
        "judge": {
            "model": env.get("JUDGE_MODEL", "") or env.get("LLM_MODEL", ""),
            "api_key_configured": bool(env.get("JUDGE_API_KEY") or env.get("LLM_API_KEY")),
        },
        "default_config": load_json_config("default"),
    }
    if extra:
        snap.update(extra)
    return snap
