"""Goal classification exercise for the teaching and assessment workflow."""
from typing import Literal
import json
from app.llm import chat
from app.prompts import GOAL_ROUTER_SYSTEM
ASSESS_KEYWORDS = [
    "考考我", "开始测验", "给我出题", "出几道题",
    "测试我的水平", "检验我的学习成果", "评估我的理解",
    "检查掌握情况", "测测我", "考察一下我",
    "开始考试", "模拟考试", "随堂测验", "小测验",
    "知识测试", "做几道练习题", "测试一下我",
    "看看我掌握得怎么样", "挑战一下我", "进行考核"
]

TEACH_KEYWORDS = [
    "开始讲解", "分步讲解", "详细讲解", "解释一下",
    "教我", "带我学习", "从零开始学", "一步一步教我",
    "讲一下原理", "说明原理", "怎么理解", "为什么",
    "是什么", "怎么使用", "如何使用", "举例说明",
    "帮我理解", "学习一下", "详细说明", "展开讲讲"
]

DIRECT_KEYWORDS = [
    "你好", "您好", "嗨", "哈喽", "hello", "hi",
    "谢谢", "感谢", "非常感谢", "辛苦了", "多谢",
    "再见", "拜拜", "回头见", "晚安", "早上好",
    "下午好", "晚上好", "good morning", "goodbye",
    "thanks", "thank you"
]

def classify_goal(
    question: str,
    selected_mode: str | None = None,
) -> Literal["teach", "assess", "direct"]:
    """优先采用显式模式，完整匹配明确指令，其余问题交给模型分类。"""
    question = question.strip()
    if not question:
        raise ValueError("问题不能为空。")
    allowed_modes = ("teach", "assess", "direct")
    if selected_mode is not None:
        selected_mode = selected_mode.strip()
        if selected_mode not in allowed_modes:
            raise ValueError("模式须为 teach、assess 或 direct。")
        return selected_mode
    text = question.lower().strip(" \t\r\n，。！？、,.!?;；:：")
    if text in ASSESS_KEYWORDS:
        return "assess"
    if text in TEACH_KEYWORDS:
        return "teach"
    if text in DIRECT_KEYWORDS:
        return "direct"
    response = chat([
            {"role": "system", "content": GOAL_ROUTER_SYSTEM},
            {"role": "user", "content": question}
        ], json_mode=True)
    payload = json.loads(response)
    if not isinstance(payload, dict):
        raise ValueError("模型分类结果须为 JSON 对象。")
    mode = payload.get("goal_class")
    if mode not in allowed_modes:
        raise ValueError("模型返回了无效的 goal_class。")
    return mode
