from __future__ import annotations

from .models import VoiceInfo


VOICE_INFOS: list[VoiceInfo] = [
    VoiceInfo(id="zh-CN-YunxiNeural", name="Yunxi", gender="male", age="young", description="Male voice, clear and steady"),
    VoiceInfo(id="zh-CN-YunyangNeural", name="Yunyang", gender="male", age="young", description="Male voice, energetic and bright"),
    VoiceInfo(id="zh-CN-YunjianNeural", name="Yunjian", gender="male", age="middle", description="Male voice, mature and authoritative"),
    VoiceInfo(id="zh-CN-YunzeNeural", name="Yunze", gender="male", age="young", description="Male voice, warm and friendly"),
    VoiceInfo(id="zh-CN-XiaoxiaoNeural", name="Xiaoxiao", gender="female", age="young", description="Female voice, soft and sweet"),
    VoiceInfo(id="zh-CN-XiaoyiNeural", name="Xiaoyi", gender="female", age="young", description="Female voice, lively and cute"),
    VoiceInfo(id="zh-CN-XiaohanNeural", name="Xiaohan", gender="female", age="middle", description="Female voice, elegant and calm"),
    VoiceInfo(id="zh-CN-XiaomengNeural", name="Xiaomeng", gender="female", age="young", description="Female voice, dreamy and pure"),
    VoiceInfo(id="zh-CN-XiaomoNeural", name="Xiaomo", gender="female", age="young", description="Female voice, cool and professional"),
    VoiceInfo(id="zh-CN-XiaoruiNeural", name="Xiaorui", gender="female", age="young", description="Female voice, smart and sharp"),
]


def _normalize_gender(gender_hint: str | None) -> str:
    value = str(gender_hint or "").strip().lower()
    if value in {"male", "man", "m", "boy"}:
        return "male"
    if value in {"female", "woman", "f", "girl"}:
        return "female"
    return "unknown"


def recommend_voice(role_text: str, personality: str = "", gender_hint: str | None = None) -> str:
    content = f"{role_text} {personality}".lower()
    normalized_gender = _normalize_gender(gender_hint)

    if normalized_gender == "female":
        if any(word in content for word in ["lively", "cute", "playful"]):
            return "zh-CN-XiaoyiNeural"
        if any(word in content for word in ["cold", "calm", "professional"]):
            return "zh-CN-XiaomoNeural"
        return "zh-CN-XiaoxiaoNeural"

    if normalized_gender == "male":
        if any(word in content for word in ["elder", "mentor", "authority", "villain"]):
            return "zh-CN-YunjianNeural"
        if any(word in content for word in ["young", "passionate", "active", "teen"]):
            return "zh-CN-YunyangNeural"
        return "zh-CN-YunxiNeural"

    if any(word in content for word in ["female", "girl", "princess", "heroine", "woman"]):
        if any(word in content for word in ["lively", "cute", "playful"]):
            return "zh-CN-XiaoyiNeural"
        if any(word in content for word in ["cold", "calm", "professional"]):
            return "zh-CN-XiaomoNeural"
        return "zh-CN-XiaoxiaoNeural"

    if any(word in content for word in ["elder", "mentor", "authority", "villain"]):
        return "zh-CN-YunjianNeural"

    if any(word in content for word in ["young", "passionate", "active", "teen"]):
        return "zh-CN-YunyangNeural"

    return "zh-CN-YunxiNeural"
