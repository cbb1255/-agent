"""Bounded web search using the selected service in an existing MCP config."""
import asyncio
from contextlib import AsyncExitStack
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import logging
from uuid import uuid4
import time
from urllib.parse import urlsplit, urlunsplit

import httpx
from dotenv import dotenv_values
from jsonschema import validators
from mcp import ClientSession
from mcp.client.sse import sse_client
from mcp.client.streamable_http import streamable_http_client
from referencing import Registry

from app.config import get_settings


LOGGER = logging.getLogger(__name__)


class WebSearchError(RuntimeError):
    """A user-readable search failure, without transport or config secrets."""
    def __init__(self, message, code="provider_error", request_id=""):
        super().__init__(message)
        self.code = code
        self.request_id = request_id


def _error_code(error):
    if isinstance(error, BaseExceptionGroup):
        codes = [_error_code(item) for item in error.exceptions]
        return next((code for code in codes if code != "transport_error"), "transport_error")
    if isinstance(error, (TimeoutError, httpx.TimeoutException)):
        return "timeout"
    if isinstance(error, httpx.HTTPStatusError):
        return {401: "authentication", 403: "authentication", 402: "rate_limit", 429: "rate_limit"}.get(error.response.status_code, "http_error")
    if isinstance(error, (ValueError, KeyError, FileNotFoundError)):
        return "configuration"
    return "transport_error"


def _expand(value, environment):
    if isinstance(value, str):
        return re.sub(r"\$\{(?:env:)?([A-Za-z_][A-Za-z0-9_]*)\}",
                      lambda match: environment[match.group(1)], value)
    if isinstance(value, dict):
        return {key: _expand(item, environment) for key, item in value.items()}
    if isinstance(value, list):
        return [_expand(item, environment) for item in value]
    return value


def _load_server_config(config_path, server_name):
    path = Path(config_path)
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    server = data["mcpServers"][server_name]
    if not isinstance(server, dict) or server.get("disabled") is True or server.get("isActive") is False:
        raise ValueError("Search server is unavailable")
    env_file = Path(data.get("envFile", ".env"))
    if not env_file.is_absolute():
        env_file = path.parent / env_file
    if "envFile" in data and not env_file.is_file():
        raise ValueError("Configured environment file is unavailable")
    environment = dict(dotenv_values(env_file)) if env_file.is_file() else {}
    environment.update(os.environ)
    server = _expand(server, environment)
    kind = server.get("type", server.get("transport", "http"))
    if kind not in ("http", "streamable-http", "streamable_http", "sse"):
        raise ValueError("Search requires an HTTP MCP server")
    address = urlsplit(server["url"])
    if address.scheme not in ("http", "https") or not address.hostname:
        raise ValueError("Invalid search server address")
    headers = server.get("headers", {})
    if not isinstance(headers, dict) or not all(isinstance(value, str) for value in headers.values()):
        raise ValueError("Invalid search server headers")
    return {"type": kind, "url": server["url"], "headers": headers}


def _tool_arguments(tool, query, limit):
    schema = tool.inputSchema
    properties = schema.get("properties", {})
    if "query" not in properties:
        raise ValueError("Search tool has no query field")
    arguments = {"query": query}
    if "objective" in properties:
        maximum = properties["objective"].get("maxLength", 4096)
        arguments["objective"] = query[:maximum]
    if "numResults" in properties:
        arguments["numResults"] = limit
    if "contextMaxCharacters" in properties:
        arguments["contextMaxCharacters"] = 10000
    validator = validators.validator_for(schema)
    validator.check_schema(schema)
    validator(schema, registry=Registry()).validate(arguments)
    return arguments


