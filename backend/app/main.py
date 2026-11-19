"""FastAPI endpoints for local Markdown knowledge and grounded questions."""
import json
import asyncio
from contextlib import asynccontextmanager
import logging
import sys
import time
import codecs
from datetime import datetime
from pathlib import Path
from tempfile import SpooledTemporaryFile
from threading import Event
from typing import Literal
from uuid import uuid4

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.security import HTTPBearer
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel, Field, field_validator
from starlette.concurrency import run_in_threadpool

from app.config import get_settings
from app.access import AccessControlMiddleware, get_access_token
from app.graph.builder import build_graph
from app.graph.citations import extract_source_numbers
from app.knowledge import documents
from app.learning.service import LearningError, LearningService
from app.learning.review import utc_datetime
from app.llm import (ResponseStreamCancelled, stream_responses, model_usage_scope,
                     drain_model_calls, sum_usage, request_usage)

LOGGER = logging.getLogger(__name__)
FUSION_PAGE = Path(__file__).resolve().parents[2] / "frontend" / "index.html"
FUSION_META = '<meta name="knowledge-backend" content="same-origin">'
CHAT_ERROR = "问答暂时失败，请检查模型配置和服务连接后重试。详细原因已记录在服务日志中。"
UNTRACEABLE_CITATIONS = "回答的来源编号无法追溯到实际证据，已拒绝发布，请重试。"


class ChatRequest(BaseModel):
    message: str = Field(strict=True)
    session_id: str = Field(default="demo", strict=True, max_length=128)

    @field_validator("message")
    @classmethod
    def validate_message(cls, message: str) -> str:
        message = message.strip()
        if not message:
            raise ValueError("请输入要查询的问题。")
        if len(message) > get_settings().max_query_chars:
            raise ValueError("问题超过长度上限，请缩短后重试。")
        return message


class DocumentRequest(BaseModel):
    filename: str = Field(strict=True)
    content: str = Field(strict=True)
    overwrite: bool = Field(default=False, strict=True)


@asynccontextmanager
async def lifespan(app):
    app.state.document_recovery = documents.recover_document_operations()
    service = LearningService(Path(get_settings().index_dir).parent / "learning.sqlite3")
    app.state.learning = service
    if get_settings().prewarm_models:
        try:
            from app.rag.embedder import warmup
            app.state.model_warmup = warmup()
            from app.rag.retriever import warmup_indexes
            warmup_indexes()
            LOGGER.info("Model warmup completed: %s", app.state.model_warmup)
        except Exception:
            LOGGER.exception("Model warmup failed; the first request will load models lazily")
    try:
        yield
    finally:
        service.close()


app = FastAPI(title="personal-knowledge-base", lifespan=lifespan, dependencies=[Depends(HTTPBearer(auto_error=False))])
app.add_middleware(AccessControlMiddleware)
GRAPH = build_graph()


@app.get("/access/token", openapi_extra={"security": []})
def local_access_token():
    return {"access_token": get_access_token()}


class LearningAction(BaseModel):
    request_id: str = Field(strict=True, min_length=1, max_length=128)
    action: Literal["start", "answer", "exercise", "remediate", "variant", "finish"]
    message: str = Field(default="", strict=True)
    selected_mode: Literal["teach", "assess", "direct"] | None = None
    submode: Literal["explain", "solve_step"] = "explain"
    concept_label: str = Field(default="", strict=True, max_length=120)
    pending_id: str | None = Field(default=None, max_length=256)
    answer: str | None = Field(default=None, max_length=16)

    @field_validator("request_id", "message", "concept_label")
    @classmethod
    def validate_text(cls, value, info):
        value = value.strip()
        if info.field_name == "request_id" and not value:
            raise ValueError("请求编号不能为空")
        if info.field_name == "message" and len(value) > get_settings().max_query_chars:
            raise ValueError("问题超过长度上限")
        return value


