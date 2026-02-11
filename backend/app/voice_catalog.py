from __future__ import annotations

from .models import VoiceInfo


VOICE_INFOS: list[VoiceInfo] = [
    VoiceInfo(id="zh-CN-YunxiNeural", name="雲希", gender="male", age="young", description="清晰穩重"),
    VoiceInfo(id="zh-CN-YunyangNeural", name="雲揚", gender="male", age="young", description="活潑陽光"),
    VoiceInfo(id="zh-CN-YunjianNeural", name="雲健", gender="male", age="middle", description="成熟威嚴"),
    VoiceInfo(id="zh-CN-YunzeNeural", name="雲澤", gender="male", age="young", description="溫和親切"),
    VoiceInfo(id="zh-CN-XiaoxiaoNeural", name="曉曉", gender="female", age="young", description="溫柔甜美"),
    VoiceInfo(id="zh-CN-XiaoyiNeural", name="曉伊", gender="female", age="young", description="活潑可愛"),
    VoiceInfo(id="zh-CN-XiaohanNeural", name="曉涵", gender="female", age="middle", description="優雅知性"),
    VoiceInfo(id="zh-CN-XiaomengNeural", name="曉夢", gender="female", age="young", description="清純夢幻"),
    VoiceInfo(id="zh-CN-XiaomoNeural", name="曉墨", gender="female", age="young", description="冷靜專業"),
    VoiceInfo(id="zh-CN-XiaoruiNeural", name="曉睿", gender="female", age="young", description="聰慧機智"),
]


def recommend_voice(role_text: str, personality: str = "") -> str:
    content = f"{role_text} {personality}"
    if any(word in content for word in ["女", "少女", "公主", "女主"]):
        if any(word in content for word in ["活潑", "可愛", "俏皮"]):
            return "zh-CN-XiaoyiNeural"
        if any(word in content for word in ["冷", "理性", "專業"]):
            return "zh-CN-XiaomoNeural"
        return "zh-CN-XiaoxiaoNeural"
    if any(word in content for word in ["長者", "師父", "權威", "反派"]):
        return "zh-CN-YunjianNeural"
    if any(word in content for word in ["少年", "熱血", "活力"]):
        return "zh-CN-YunyangNeural"
    return "zh-CN-YunxiNeural"

