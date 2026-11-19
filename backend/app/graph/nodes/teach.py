"""Grounded teaching exercise and source-number preserving context adapter."""
from datetime import datetime, timedelta, timezone

from app.llm import chat
from app.prompts import NO_EVIDENCE_TEXT, TEACH_SYSTEM, build_context


def build_teaching_context(chunks: list[dict], supporting_indices: list[int]) -> str:
    """Validate selected sources and preserve their original citation numbers."""
    if not isinstance(chunks, list):
        raise ValueError("chunks 必须是片段列表")
    if not isinstance(supporting_indices, list):
        raise ValueError("supporting_indices 必须是列表")

    seen_indices = set()
    blocks = []
    for index in supporting_indices:
        if type(index) is not int or not 1 <= index <= len(chunks):
            raise ValueError("支撑编号必须是 1 到 len(chunks) 之间的整数")
        if index in seen_indices:
            raise ValueError("支撑编号不能重复")
        seen_indices.add(index)
        chunk = chunks[index - 1]
        if not isinstance(chunk, dict):
            raise ValueError("引用的资料片段必须是字典")
        text = chunk.get("text")
        if not isinstance(text, str) or not text.strip():
            raise ValueError("引用的资料片段必须包含非空正文")
        metadata = chunk.get("metadata")
        if metadata is not None and not isinstance(metadata, dict):
            raise ValueError("片段 metadata 必须是字典")

        block = build_context([chunk])
        blocks.append(block.replace("[1]", f"[{index}]", 1))
    return "\n\n---\n\n".join(blocks)


def generate_teaching(
    question: str,
    chunks: list[dict],
    evidence: dict,
    submode: str = "explain",
) -> str:
    """基于同一问题和片段的证据判断，生成讲解或分步教学正文。"""
    if not isinstance(question, str) or not question.strip():
        raise ValueError("问题必须是非空字符串")
    question = question.strip()
    if submode not in ("explain", "solve_step"):
        raise ValueError("教学方式须为 explain 或 solve_step")
    if not isinstance(evidence, dict):
        raise ValueError("evidence 必须是证据判断字典")

    status = evidence.get("status")
    if status not in ("sufficient", "partial", "insufficient"):
        raise ValueError("证据 status 无效")
    reason = evidence.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("证据 reason 必须是非空字符串")
    missing_points = evidence.get("missing_points")
    if not isinstance(missing_points, list):
        raise ValueError("missing_points 必须是列表")
    for point in missing_points:
        if not isinstance(point, str) or not point.strip():
            raise ValueError("缺失要点必须是非空字符串")

    supporting_indices = evidence.get("supporting_indices")
    context = build_teaching_context(chunks, supporting_indices)
    has_support = bool(supporting_indices)
    has_missing = bool(missing_points)
    if status == "sufficient" and (not has_support or has_missing):
        raise ValueError("sufficient 要求有支撑，且没有缺失要点")
    if status == "partial" and (not has_support or not has_missing):
        raise ValueError("partial 要求有支撑，也有缺失要点")
    if status == "insufficient":
        if has_support or not has_missing:
            raise ValueError("insufficient 要求没有支撑，但有缺失要点")
        return NO_EVIDENCE_TEXT

    if missing_points:
        missing_text = "\n".join(point.strip() for point in missing_points)
    else:
        missing_text = "无"
    current_time = datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds")
    runtime_context = f"当前北京时间为 {current_time}。"
    if any((chunks[index - 1].get("metadata") or {}).get("source_type") == "external"
           for index in supporting_indices):
        runtime_context += (
            "\n本轮已通过 MCP 完成网页搜索，参考资料包含实际返回的网页摘录。"
            "依据实际执行状态描述能力，避免声称本轮无法联网或让用户自行搜索。"
            "\n检索时间表示取回网页的时间，网页发布时间和正文所述日期须分别核对。"
            "当前价格、版本等问题应说明资料的时间范围，资料不足时明确指出缺失信息。"
        )
    system_prompt = TEACH_SYSTEM.format(
        runtime_context=runtime_context,
        mode_instruction={
            "explain": "explain 讲解模式，先给简短具体例子，再解释概念与关键机制。",
            "solve_step": "solve_step 分步模式，将过程拆成有序步骤，说明每步目的和关键条件。",
        }[submode],
        missing_points=missing_text,
        context=context,
    )
    response = chat([
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": question},
    ])
    if not isinstance(response, str) or not response.strip():
        raise RuntimeError("模型没有返回可用的教学正文")
    return response.strip()


def search_external_evidence(
    question: str,
    missing_points: list[str],
    search_web,
) -> list[dict]:
    if not isinstance(question, str) or not question.strip():
        raise ValueError("问题必须是非空字符串")
    question = question.strip()
    if not isinstance(missing_points, list):
        raise ValueError("missing_points 必须是列表")
    for point in missing_points:
        if not isinstance(point, str) or not point.strip():
            raise ValueError("缺失要点必须是非空字符串")
    if not missing_points:
            return []
    been_missing_points=[]
    for missing_point in missing_points:
        if  missing_point.strip() not in been_missing_points:
                        been_missing_points.append(missing_point.strip())
    if not callable(search_web):
        raise ValueError("search_web 必须是可调用的搜索函数")
    been_url=[]
    finally_result=[]
    for i, missing_point in enumerate(been_missing_points, 1):
        query = f"{question} {missing_point}"
        if i<3 and query.strip() :
            search_results=search_web(query)
            if len(search_results)==0:
                continue
            for result in search_results:
                text = result.get("text", "").strip()
                url = result.get("url", "").strip()
                title = result.get("title", "").strip() or "未命名外部资料"
                retrieved_at=result.get("retrieved_at","")
                if not text.strip() or not url.strip():
                    continue
                if  url.strip() in been_url:
                    continue
                been_url.append(url.strip())
                finally_result.append({"text": text,
            "metadata": {
                "source_type": "external",
                "title": title,
                "url": url,
                "retrieved_at": retrieved_at,
            },})
    return finally_result


