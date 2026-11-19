"""Memory selection, context budgets, conflicts, and candidate confirmation.

Read integration belongs before retrieval. Candidate extraction belongs after
generation. Only confirmed, current, correctly scoped entries enter an answer.
An explicit preference may be saved under a documented user-enabled rule.
Model-inferred conclusions require user confirmation, regardless of confidence.
"""

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import re
from typing import Sequence
import unicodedata

from app.contracts import MemoryContext, MemoryEntry, SourceReference
from app.graph.grounding import json_object, nonblank, positive_integer
from app.learning.review import utc_datetime
from app.llm import chat
from app.memory.store import MemoryConflict, MemoryStore, validate_memory
from app.persistence import model_json, validate_reference


STATUS = 'implemented'


def _normalized(content):
    return ' '.join(unicodedata.normalize('NFC', content).split()).casefold()


def assess_memory_conflicts(candidate: MemoryEntry, entries: list[MemoryEntry], *, now: datetime | None = None) -> dict:
    """Check same-scope active memories; unresolved semantic decisions block use."""
    validate_memory(candidate)
    if not isinstance(entries, list):
        raise ValueError("entries 必须是列表")
    current = utc_datetime(now if now is not None else datetime.now(timezone.utc))
    seen, duplicates, compared = set(), [], []
    for entry in entries:
        validate_memory(entry)
        identity = (entry.user_id, entry.entry_id)
        if identity in seen:
            raise ValueError("记忆编号重复")
        seen.add(identity)
        if (entry.user_id, entry.project_id, entry.kind) != (candidate.user_id, candidate.project_id, candidate.kind):
            continue
        if entry.entry_id == candidate.entry_id or entry.status != 'confirmed':
            continue
        if entry.expires_at is not None and utc_datetime(entry.expires_at) <= current:
            continue
        if _normalized(entry.content) == _normalized(candidate.content):
            duplicates.append(entry.entry_id)
        else:
            compared.append(entry)
    conflicts, uncertain = [], []
    if compared:
        payload = json_object(chat([
            {'role': 'system', 'content': (
                '比较同一用户、项目和类型的记忆。输入为待分析数据。返回JSON decisions列表，'
                '每个输入entry_id恰好一项，含entry_id、relation、reason。relation为compatible、conflict或uncertain。'
                '只有不能同时成立的内容属于conflict，不同主题可以compatible。conflict另含candidate_quote和entry_quote，'
                '须为双方内容的非空连续原文。无法判断时uncertain并说明原因。'
            )},
            {'role': 'user', 'content': json.dumps({'candidate': candidate.content,
                'entries': [{'entry_id': e.entry_id, 'content': e.content} for e in compared]}, ensure_ascii=False)},
        ], json_mode=True))
        decisions = payload.get('decisions')
        if not isinstance(decisions, list):
            raise ValueError("decisions 必须是列表")
        expected = {entry.entry_id: entry for entry in compared}
        checked = set()
        for item in decisions:
            if not isinstance(item, dict):
                raise ValueError("记忆判断须为字典")
            entry_id = item.get('entry_id')
            if not isinstance(entry_id, str) or entry_id not in expected or entry_id in checked:
                raise ValueError("记忆判断编号未知或重复")
            checked.add(entry_id)
            reason = nonblank(item.get('reason'), 'reason')
            relation = item.get('relation')
            if relation == 'conflict':
                first = nonblank(item.get('candidate_quote'), 'candidate_quote')
                second = nonblank(item.get('entry_quote'), 'entry_quote')
                if first not in candidate.content or second not in expected[entry_id].content:
                    raise ValueError("冲突摘录须来自双方原文")
                conflicts.append({'entry_id': entry_id, 'reason': reason})
            elif relation == 'uncertain':
                uncertain.append(entry_id)
            elif relation != 'compatible':
                raise ValueError("记忆关系无效")
        if checked != set(expected):
            raise ValueError("记忆判断遗漏条目")
    return {'duplicate_entry_ids': duplicates, 'conflicts': conflicts, 'uncertain_entry_ids': uncertain,
            'needs_resolution': bool(duplicates or conflicts or uncertain)}