def _parse_exa_text(value):
    """Yield common Exa Title/URL/Text blocks and JSON-formatted tool text."""
    stripped = value.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*|\s*```$", "", stripped, flags=re.I)
    try:
        payload = json.loads(stripped)
    except ValueError:
        payload = None
    if isinstance(payload, (dict, list)):
        yield from _records(payload)
        return
    record = {}
    body = []
    in_body = False
    for line in stripped.splitlines():
        match = re.match(r"^(Title|URL|Text|Content|Highlights|Published|Published Date|PublishedDate|Author|ID):\s*(.*)$", line, re.I)
        if match:
            field, content = match.group(1).lower(), match.group(2)
            if field == "title":
                if record.get("url"):
                    yield {**record, "text": "\n".join(body)}
                record, body, in_body = {"title": content}, [], False
            elif field == "url":
                record["url"] = content
            elif field in ("text", "content", "highlights"):
                in_body = True
                body.append(content)
        elif in_body:
            body.append(line)
    if record.get("url"):
        yield {**record, "text": "\n".join(body)}


def _records(value):
    if isinstance(value, list):
        for item in value:
            yield from _records(item)
    elif isinstance(value, dict):
        if value.get("url") or value.get("link"):
            yield value
        else:
            for key in ("results", "data", "documents", "content"):
                if key in value:
                    yield from _records(value[key])
            if isinstance(value.get("text"), str):
                yield from _parse_exa_text(value["text"])
    elif isinstance(value, str):
        yield from _parse_exa_text(value)


def _normalize_results(result, limit):
    payload = result.model_dump(mode="json", by_alias=True, exclude_none=True)
    if payload.get("isError"):
        text = json.dumps(payload, ensure_ascii=False).lower()
        code = "provider_error"
        if any(term in text for term in ("429", "rate limit", "quota", "credits", "限流", "额度")):
            code = "rate_limit"
        elif any(term in text for term in ("401", "403", "unauthorized", "forbidden", "鉴权")):
            code = "authentication"
        raise WebSearchError("联网搜索暂时失败，请稍后重试。", code)
    records = []
    seen = set()
    retrieved_at = datetime.now(timezone.utc).isoformat()
    for source in (payload.get("structuredContent"), payload.get("content", [])):
        for item in _records(source):
            address = item.get("url") or item.get("link")
            text = item.get("text") or item.get("content") or item.get("snippet") or item.get("highlights")
            if isinstance(text, list) and all(isinstance(part, str) for part in text):
                text = "\n".join(text)
            if not isinstance(address, str) or not isinstance(text, str) or not text.strip():
                continue
            try:
                parts = urlsplit(address.strip())
                if (parts.scheme not in ("http", "https") or not parts.hostname
                        or parts.username or parts.password or any(char.isspace() for char in address)):
                    continue
                parts.port  # Validate any explicit port before exposing the URL.
                url = urlunsplit((parts.scheme, parts.netloc.lower(), parts.path or "/", parts.query, ""))
            except ValueError:
                continue
            if url in seen:
                continue
            seen.add(url)
            title = item.get("title")
            records.append({
                "title": title.strip() if isinstance(title, str) and title.strip() else parts.hostname,
                "text": text.strip()[:12000], "url": url, "retrieved_at": retrieved_at,
            })
            if len(records) >= limit:
                return records
    return records


async def _search_server(query, server, timeout, limit):
    async with asyncio.timeout(timeout):
        async with AsyncExitStack() as stack:
            if server["type"] == "sse":
                streams = await stack.enter_async_context(sse_client(
                    server["url"], headers=server["headers"], timeout=timeout, sse_read_timeout=timeout,
                ))
            else:
                http = await stack.enter_async_context(httpx.AsyncClient(headers=server["headers"], timeout=timeout))
                streams = await stack.enter_async_context(streamable_http_client(server["url"], http_client=http))
            session = await stack.enter_async_context(ClientSession(
                streams[0], streams[1], read_timeout_seconds=timedelta(seconds=timeout),
            ))
            await session.initialize()
            tools = []
            cursor = None
            seen_cursors = set()
            while True:
                page = await session.list_tools(cursor=cursor)
                tools.extend(page.tools)
                cursor = page.nextCursor
                if not cursor:
                    break
                if cursor in seen_cursors or len(seen_cursors) >= 10:
                    raise ValueError("Invalid tool pagination")
                seen_cursors.add(cursor)
            candidates = [tool for tool in tools if tool.name == "web_search_exa"]
            if not candidates:
                candidates = [tool for tool in tools if "search" in tool.name.lower() and (
                    "web" in tool.name.lower() or any(word in (tool.description or "").lower()
                    for word in ("search the web", "web search", "internet", "搜索引擎", "网页搜索", "网络搜索"))
                )]
            if not candidates:
                raise ValueError("No web search tool was discovered")
            tool = candidates[0]
            result = await session.call_tool(tool.name, _tool_arguments(tool, query, limit))
            return _normalize_results(result, limit)


def search_web(query: str) -> list[dict]:
    """Search once, returning cited snippets with UTC retrieval timestamps."""
    if not isinstance(query, str) or not query.strip():
        raise WebSearchError("搜索问题不能为空。")
    from app.graph.nodes.fallback import web_search_forbidden
    if web_search_forbidden(query):
        raise WebSearchError("本次请求已禁止联网搜索。", "forbidden")
    from app.llm import current_request_id
    request_id = current_request_id() or uuid4().hex
    started = time.monotonic()
    try:
        settings = get_settings()
        server = _load_server_config(settings.web_search_mcp_config, settings.web_search_mcp_server)
        return asyncio.run(_search_server(
            query.strip(), server, settings.web_search_timeout, min(settings.web_search_max_results, 5),
        ))
    except WebSearchError as error:
        error.request_id = request_id
        LOGGER.warning("Web search failed request_id=%s code=%s ms=%d", request_id, error.code,
                       int((time.monotonic() - started) * 1000))
        raise
    except Exception as error:
        code = _error_code(error)
        LOGGER.warning("Web search failed request_id=%s code=%s error_type=%s ms=%d",
                       request_id, code, type(error).__name__, int((time.monotonic() - started) * 1000))
        messages = {"timeout": "联网搜索超时，请稍后重试。", "authentication": "搜索服务鉴权失败，请检查配置。",
                    "rate_limit": "搜索服务请求过多，请稍后重试。", "configuration": "搜索服务配置无效，请检查配置。"}
        raise WebSearchError(messages.get(code, "联网搜索暂时不可用，请检查搜索服务配置和连接后重试。"), code, request_id) from error