def _learning_operation(operation, *args, **kwargs):
    try:
        return operation(*args, **kwargs)
    except LearningError as error:
        raise HTTPException(error.status_code, str(error)) from error
    except Exception as error:
        LOGGER.exception("Learning session operation failed")
        raise HTTPException(500, CHAT_ERROR) from error


@app.post("/sessions")
def create_learning_session():
    return _learning_operation(app.state.learning.create)


class ReviewAnswer(BaseModel):
    request_id: str = Field(strict=True, min_length=1, max_length=128)
    answer: str = Field(strict=True, min_length=1, max_length=16)
    expected_review_at: datetime

    @field_validator("request_id")
    @classmethod
    def valid_request_id(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("请求编号不能为空")
        return value

    @field_validator("expected_review_at")
    @classmethod
    def aware_review_time(cls, value):
        return utc_datetime(value)


@app.get("/learning/reviews")
def list_review_items(due_only: bool = True):
    return _learning_operation(app.state.learning.reviews.list_items, due_only=due_only)


@app.get("/learning/progress")
def learning_progress():
    return _learning_operation(app.state.learning.progress)


@app.get("/learning/reviews/{exercise_id}/question")
def get_review_question(exercise_id: str):
    return _learning_operation(app.state.learning.review_question, exercise_id)


@app.post("/learning/reviews/{exercise_id}/answer")
def submit_review_answer(exercise_id: str, req: ReviewAnswer):
    return _learning_operation(
        app.state.learning.answer_review, exercise_id, req.request_id, req.answer, req.expected_review_at,
    )


@app.get("/sessions/{session_id}")
def read_learning_session(session_id: str):
    return _learning_operation(app.state.learning.view, session_id)


@app.post("/sessions/{session_id}/cancel")
def cancel_learning_session(session_id: str):
    return _learning_operation(app.state.learning.cancel, session_id)


@app.post("/sessions/{session_id}/actions")
def learning_action(session_id: str, req: LearningAction):
    def execute():
        return list(app.state.learning.events(session_id, req.model_dump()))[-1]["session"]
    return _learning_operation(execute)


@app.post("/sessions/{session_id}/actions/stream")
async def learning_action_stream(session_id: str, req: LearningAction):
    service = app.state.learning
    payload = req.model_dump()

    def produce(publish):
        for event in service.events(session_id, payload):
            publish(event)

    return _stream_response(_stream_events(produce))


def _document_operation(operation, *args):
    try:
        return operation(*args)
    except documents.DocumentError as error:
        raise HTTPException(status_code=error.status_code, detail=str(error)) from error
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail="索引或原文尚不可用，请重建索引后重试。") from error
    except Exception as error:
        LOGGER.exception("Knowledge document operation failed")
        raise HTTPException(
            status_code=500,
            detail="文档操作失败，请检查 Markdown 格式、索引配置和服务日志后重试。",
        ) from error


@app.get("/health")
def health():
    app.state.document_recovery = documents.recover_document_operations()
    recovery = app.state.document_recovery
    index_error = ""
    try:
        index = _document_operation(documents.current_summary)
    except HTTPException:
        if not recovery.get("conflicts"):
            raise
        index = None
        index_error = "当前索引摘要暂不可用，请先核对文档恢复冲突及服务日志。"
    result = {
        "ok": True,
        "service": "personal-knowledge-base",
        "index": index,
    }
    if recovery.get("conflicts"):
        result["document_recovery"] = recovery
    if index_error:
        result["index_error"] = index_error
    return result


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
@app.get("/demo", response_class=HTMLResponse, include_in_schema=False)
@app.get("/fusion", response_class=HTMLResponse, include_in_schema=False)
def fusion_page():
    try:
        page = FUSION_PAGE.read_text(encoding="utf-8")
    except FileNotFoundError as error:
        raise HTTPException(status_code=503, detail="找不到融合前端，请先生成 frontend/index.html。") from error
    return HTMLResponse(page.replace("<head>", f"<head>\n  {FUSION_META}", 1), headers={"Cache-Control": "no-store"})