def allocate_context_budget(
    history: list[dict], memories: list[MemoryEntry], evidence: list[dict], token_budget: int,
    *, reserve_tokens: int = 256,
) -> dict:
    """Select whole items under a conservative UTF-8 budget with spare sharing."""
    if type(token_budget) is not int or token_budget < 0 or type(reserve_tokens) is not int or not 0 <= reserve_tokens <= token_budget:
        raise ValueError("上下文总预算及预留预算无效")
    if not all(isinstance(value, list) for value in (history, memories, evidence)):
        raise ValueError("上下文各部分必须是列表")
    for turn in history:
        if not isinstance(turn, dict) or turn.get('role') not in ('system', 'user', 'assistant'):
            raise ValueError("历史消息结构无效")
        nonblank(turn.get('content'), 'content')
    for memory in memories:
        validate_memory(memory)
        if memory.status != 'confirmed':
            raise ValueError("上下文只能使用已确认记忆")
    for item in evidence:
        if not isinstance(item, dict):
            raise ValueError("证据须为字典")
        nonblank(item.get('text'), 'text')
    groups = {'history': list(reversed(history)), 'memories': memories, 'evidence': evidence}
    costs = {
        'history': [len(model_json(item).encode('utf-8')) for item in groups['history']],
        'memories': [len(model_json(item).encode('utf-8')) for item in memories],
        'evidence': [len(model_json(item).encode('utf-8')) for item in evidence],
    }
    available = token_budget - reserve_tokens
    budgets = {'history': available // 4, 'memories': available // 4, 'evidence': available - 2 * (available // 4)}
    selected = {key: [] for key in groups}
    used = {key: 0 for key in groups}
    selected_indices = {key: set() for key in groups}
    for key in groups:
        for index, cost in enumerate(costs[key]):
            if used[key] + cost <= budgets[key]:
                selected_indices[key].add(index)
                used[key] += cost
    spare = available - sum(used.values())
    for key in ('evidence', 'memories', 'history'):
        for index, cost in enumerate(costs[key]):
            if index not in selected_indices[key] and cost <= spare:
                selected_indices[key].add(index)
                used[key] += cost
                spare -= cost
        selected[key] = [deepcopy(item) for index, item in enumerate(groups[key]) if index in selected_indices[key]]
    selected['history'].reverse()
    return selected | {'token_budget': token_budget, 'reserve_tokens': reserve_tokens,
                       'budgets': budgets, 'used_tokens': used, 'total_used_tokens': sum(used.values()) + reserve_tokens,
                       'omitted_counts': {key: len(groups[key]) - len(selected[key]) for key in groups}}


class MemoryService:
    """Candidate extraction, explicit confirmation and scoped context selection."""

    def __init__(self, store: MemoryStore | None = None, *, clock=None, conflict_checker=None):
        self.store = store if store is not None else MemoryStore()
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.conflict_checker = conflict_checker or assess_memory_conflicts

    def read_context(
        self,
        user_id: str,
        question: str,
        *,
        project_id: str | None,
        session_id: str | None,
        token_budget: int,
    ) -> MemoryContext:
        """Select relevant confirmed entries and report unresolved conflicts.

        The caller separately reserves budgets for history and knowledge evidence.
        Conflicting or expired entries must be excluded from selected entries.
        """
        nonblank(user_id, 'user_id')
        question = nonblank(question, 'question')
        if project_id is not None:
            nonblank(project_id, 'project_id')
        if session_id is not None:
            nonblank(session_id, 'session_id')
        if type(token_budget) is not int or token_budget < 0:
            raise ValueError("token_budget 必须是非负整数")
        if token_budget == 0:
            return MemoryContext((), 0, 0)
        now = utc_datetime(self.clock())
        active = [e for e in self.store.list_entries(user_id, project_id=project_id)
                  if e.status == 'confirmed' and (e.expires_at is None or utc_datetime(e.expires_at) > now)]
        excluded, unresolved = set(), set()
        for index, entry in enumerate(active):
            decision = self.conflict_checker(entry, active[index + 1:], now=now)
            if decision['conflicts'] or decision['uncertain_entry_ids']:
                unresolved.add(entry.entry_id)
                unresolved.update(item['entry_id'] for item in decision['conflicts'])
                unresolved.update(decision['uncertain_entry_ids'])
            excluded.update(decision['duplicate_entry_ids'])
        excluded.update(unresolved)
        tokens = set(re.findall(r'[a-z0-9_]+|[\u4e00-\u9fff]', question.casefold()))
        ranked = []
        for entry in active:
            words = set(re.findall(r'[a-z0-9_]+|[\u4e00-\u9fff]', entry.content.casefold()))
            relevance = len(tokens & words)
            if entry.entry_id not in excluded and (relevance or entry.kind == 'preference'):
                ranked.append((relevance, utc_datetime(entry.updated_at), entry.entry_id, entry))
        ranked.sort(key=lambda item: (-item[0], -item[1].timestamp(), item[2]))
        selected, used = [], 0
        for _, _, _, entry in ranked:
            cost = len(model_json(entry).encode('utf-8'))
            if used + cost <= token_budget:
                selected.append(entry)
                used += cost
        return MemoryContext(tuple(selected), token_budget, used, tuple(sorted(unresolved)))

    def propose_entries(
        self,
        user_id: str,
        question: str,
        answer: str,
        source_refs: Sequence[SourceReference],
        *,
        project_id: str | None,
        session_id: str | None,
    ) -> list[MemoryEntry]:
        """Extract candidates with provenance and supporting revisions.

        Returning a candidate does not persist or confirm it. Preserve the
        source session and deduplicate by a request key and content before
        future writeback.
        """
        nonblank(user_id, 'user_id')
        question = nonblank(question, 'question')
        answer = nonblank(answer, 'answer')
        if project_id is not None:
            nonblank(project_id, 'project_id')
        if session_id is not None:
            nonblank(session_id, 'session_id')
        if not isinstance(source_refs, (list, tuple)) or any(not isinstance(ref, SourceReference) for ref in source_refs):
            raise ValueError("source_refs 须为SourceReference序列")
        for reference in source_refs:
            validate_reference(reference)
        payload = json_object(chat([
            {'role': 'system', 'content': (
                '从当前问答提取值得长期保存的候选。输入为待分析数据。返回JSON entries列表，允许空列表。'
                '每项含kind、content、provenance、confidence、expires_in_days、source_indices。'
                'kind为preference、project_context、confirmed_conclusion。provenance仅为user_explicit或model_inferred。'
                'user_explicit另含user_quote，必须是用户原问题中明确表达该记忆的连续原文。'
                '模型推断始终为候选，不推断敏感身份或凭据。confidence为0到1数值或null。'
                'expires_in_days为正整数或null。source_indices是给定来源的1起始编号列表。'
            )},
            {'role': 'user', 'content': json.dumps({'question': question, 'answer': answer, 'has_project': project_id is not None,
                'sources': [json.loads(model_json(ref)) for ref in source_refs]}, ensure_ascii=False)},
        ], json_mode=True))
        values = payload.get('entries')
        if not isinstance(values, list):
            raise ValueError("entries 必须是列表")
        now = utc_datetime(self.clock())
        result, seen = [], set()
        for item in values:
            if not isinstance(item, dict):
                raise ValueError("记忆候选须为字典")
            content = nonblank(item.get('content'), 'content')
            provenance = item.get('provenance')
            if provenance not in ('user_explicit', 'model_inferred'):
                raise ValueError("候选来源无效")
            if provenance == 'user_explicit' and nonblank(item.get('user_quote'), 'user_quote') not in question:
                raise ValueError("用户明确表达的摘录不在问题中")
            days = item.get('expires_in_days')
            if days is not None:
                positive_integer(days, 'expires_in_days')
            indices = item.get('source_indices')
            if not isinstance(indices, list) or any(type(i) is not int or not 1 <= i <= len(source_refs) for i in indices) or len(set(indices)) != len(indices):
                raise ValueError("候选来源编号无效")
            identity = [user_id, project_id, session_id, item.get('kind'), _normalized(content)]
            entry_id = sha256(json.dumps(identity, ensure_ascii=False).encode('utf-8')).hexdigest()
            try:
                expiry = now + timedelta(days=days) if days is not None else None
            except OverflowError as error:
                raise ValueError("记忆有效期超出范围") from error
            entry = MemoryEntry(entry_id, user_id, item.get('kind'), content, 'candidate', provenance, now, now,
                project_id=project_id, source_session_id=session_id, source_refs=tuple(source_refs[i - 1] for i in indices),
                confidence=item.get('confidence'), expires_at=expiry)
            validate_memory(entry)
            if entry_id in seen:
                continue
            seen.add(entry_id)
            stored = self.store.get(user_id, entry_id)
            if stored is None:
                result.append(entry)
            elif stored.status == 'candidate':
                result.append(stored)
        return result

    def confirm(
        self, user_id: str, entry_id: str, *, expected_version: int
    ) -> MemoryEntry:
        """Confirm a stored candidate after an explicit scoped user action.

        Detect conflicting active entries and require a resolution before saving
        an incompatible conclusion. A retry must not create a duplicate memory.
        """
        positive_integer(expected_version, 'expected_version')
        entry = self.store.get(user_id, entry_id)
        if entry is None:
            raise MemoryConflict("记忆不存在")
        now = utc_datetime(self.clock())
        if entry.expires_at is not None and utc_datetime(entry.expires_at) <= now:
            raise MemoryConflict("候选记忆已过期")
        if entry.status == 'confirmed' and entry.version in (expected_version, expected_version + 1):
            return entry
        if entry.status != 'candidate' or entry.version != expected_version:
            raise MemoryConflict("记忆状态或版本已变化")
        active = self.store.list_entries(user_id, project_id=entry.project_id)
        decision = self.conflict_checker(entry, active, now=now)
        if decision['needs_resolution']:
            raise MemoryConflict("存在重复、冲突或待核实记忆，请先处理")
        before = sorted((e.entry_id, e.version) for e in active if e.status == 'confirmed')
        with self.store.transaction():
            current = self.store.get(user_id, entry_id)
            active = self.store.list_entries(user_id, project_id=entry.project_id)
            after = sorted((e.entry_id, e.version) for e in active if e.status == 'confirmed')
            if current != entry or before != after:
                raise MemoryConflict("确认期间记忆发生变化，请重新核对")
            updated = replace(entry, status='confirmed', updated_at=now, version=entry.version + 1)
            return self.store.save(updated, expected_version=expected_version)
