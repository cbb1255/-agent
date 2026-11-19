"""本地模型加载（离线优先）。

为什么需要这个文件：
sentence-transformers 每次加载模型都会去 huggingface.co 检查有没有新版本，
国内网络会超时重试，让 ingest / 检索卡好几分钟。
这里改成「先离线读本地缓存，缓存没有才联网下载」，并且默认走国内镜像。
"""
import os


def _prepare_env() -> None:
    # 关掉 Windows 下的 symlink 警告
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
    # 国内直连 huggingface 容易超时，默认走镜像；想直连就自己设置 HF_ENDPOINT
    os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")


def load_sentence_transformer(model_name: str):
    """嵌入模型：先离线加载，失败（首次没缓存）才联网下载。"""
    from sentence_transformers import SentenceTransformer
    _prepare_env()
    try:
        return SentenceTransformer(model_name, local_files_only=True)
    except Exception:
        return SentenceTransformer(model_name)


def load_cross_encoder(model_name: str):
    """重排模型：同上。"""
    from sentence_transformers import CrossEncoder
    _prepare_env()
    try:
        return CrossEncoder(model_name, local_files_only=True)
    except Exception:
        return CrossEncoder(model_name)
