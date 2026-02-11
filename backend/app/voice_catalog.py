from __future__ import annotations

from .models import VoiceInfo


VOICE_INFOS: list[VoiceInfo] = [
    VoiceInfo(id="zh-CN-YunxiNeural", name="Yunxi", gender="male", age="young", description="Clear and steady"),
    VoiceInfo(id="zh-CN-YunyangNeural", name="Yunyang", gender="male", age="young", description="Energetic and bright"),
    VoiceInfo(id="zh-CN-YunjianNeural", name="Yunjian", gender="male", age="middle", description="Mature and authoritative"),
    VoiceInfo(id="zh-CN-YunzeNeural", name="Yunze", gender="male", age="young", description="Warm and friendly"),
    VoiceInfo(id="zh-CN-XiaoxiaoNeural", name="Xiaoxiao", gender="female", age="young", description="Soft and sweet"),
    VoiceInfo(id="zh-CN-XiaoyiNeural", name="Xiaoyi", gender="female", age="young", description="Lively and cute"),
    VoiceInfo(id="zh-CN-XiaohanNeural", name="Xiaohan", gender="female", age="middle", description="Elegant and calm"),
    VoiceInfo(id="zh-CN-XiaomengNeural", name="Xiaomeng", gender="female", age="young", description="Dreamy and pure"),
    VoiceInfo(id="zh-CN-XiaomoNeural", name="Xiaomo", gender="female", age="young", description="Cool and professional"),
    VoiceInfo(id="zh-CN-XiaoruiNeural", name="Xiaorui", gender="female", age="young", description="Smart and sharp"),
]


def recommend_voice(role_text: str, personality: str = "") -> str:
    content = f"{role_text} {personality}".lower()

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