@app.get("/documents")
def document_list():
    return _document_operation(documents.list_documents)


@app.post("/documents")
def document_import(req: DocumentRequest):
    return _document_operation(documents.import_document, req.filename, req.content, req.overwrite)


@app.post("/documents/upload")
async def document_upload(request: Request, filename: str = Query(..., min_length=1, max_length=240), overwrite: bool = False):
    """Receive bounded UTF-8 file bytes, then reuse the existing import transaction."""
    filename = _document_operation(documents._validate_filename, filename)
    if request.headers.get('content-type', '').split(';')[0].strip().lower() != 'application/octet-stream':
        raise HTTPException(415, '上传文件请使用application/octet-stream')
    limit = get_settings().max_document_bytes
    length = request.headers.get('content-length')
    if length is not None:
        if not length.isascii() or not length.isdecimal():
            raise HTTPException(400, '上传长度无效')
        try:
            declared_size = int(length)
        except ValueError as error:
            raise HTTPException(400, '上传长度无效') from error
        if declared_size > limit:
            raise HTTPException(413, f'文档超过{limit}字节上限')
    try:
        decoder = codecs.getincrementaldecoder('utf-8-sig')(errors='strict')
        with SpooledTemporaryFile(max_size=1024 * 1024, mode='w+b') as temporary:
            size = 0
            async for chunk in request.stream():
                size += len(chunk)
                if size > limit:
                    raise HTTPException(413, f'文档超过{limit}字节上限')
                decoder.decode(chunk, final=False)
                temporary.write(chunk)
            decoder.decode(b'', final=True)
            temporary.seek(0)
            content = temporary.read().decode('utf-8-sig')
    except UnicodeDecodeError as error:
        raise HTTPException(400, '文档须为有效的UTF-8编码') from error
    except OSError as error:
        LOGGER.exception('Document upload failed')
        raise HTTPException(500, '上传文件暂时无法保存，请检查存储后重试') from error
    return await run_in_threadpool(_document_operation, documents.import_document, filename, content, overwrite)


@app.post("/documents/reindex")
def document_reindex():
    return _document_operation(documents.reindex_documents)


@app.get("/documents/{doc_id}/source")
def document_source(doc_id: str, snapshot: str = Query(...)):
    return _document_operation(documents.read_source, doc_id, snapshot)


@app.delete("/documents/{doc_id}")
def document_delete(doc_id: str, expected_revision_id: str = Query(...)):
    return _document_operation(documents.delete_document, doc_id, expected_revision_id)


def _sse(payload: dict) -> str:
    return "data: %s\n\n" % json.dumps(payload, ensure_ascii=False)


def _initial_state(message: str) -> dict:
    if not message.strip():
        raise ValueError
    return {"question": message.strip(),
     "messages": [{"role": "user", "content": message.strip()
    }] ,"retrieved":[],
     "citations":[],
     "answer":""}


def _done(state: dict, trace_id: str = "") -> dict:
    answer = state.get("answer") or ""
    payload = {
        "event": "done",
        "answer": answer,
        "data": answer,
        "citations": state.get("citations") or [],
        "retrieved": state.get("retrieved") or [],
        "answer_basis": state.get("answer_basis", ""),
    }
    if trace_id:
        payload["trace_id"] = trace_id
    if not _answer_citations_traceable(state):
        return {
            "event": "error",
            "message": UNTRACEABLE_CITATIONS,
            "data": UNTRACEABLE_CITATIONS,
            "trace_id": trace_id or "",
        }
    return payload


def _answer_citations_traceable(state: dict) -> bool:
    """Reject done/local or done/web whose prose numbers cannot reach evidence."""
    basis = state.get("answer_basis", "")
    if basis not in ("local", "web"):
        return True
    if not (state.get("answer") or "").strip():
        return True
    indices = {
        item.get("index")
        for item in (state.get("citations") or [])
        if isinstance(item, dict) and item.get("index") is not None
    }
    numbers = extract_source_numbers(state["answer"])
    return bool(numbers) and numbers.issubset(indices)


