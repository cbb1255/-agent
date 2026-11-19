
#6 句子 transformers 库中的交叉编码器，用于计算查询和文档的相关性分数
"""整体作用就是先知道要取几个然后先rrf进行粗排序然后再用crossdecoder精排序最后按照crossdecoder的分数返回"""
# 全局缓存模型
from app.config import get_settings
from app.rag.splitter import search_text
from threading import RLock
_model = None#全局变量为none
_model_lock = RLock()


def _get_model():
    global _model
    with _model_lock:
        if _model is None:
            from app.rag.model_loader import load_cross_encoder
            _model = load_cross_encoder(get_settings().rerank_model)
        return _model
def rerank(query: str, items: list[dict], top_n: int | None = None) -> list[dict]:
    """函数 rerank
    入参 query: 用户查询原文
    入参 items: RRF 融合后的候选列表，每个元素含 "text"（可能已有 "rrf_score"）
    入参 top_n: 保留条数（settings.rerank_top_n，默认 5）
    返回值: list[dict]，每项写回 "final_score" 后按分数降序取前 top_n

    逻辑步骤:
     1. settings.enable_rerank 为 False: 直接返回 items[:top_n]（Noop 降级），
        final_score 用 rrf_score 或 score 兜底s
     2. 否则用全局缓存的 CrossEncoder(settings.rerank_model)
     3. model.predict([(query, item["text"]) ...]) 得到每项相关性分数
     4. 写回 item["final_score"]，按分数降序排序
     5. 返回前 top_n

    注意: 模型加载很慢，用全局变量缓存 CrossEncoder，不要每次请求重新加载。
   作用是先知道取几个然后如果enable_rerank为假就停止返回rrf_score(混合检索 + 重排序)的前n个如果没有就返回0
   懒加载模型不再重复加载 后续请求直接复用，predict是搜索的核心可以让搜索结果更加可靠，最后按分数降序取前 top_n"""
    global _model
    top_n = top_n or get_settings().rerank_top_n
    # 关闭时透传，用 rrf_score 或 score 兜底
    if not get_settings().enable_rerank:
#get_settings() 调用函数获取配置对象（注意必须有括号）
#enable_rerank 是布尔值，not 表示"如果关闭"则进入降级逻辑
        for item in items:
            item["final_score"] = item.get("rrf_score") or item.get("score", 0.0)
        #如果 RRF 分数不存在，则使用原始分数，默认 0
        return items[:top_n]
    
    # 懒加载模型
    _model = _get_model()
#     第一次调用时 _model 是 None，进入分支加载模型
# get_settings().rerank_model 获取配置中的模型名称（如 bge-reranker-base）
# CrossEncoder(...) 实例化模型并赋值给全局变量 _model
# 后续请求直接使用缓存的 _model，不再重复加载 后续请求直接复用，避免重复加载的I/O和内存开销
    # 批量预测并写回分数
    scores = _model.predict([(query, search_text(item)) for item in items])
    for item, score in zip(items, scores):
        item["final_score"] = float(score)
#     列表推导式构造输入对：[(query, 文档1文本), (query, 文档2文本), ...]
# _model.predict() 接收列表，返回 numpy 数组或列表，包含每个文档与查询的相关性分数
# 分数越高表示越相关
    # 按分数降序取前 top_n
    return sorted(items, key=lambda x: x["final_score"], reverse=True)[:top_n]
# zip(items, scores)：将文档列表和分数列表一一配对
# float(score)：将 numpy 类型转换为 Python float（避免 JSON 序列化问题）
# 写入 item["final_score"]，覆盖或新增该字段


def warmup() -> None:
    """Preload the cross-encoder and exercise one trivial pair to cut cold starts."""
    global _model
    if get_settings().enable_rerank:
        _model = _get_model()
        _model.predict([("预热", "预热查询与文档相关性计算")])
