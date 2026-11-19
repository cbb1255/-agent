"""Learning progress aggregation exercise."""


def summarize_learning(attempts: list[dict]) -> dict:
    """按答题编号去重，汇总百分制平均分与满分率。

    字段缺失、类型错误、分数越界或同一编号的成绩冲突时抛出 ValueError。
    每条成绩先换算为百分数，再计算平均值，空记录的百分数返回 0.0。
    """
    if not isinstance(attempts, list):
        raise ValueError("attempts 必须是列表")
    total_attempts = 0
    full_score_count = 0
    percentage_sum = 0.0
    seen = {}
    for attempt in attempts:
        if not isinstance(attempt, dict):
            raise ValueError("每条答题记录必须是字典")

        attempt_id = attempt.get("attempt_id")
        if not isinstance(attempt_id, str) or not attempt_id.strip():
            raise ValueError("attempt_id 必须是非空字符串")
        attempt_id = attempt_id.strip()

        score = attempt.get("score")
        max_score = attempt.get("max_score")
        if type(max_score) is not int or max_score <= 0:
            raise ValueError("max_score 必须是正整数，不能是布尔值")
        if type(score) is not int or not 0 <= score <= max_score:
            raise ValueError("score 必须是 0 到 max_score 之间的整数，不能是布尔值")

        current = (score, max_score)
        if attempt_id in seen:
            if seen[attempt_id] != current:
                raise ValueError("同一答题编号的成绩存在冲突")
            continue

        seen[attempt_id] = current
        total_attempts += 1
        percentage_sum += score / max_score * 100
        if score == max_score:
            full_score_count += 1

    average_score = 0.0
    full_score_rate = 0.0
    if total_attempts > 0:
        average_score = round(percentage_sum / total_attempts, 2)
        full_score_rate = round(full_score_count / total_attempts * 100, 2)

    needs_review_count = total_attempts - full_score_count
    return {
        "total_attempts": total_attempts,
        "full_score_count": full_score_count,
        "needs_review_count": needs_review_count,
        "average_score": average_score,
        "full_score_rate": full_score_rate,
    }


def summarize_concept_progress(attempts: list[dict]) -> list[dict]:
    """按知识点统计学习进度
    按标准化的concept_id对经过验证的尝试统计数据进行分组。
    每条记录都会将一个非空的concept_id添加到summary_learning的输入中。
    在本练习中，每个 attempts_id 都属于一个概念。拒绝
    冲突的概念所有权和分数冲突，使周围环境正常化
    ID 中的空格，并重用summarize_learning 进行统计。
    每个概念返回一个简单摘要，按 Concept_id 排序。空输入
    返回一个空列表。保存来电者的记录。
    """
    if not isinstance(attempts, list):
        raise ValueError("attempts 必须是列表")
    seen = {}
    groups = {}
    for attempt in attempts:
        if not isinstance(attempt, dict):
            raise ValueError("每条答题记录必须是字典")
        attempt_id = attempt.get("attempt_id")
        concept_id = attempt.get("concept_id")
        if not isinstance(attempt_id, str) or not attempt_id.strip():
            raise ValueError("attempt_id 必须是非空字符串")
        if not isinstance(concept_id, str) or not concept_id.strip():
            raise ValueError("concept_id 必须是非空字符串")
        attempt_id = attempt_id.strip()
        concept_id = concept_id.strip()

        score = attempt.get("score")
        max_score = attempt.get("max_score")
        if type(max_score) is not int or max_score <= 0:
            raise ValueError("max_score 必须是正整数，不能是布尔值")
        if type(score) is not int or not 0 <= score <= max_score:
            raise ValueError("score 必须是 0 到 max_score 之间的整数，不能是布尔值")

        current = (
            concept_id,
            score,
            max_score,
        )
        if attempt_id in seen:
            if seen[attempt_id] != current:
                raise ValueError("同一答题编号的知识点或成绩存在冲突")
            # 编号、知识点和成绩都相同，跳过重复记录
            continue
        # 第一次遇到这个编号，保存其知识点和成绩
        seen[attempt_id] = current
        if concept_id not in groups:
            groups[concept_id] = []
        groups[concept_id].append(attempt)

    # 所有记录分组完成后，再分别计算统计值
    result = []
    for concept_id in sorted(groups):
        stats = summarize_learning(groups[concept_id])
        result.append({
            "concept_id": concept_id,
            **stats,
        })
    return result
