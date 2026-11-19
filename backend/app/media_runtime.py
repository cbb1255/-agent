"""Call optional local media libraries in their configured Python runtime."""
import json
import os
from pathlib import Path
import subprocess
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def run_media(action: str, payload: dict, *, config_path=None, timeout=600) -> dict:
    """Exchange JSON with a bounded local worker without importing its binaries."""
    if action not in ('ocr', 'pdf', 'transcribe', 'export_pdf'):
        raise ValueError("不支持的媒体操作")
    if not isinstance(payload, dict):
        raise ValueError("媒体参数须为字典")
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= 3600:
        raise ValueError("媒体处理超时须在0到3600秒之间")
    config_path = Path(config_path) if config_path is not None else PROJECT_ROOT / 'storage/media-runtime.json'
    try:
        config = json.loads(config_path.read_text(encoding='utf-8'))
        interpreter = Path(config['python'])
        site_packages = (PROJECT_ROOT / config['site_packages']).resolve()
        model_path = (PROJECT_ROOT / config['asr_model']).resolve()
        if not interpreter.is_file() or not site_packages.is_dir():
            raise ValueError("解释器或依赖目录不存在")
        if not site_packages.is_relative_to(PROJECT_ROOT) or not model_path.is_relative_to(PROJECT_ROOT):
            raise ValueError("媒体依赖与模型目录须位于项目中")
    except (OSError, ValueError, TypeError, KeyError) as error:
        raise RuntimeError("本地媒体运行环境不可用，请执行scripts/setup_media_runtime.py") from error
    request = {'action': action, 'payload': payload, 'site_packages': str(site_packages), 'asr_model': str(model_path)}
    environment = os.environ.copy()
    environment.update(PYTHONIOENCODING='utf-8', HF_HUB_OFFLINE='1', HF_HUB_DISABLE_XET='1')
    try:
        completed = subprocess.run(
            [str(interpreter), str(Path(__file__).with_name('media_worker.py'))],
            input=json.dumps(request, ensure_ascii=False), capture_output=True, text=True,
            encoding='utf-8', timeout=timeout, env=environment,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0,
        )
    except subprocess.TimeoutExpired as error:
        raise RuntimeError(f"本地媒体处理超过{timeout}秒") from error
    except OSError as error:
        raise RuntimeError("无法启动本地媒体处理进程") from error
    try:
        response = json.loads(completed.stdout)
    except ValueError as error:
        raise RuntimeError("媒体处理进程未返回有效JSON") from error
    if not isinstance(response, dict) or completed.returncode or response.get('error'):
        message = response.get('error', '进程执行失败') if isinstance(response, dict) else '返回结构无效'
        raise RuntimeError(f"本地媒体处理失败，{message}")
    return response
