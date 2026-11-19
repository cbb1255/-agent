"""DeepSeek 客户端（框架已实现，直接使用）。
作用就是先创建客户端装饰器可以实现缓存，如果没有配置apikey就抛一个异常
返回一个指向 DeepSeek 服务。然后发送聊天请求配置然后如果json_model为真就设置结构化输出
然后就向模型发送请求最后提取第一个 choice 的文本内容返回，若为空则返回 ""
用法示例：
    from app.llm import chat
    text = chat([{"role": "user", "content": "你好"}])
    text = chat(msgs, json_mode=True)   # 返回 JSON 字符串，用于意图分类
"""
from functools import lru_cache, wraps
from contextlib import contextmanager
from contextvars import ContextVar
import threading
import time
import logging
import json
from uuid import uuid4
from typing import Iterator
from openai import OpenAI, OpenAIError
from app.config import get_settings


_RESPONSE_STREAM = ContextVar('response_stream', default=None)
_RESPONSE_RESET = ContextVar('response_reset', default=None)
_last_usage = ContextVar('last_model_usage', default=None)
_USAGE_REQUEST = ContextVar('model_usage_request', default=None)
_MODEL_CALL = ContextVar('current_model_call', default=None)
LOGGER = logging.getLogger(__name__)


@contextmanager
def model_usage_scope(request_id=""):
    tracker = {"request_id": request_id or uuid4().hex, "calls": [], "cursor": 0, "lock": threading.Lock()}
    token = _USAGE_REQUEST.set(tracker)
    previous = _last_usage.set(None)
    try:
        yield
    finally:
        _last_usage.reset(previous)
        _USAGE_REQUEST.reset(token)


def usage_request(generator):
    @wraps(generator)
    def tracked(self, session_id, request):
        with model_usage_scope(request["request_id"]):
            yield from generator(self, session_id, request)
    return tracked


@contextmanager
def _model_call(model, streaming):
    started = time.monotonic()
    tracker = _USAGE_REQUEST.get()
    call = {"call_id": uuid4().hex, "model": model, "stream": streaming, "tokens": {}, "status": "done"}
    if tracker is not None:
        call["request_id"] = tracker["request_id"]
    token = _MODEL_CALL.set(call)
    _last_usage.set(None)
    try:
        yield
    except BaseException as error:
        call.update(status="error", error_type=type(error).__name__)
        if isinstance(error, OpenAIError):
            # Provider error bodies can echo credentials. Log diagnostics without the body.
            status_code = getattr(error, "status_code", None)
            if isinstance(status_code, int):
                call["status_code"] = status_code
            raise RuntimeError("模型服务请求失败，请检查模型配置或网络连接。") from None
        raise
    finally:
        call["ms"] = max(0, int((time.monotonic() - started) * 1000))
        if tracker is not None:
            with tracker["lock"]:
                tracker["calls"].append(call)
        LOGGER.info("Model call %s", json.dumps(call, ensure_ascii=False))
        _MODEL_CALL.reset(token)


def drain_model_calls():
    tracker = _USAGE_REQUEST.get()
    if tracker is None:
        return []
    with tracker["lock"]:
        calls = tracker["calls"][tracker["cursor"]:]
        tracker["cursor"] = len(tracker["calls"])
        return list(calls)


def sum_usage(calls):
    totals = {}
    for call in calls:
        for key, value in call["tokens"].items():
            totals[key] = totals.get(key, 0) + value
    return totals


def request_usage():
    tracker = _USAGE_REQUEST.get()
    if tracker is None:
        return {"calls": [], "tokens": {}}
    with tracker["lock"]:
        calls = list(tracker["calls"])
    return {"calls": calls, "tokens": sum_usage(calls)}


def current_request_id():
    tracker = _USAGE_REQUEST.get()
    return tracker["request_id"] if tracker is not None else ""


def _record_usage(usage) -> dict:
    """Store the most recent provider usage on this thread and return it."""
    recorded = {}
    if usage is not None:
        for field in ("prompt_tokens", "completion_tokens", "total_tokens"):
            value = getattr(usage, field, None)
            if value is not None:
                recorded[field] = value
    _last_usage.set(recorded)
    call = _MODEL_CALL.get()
    if call is not None:
        call["tokens"] = dict(recorded)
    return dict(recorded)