def _error() -> dict:
    return {"event": "error", "message": CHAT_ERROR, "data": CHAT_ERROR}


def _step(node: str, label: str, status: str, summary: str, started: float, **extra) -> dict:
    return {
        "event": "step",
        "node": node,
        "label": label,
        "status": status,
        "summary": summary,
        "ms": max(0, int((time.monotonic() - started) * 1000)),
        **extra,
    }


def _stream_response(generator) -> StreamingResponse:
    return StreamingResponse(
        generator,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _stream_events(producer, trace_id=""):
    """Forward text deltas during graph execution, then its validated final event."""
    loop = asyncio.get_running_loop()
    trace_id = trace_id or uuid4().hex
    queue = asyncio.Queue()
    cancelled = Event()

    def publish(event):
        if cancelled.is_set():
            return
        try:
            if isinstance(event, dict):
                event = {"trace_id": trace_id, **event}
            loop.call_soon_threadsafe(queue.put_nowait, event)
        except RuntimeError:
            cancelled.set()

    def run():
        try:
            with model_usage_scope(trace_id), stream_responses(
                lambda text: publish({'event': 'delta', 'text': text, 'status': 'draft'}), cancelled,
                reset=lambda: publish({'event': 'answer_reset', 'status': 'draft'}),
            ):
                producer(publish)
        except ResponseStreamCancelled:
            pass
        except LearningError as error:
            publish({'event': 'error', 'message': str(error), 'status': error.status_code})
        except Exception:
            LOGGER.exception('Model response stream failed')
            publish(_error())
        finally:
            publish(None)

    future = loop.run_in_executor(None, run)
    try:
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=15)
            except asyncio.TimeoutError:
                yield ': keepalive\n\n'
                continue
            if event is None:
                break
            yield _sse(event)
        await future
    finally:
        cancelled.set()


@app.post("/chat")
async def chat(req: ChatRequest):
    trace_id = uuid4().hex

    def produce(publish):
        state = GRAPH.invoke(_initial_state(req.message))
        publish({**_done(state, trace_id), "model_usage": request_usage()})

    return _stream_response(_stream_events(produce, trace_id))


@app.post("/chat/stream")
async def chat_stream(req: ChatRequest):
    trace_id = uuid4().hex

    def produce(publish):
        started = time.monotonic()
        state = _initial_state(req.message)
        publish(_step("receive", "接收问题", "done", req.message[:80], started, trace_id=trace_id))
        publish(_step("retrieve", "检索知识库", "running", "正在查询相关资料", started, trace_id=trace_id))
        for chunk in GRAPH.stream(state, stream_mode="updates"):
            for node, update in chunk.items():
                state.update(update or {})
                if node == "retrieve":
                    hits = state.get("retrieved") or []
                    summary = f"找到 {len(hits)} 条相关片段" if hits else "没有找到足够相关的资料"
                    publish(_step(
                        "retrieve", "检索知识库", "done", summary, started,
                        retrieved=hits, citations=state.get("citations") or [], trace_id=trace_id,
                    ))
                    started = time.monotonic()
                    publish(_step("generate", "生成回答", "running", "正在整理资料", started, trace_id=trace_id))
                elif node == "generate":
                    step = _step(
                        "generate", "生成回答", "done",
                        f"已生成 {len(state.get('answer') or '')} 字", started, trace_id=trace_id,
                    )
                    calls = drain_model_calls()
                    if calls:
                        step["model_calls"] = calls
                        step["tokens"] = sum_usage(calls)
                    publish(step)
        publish({**_done(state, trace_id), "model_usage": request_usage()})

    return _stream_response(_stream_events(produce, trace_id))


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="127.0.0.1", port=8000)
