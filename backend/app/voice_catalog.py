from __future__ import annotations


VOICE_MAP: dict[str, dict] = {
    "zh-CN-YunxiNeural": {
        "name": "雲希",
        "gender": "male",
        "age": "young",
        "traits": ["清晰", "穩重", "磁性", "冷靜", "理性"],
        "suitable_for": ["主角", "劍客", "學者", "冷靜型角色"],
    },
    "zh-CN-YunyangNeural": {
        "name": "雲揚",
        "gender": "male",
        "age": "young",
        "traits": ["活潑", "陽光", "活力", "熱情", "積極"],
        "suitable_for": ["少年主角", "熱血少年", "活潑型角色"],
    },
    "zh-CN-YunjianNeural": {
        "name": "雲健",
        "gender": "male",
        "age": "middle",
        "traits": ["成熟", "穩重", "威嚴", "沉穩", "權威"],
        "suitable_for": ["長者", "師父", "權威人物", "反派"],
    },
    "zh-CN-YunzeNeural": {
        "name": "雲澤",
        "gender": "male",
        "age": "young",
        "traits": ["溫和", "親切", "可靠", "溫暖", "友善"],
        "suitable_for": ["配角", "朋友", "溫和型角色"],
    },
    "zh-CN-XiaoxiaoNeural": {
        "name": "曉曉",
        "gender": "female",
        "age": "young",
        "traits": ["溫柔", "甜美", "清澈", "善良"],
        "suitable_for": ["女主", "溫柔型女性", "公主"],
    },
    "zh-CN-XiaoyiNeural": {
        "name": "曉伊",
        "gender": "female",
        "age": "young",
        "traits": ["活潑", "可愛", "俏皮", "開朗"],
        "suitable_for": ["少女", "活潑型女性", "妹妹型角色"],
    },
    "zh-CN-XiaohanNeural": {
        "name": "曉涵",
        "gender": "female",
        "age": "middle",
        "traits": ["優雅", "知性", "沉穩", "理性"],
        "suitable_for": ["成熟女性", "女強人", "長者"],
    },
    "zh-CN-XiaomengNeural": {
        "name": "曉夢",
        "gender": "female",
        "age": "young",
        "traits": ["清純", "柔和", "夢幻", "純真"],
        "suitable_for": ["純真少女", "仙女", "夢幻型角色"],
    },
    "zh-CN-XiaomoNeural": {
        "name": "曉墨",
        "gender": "female",
        "age": "young",
        "traits": ["冷靜", "理性", "專業", "冷酷"],
        "suitable_for": ["女劍客", "冷酷型女性", "專業人士"],
    },
    "zh-CN-XiaoruiNeural": {
        "name": "曉睿",
        "gender": "female",
        "age": "young",
        "traits": ["聰慧", "靈動", "機智", "聰明"],
        "suitable_for": ["智慧型女性", "謀士", "機智型角色"],
    },
}


def recommend_voice(role_text: str, personality: str = "") -> str:
    content = f"{role_text} {personality}".lower()
    if any(token in content for token in ["女", "少女", "公主", "女主"]):
        if any(token in content for token in ["活潑", "可愛", "俏皮"]):
            return "zh-CN-XiaoyiNeural"
        if any(token in content for token in ["冷", "理性", "專業"]):
            return "zh-CN-XiaomoNeural"
        return "zh-CN-XiaoxiaoNeural"
    if any(token in content for token in ["長者", "師父", "權威", "反派"]):
        return "zh-CN-YunjianNeural"
    if any(token in content for token in ["少年", "熱血", "活力"]):
        return "zh-CN-YunyangNeural"
    return "zh-CN-YunxiNeural"