def last_usage() -> dict:
    """Token usage of the most recent model call on this thread, if any."""
    return dict(_last_usage.get() or {})


class ResponseStreamCancelled(RuntimeError):
    """The HTTP consumer disconnected before the model response completed."""


@contextmanager
def stream_responses(emit, cancelled, reset=None):
    """Bind public text deltas and cancellation to this request's execution."""
    token = _RESPONSE_STREAM.set((emit, cancelled))
    reset_token = _RESPONSE_RESET.set(reset)
    try:
        yield
    finally:
        _RESPONSE_STREAM.reset(token)
        _RESPONSE_RESET.reset(reset_token)


def reset_response_draft():
    reset = _RESPONSE_RESET.get()
    if reset is not None:
        reset()


def is_streaming_response() -> bool:
    return _RESPONSE_STREAM.get() is not None


def _chat_stream(messages: list[dict], temperature: float = 0.1) -> Iterator[str]:
    """Yield provider text deltas and require a complete normal termination."""
    channel = _RESPONSE_STREAM.get()
    if channel is not None and channel[1].is_set():
        raise ResponseStreamCancelled("回答连接已结束")
    settings = get_settings()
    stream = get_client().chat.completions.create(
        model=settings.deepseek_model, messages=messages, temperature=temperature, stream=True,
        stream_options={"include_usage": True},
    )
    has_text, finished = False, False
    try:
        for chunk in stream:
            if channel is not None and channel[1].is_set():
                raise ResponseStreamCancelled("回答连接已结束")
            if getattr(chunk, "usage", None) is not None:
                _record_usage(chunk.usage)
            if not chunk.choices:
                continue
            choice = chunk.choices[0]
            content = choice.delta.content
            if content is not None:
                if not isinstance(content, str):
                    raise RuntimeError("模型流式正文格式无效")
                has_text = has_text or bool(content.strip())
                if content:
                    yield content
            if choice.finish_reason is not None:
                if choice.finish_reason != 'stop':
                    raise RuntimeError("模型回答未完整生成，请重试")
                finished = True
        if not finished:
            raise RuntimeError("模型连接在回答完成前结束，请重试")
        if not has_text:
            raise RuntimeError("模型没有返回可用正文，请检查模型配置或稍后重试")
    finally:
        stream.close()


def chat_stream(messages: list[dict], temperature: float = 0.1) -> Iterator[str]:
    with _model_call(get_settings().deepseek_model, True):
        yield from _chat_stream(messages, temperature)

@lru_cache()#做缓存，确保只创建一次客户端实例
def get_client() -> OpenAI:
    s = get_settings()
    if not s.deepseek_api_key:
        raise RuntimeError(
            "未配置 DEEPSEEK_API_KEY：把 .env.example 复制为 .env 并填上 key")
    return OpenAI(
        api_key=s.deepseek_api_key,
        base_url=s.deepseek_base_url,
        timeout=s.llm_timeout,
        max_retries=s.llm_max_retries,
    )


def chat(messages: list[dict], temperature: float = 0.1, json_mode: bool = False) -> str:
    """调用 DeepSeek，返回回复文本。

    messages 形如 [{"role": "system", "content": "..."}, {"role": "user", "content": "..."}]
    """
    channel = _RESPONSE_STREAM.get()
    if channel is not None and channel[1].is_set():
        raise ResponseStreamCancelled("回答连接已结束")
    if channel is not None and not json_mode:
        parts = []
        for content in chat_stream(messages, temperature):
            parts.append(content)
            channel[0](content)
        return ''.join(parts).strip()
    s = get_settings()
    kwargs = dict(model=s.deepseek_model, messages=messages, temperature=temperature)
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    with _model_call(s.deepseek_model, False):
        resp = get_client().chat.completions.create(**kwargs)
        _record_usage(getattr(resp, "usage", None))
        if channel is not None and channel[1].is_set():
            raise ResponseStreamCancelled("回答连接已结束")
        content = resp.choices[0].message.content
        if not isinstance(content, str) or not content.strip():
            raise RuntimeError("模型没有返回可用正文，请检查模型配置或稍后重试")
    return content.strip()
