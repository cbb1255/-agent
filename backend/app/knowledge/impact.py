"""Find dependent records after a local record revision changes."""


def analyze_revision_impact(
    doc_id: str,
    old_revision_id: str,
    new_revision_id: str | None,
    records: list[dict],
) -> dict:
    """家庭作业。找出需要复核或单独待核实的引用记录。
    doc_id、old_revision_id 须为非空白字符串。new_revision_id 可为 None
    表示删除，否则须为非空白字符串。标识匹配和返回使用原始字符串。
    records 须为列表。每项为字典，含非空白字符串 record_id，record_type
    须为 exercise、teaching 或 artifact。相同类型内 record_id 唯一，
    不同类型允许同名。source_refs 缺省为 []，显式提供须为字典列表。
    source_type 为 external 的引用跳过。本地引用只有 doc_id 精确匹配
    本次文档才参与判断。匹配引用的 revision_id 缺省、None 或空白字符串
    表示修订未知；其他有效字符串按原值比较，其他类型抛出 ValueError。
    缺少 doc_id 或引用其他文档的条目不参与本次影响判断。
    完成全部参数和记录校验后，若新旧修订相同，返回两个空结果列表。
    否则，任一引用明确匹配旧修订的记录进入 affected_records。仅有
    修订未知引用、且没有明确旧修订引用的记录进入 uncertain_records。
    仅引用新修订或其他修订的记录跳过。无引用的记录跳过。
    同一记录最多输出一次，affected 优先于 uncertain，重复引用不重复
    输出记录，各结果列表保持输入记录顺序。每项含 record_id、
    record_type、reason，reason 为符合对应判断的非空白说明。
    返回 {doc_id, old_revision_id, new_revision_id, affected_records,
    uncertain_records, summary}，summary 含 affected_count、uncertain_count。
    空 records 返回空列表和零计数。非法输入抛出 ValueError。保持所有
    输入数据。本题使用确定性匹配，调用方负责读取记录和保存复核标记。
    """
    seen=[]
    affected_records = []   # 明确引用旧版本的记录
    uncertain_records = []  # 引用了该文档，但版本未知的记录
    if not isinstance(doc_id,str) or  not doc_id.strip() :
        raise ValueError
    if  new_revision_id!=None :
        if not isinstance(new_revision_id,str)or not new_revision_id.strip():
            raise ValueError
    if not isinstance(old_revision_id, str) or not old_revision_id.strip():
        raise ValueError("old_revision_id 必须是非空白字符串")
    if not isinstance(records,list):
        raise ValueError
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("每份record必须是字典")
        record_id = record.get("record_id")
        record_type = record.get("record_type")
        if not isinstance(record_id, str) or not record_id.strip():
            raise ValueError("record_id 必须是非空白字符串")
        if record_type not in ("exercise", "teaching", "artifact"):
            raise ValueError("record_type 必须是有效的记录类型")
        record_key = (record_type, record_id)
        source_refs = record.get("source_refs",[])
        if not isinstance(source_refs, list):
            raise ValueError("source_refs 必须是列表")
        if record_key in seen:
            raise ValueError(f"记录重复：{record_key}")
        seen.append(record_key)
        has_old_revision = False
        has_unknown_revision = False
        for ref in source_refs:
            if not isinstance(ref, dict):
                raise ValueError("每条引用必须是字典")
            # 跳过外部网页引用
            if ref.get("source_type") == "external":
                continue
            # 跳过其他文档的引用
            if ref.get("doc_id") != doc_id:
                continue
            revision_id = ref.get("revision_id")
            if revision_id is None:
                has_unknown_revision = True
            elif not isinstance(revision_id, str):
                raise ValueError("引用的 revision_id 必须是字符串或 None")
            elif not revision_id.strip():
                has_unknown_revision = True
            elif revision_id == old_revision_id:
                has_old_revision = True
        # 当前记录的引用已经全部检查完
        if old_revision_id == new_revision_id:
            continue
        if has_old_revision:
            affected_records.append({
                "record_id": record_id,
                "record_type": record_type,
                "reason": "记录引用了已更新或删除的文档旧版本，需要复核。",
            })
        elif has_unknown_revision:
            uncertain_records.append({
                "record_id": record_id,
                "record_type": record_type,
                "reason": "记录引用了该文档，但引用版本未知，需要核实。",
            })
    return {
    "doc_id": doc_id,
    "old_revision_id": old_revision_id,
    "new_revision_id": new_revision_id,
    "affected_records": affected_records,
    "uncertain_records": uncertain_records,
    "summary": {
        "affected_count": len(affected_records),
        "uncertain_count": len(uncertain_records),
    },
}