"""Plan revision retention from current versions and local source references."""


def plan_revision_retention(
    revisions: list[dict], source_refs: list[dict],
) -> dict:
    """家庭作业。按当前修订和引用关系生成保留与候选清理计划。
    两个参数都须为列表，元素都须为字典。每个修订须含非空白字符串
    doc_id、revision_id，以及严格 bool 类型的 is_current。文档和修订
    二元组唯一，同一文档最多有一个当前修订，允许文档已无当前修订。
    标识校验后继续使用原始值，保持所有输入数据。
    source_type 为 external 的引用跳过。其他引用的 doc_id 须为非空白
    字符串。revision_id 缺省、None 或空白字符串表示修订未知，规划中
    统一记为 None；非空字符串使用原值，其他类型抛出 ValueError。
    重复引用按规范化后的 (doc_id, revision_id) 合并，保留首次顺序。
    完成全部输入校验后进行规划，空修订列表也须处理给定来源。
    当前修订始终保留，原因 current；精确被引用的修订保留，原因
    referenced。同文档存在修订未知引用时，按用户决定保留该文档的
    全部修订，原因 unversioned_reference。每个保留项含 doc_id、
    revision_id、reasons，全部适用原因按上述顺序各出现一次。
    其他修订进入 cleanup_candidates，每项含 doc_id、revision_id、
    reason='unreferenced_history'。保留和候选列表分别保持修订输入顺序，
    每个修订恰好进入其中一个列表。
    未知修订引用进入 unresolved_refs，reason='missing_revision'；明确
    修订的 (doc_id, revision_id) 在目录中不存在时也进入该列表，reason
    为 revision_not_found。每项含 doc_id、revision_id、reason，按首次
    引用顺序排列，重复引用输出一次。
    返回 {keep_revisions, cleanup_candidates, unresolved_refs, summary}。
    summary 含 total_revision_count、keep_count、cleanup_candidate_count、
    unresolved_ref_count，分别为输入修订数及三个结果列表长度。
    两列表均空时返回空结果与零计数。非法输入抛出 ValueError。
    本题采用确定性匹配，调用方负责读取目录、收集引用、展示计划及执行。
    """
    if not isinstance(revisions, list):
        raise ValueError("revisions 必须是列表")
    if not isinstance(source_refs, list):
        raise ValueError("source_refs 必须是列表")
    seen = set()
    current_doc_ids = set()
    for revision in revisions:
        if not isinstance(revision, dict):
            raise ValueError("每个修订必须是字典")
        doc_id = revision.get("doc_id")
        revision_id = revision.get("revision_id")
        if not isinstance(doc_id, str) or not doc_id.strip():
            raise ValueError("修订 doc_id 必须是非空白字符串")
        if not isinstance(revision_id, str) or not revision_id.strip():
            raise ValueError("修订 revision_id 必须是非空白字符串")
        is_current = revision.get("is_current")
        if type(is_current) is not bool:
            raise ValueError("is_current 必须是 bool")
        revision_key = (doc_id, revision_id)
        if revision_key in seen:
            raise ValueError(f"文档修订重复：{revision_key}")
        seen.add(revision_key)
        if is_current:
            if doc_id in current_doc_ids:
                raise ValueError(f"同一文档最多只能有一个当前修订：{doc_id}")
            current_doc_ids.add(doc_id)
    normalized_refs = []
    seen_refs = set()
    unversioned_doc_ids = set()
    for source_ref in source_refs:
        if not isinstance(source_ref, dict):
            raise ValueError("每条引用必须是字典")
        if source_ref.get("source_type") == "external":
            continue
        doc_id = source_ref.get("doc_id")
        if not isinstance(doc_id, str) or not doc_id.strip():
            raise ValueError("引用 doc_id 必须是非空白字符串")
        revision_id = source_ref.get("revision_id")
        if revision_id is not None:
            if not isinstance(revision_id, str):
                raise ValueError("引用 revision_id 必须是字符串或 None")
            if not revision_id.strip():
                revision_id = None
        reference_key = (doc_id, revision_id)
        if reference_key in seen_refs:
            continue
        seen_refs.add(reference_key)
        normalized_refs.append(reference_key)
        if revision_id is None:
            unversioned_doc_ids.add(doc_id)
    keep_revisions = []
    cleanup_candidates = []
    for revision in revisions:
        doc_id = revision["doc_id"]
        revision_id = revision["revision_id"]
        reasons = []
        if revision["is_current"]:
            reasons.append("current")
        if (doc_id, revision_id) in seen_refs:
            reasons.append("referenced")
        if doc_id in unversioned_doc_ids:
            reasons.append("unversioned_reference")
        if reasons:
            keep_revisions.append({
                "doc_id": doc_id,
                "revision_id": revision_id,
                "reasons": reasons,
            })
        else:
            cleanup_candidates.append({
                "doc_id": doc_id,
                "revision_id": revision_id,
                "reason": "unreferenced_history",
            })

    unresolved_refs = []
    for doc_id, revision_id in normalized_refs:
        if revision_id is None:
            reason = "missing_revision"
        elif (doc_id, revision_id) not in seen:
            reason = "revision_not_found"
        else:
            continue
        unresolved_refs.append({
            "doc_id": doc_id,
            "revision_id": revision_id,
            "reason": reason,
        })

    return {
        "keep_revisions": keep_revisions,
        "cleanup_candidates": cleanup_candidates,
        "unresolved_refs": unresolved_refs,
        "summary": {
            "total_revision_count": len(revisions),
            "keep_count": len(keep_revisions),
            "cleanup_candidate_count": len(cleanup_candidates),
            "unresolved_ref_count": len(unresolved_refs),
        },
    }
