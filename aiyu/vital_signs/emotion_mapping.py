"""情感→生理指标映射表。"""

from __future__ import annotations

EMOTION_VITAL_MAP: dict[str, dict[str, float]] = {
    "开心": {"heart_rate_delta": 5, "temperature_delta": 0.1, "breath_rate_delta": 1},
    "伤心": {"heart_rate_delta": -3, "temperature_delta": -0.1, "breath_rate_delta": -1},
    "生气": {"heart_rate_delta": 30, "temperature_delta": 0.3, "breath_rate_delta": 5},
    "害怕": {"heart_rate_delta": 20, "temperature_delta": 0.2, "breath_rate_delta": 4},
    "撒娇": {"heart_rate_delta": 8, "temperature_delta": 0.1, "breath_rate_delta": 1},
    "害羞": {"heart_rate_delta": 15, "temperature_delta": 0.2, "breath_rate_delta": 2},
    "惊讶": {"heart_rate_delta": 12, "temperature_delta": 0.1, "breath_rate_delta": 2},
    "平静": {"heart_rate_delta": 0, "temperature_delta": 0, "breath_rate_delta": 0},
}
