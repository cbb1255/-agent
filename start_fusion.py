"""Start the fusion page and knowledge-base API on the same loopback server."""
import argparse
from contextlib import asynccontextmanager
import hashlib
import logging
import socket
import os
from pathlib import Path
import sys
from threading import Thread
from time import sleep
import webbrowser


ROOT = Path(__file__).resolve().parent
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"
HOST = "127.0.0.1"
PORT = 8765
LOGGER = logging.getLogger("uvicorn.error")


def default_index_dir() -> Path:
    project_id = hashlib.sha256(os.path.normcase(str(ROOT)).encode("utf-8")).hexdigest()[:16]
    return Path(os.environ["LOCALAPPDATA"]) / "FusionKnowledgeBase" / project_id / "indexes"


def open_page_when_ready(server, url: str) -> None:
    while not server.started:
        sleep(0.1)
    if not webbrowser.open(url):
        print(f"请在浏览器打开 {url}", flush=True)


def prepare_first_index() -> None:
    from app.knowledge import documents

    try:
        inventory = documents.list_documents()
        if inventory["index"] is None and inventory["documents"]:
            LOGGER.info("正在为 %d 份现有文档创建初始索引。", len(inventory["documents"]))
            documents.reindex_documents()
    except Exception:
        LOGGER.exception("初始索引创建失败。服务将继续启动，可在网页中重试重建索引。")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=PORT)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    if not (FRONTEND / "index.html").is_file():
        raise SystemExit("找不到融合前端。请在项目根目录运行 node frontend/build-demo.cjs 后重试。")
    if not (BACKEND / "app" / "main.py").is_file():
        raise SystemExit(f"找不到后端入口 {BACKEND / 'app' / 'main.py'}")

    os.chdir(BACKEND)
    sys.path.insert(0, str(BACKEND))
    try:
        from dotenv import dotenv_values
        import uvicorn

        file_settings = {key.upper(): value for key, value in dotenv_values(BACKEND / ".env").items()}
        if os.name == "nt" and not (os.getenv("INDEX_DIR") or file_settings.get("INDEX_DIR")):
            index_dir = default_index_dir()
            if not str(index_dir).isascii():
                raise SystemExit("向量索引需要英文路径。请在后端 .env 中把 INDEX_DIR 设为英文目录。")
            os.environ["INDEX_DIR"] = str(index_dir)
        if "PREWARM_MODELS" not in file_settings:
            os.environ.setdefault("PREWARM_MODELS", "false")

        from app.config import get_settings
        from app.main import app
    except ModuleNotFoundError as error:
        command = f'"{sys.executable}" -m pip install -r "{BACKEND / "requirements.txt"}"'
        raise SystemExit(f"缺少 Python 依赖 {error.name}。请运行以下命令后重试。\n{command}") from error

    settings = get_settings()
    if not settings.deepseek_api_key:
        print("尚未配置模型。文档和原文可浏览。请在后端 .env 中配置 DEEPSEEK_API_KEY。", flush=True)

    url = f"http://{HOST}:{args.port}/fusion"
    print(f"融合知识库地址 {url}\n索引目录 {settings.index_dir}", flush=True)
    original_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def fusion_lifespan(app):
        async with original_lifespan(app):
            prepare_first_index()
            yield

    app.router.lifespan_context = fusion_lifespan
    config = uvicorn.Config(app, host=HOST, port=args.port)
    server = uvicorn.Server(config)
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    with listener:
        try:
            listener.bind((HOST, args.port))
        except OSError as error:
            raise SystemExit(f"无法使用端口 {args.port}。请关闭正在运行的服务，或使用 --port 指定其他端口。\n{error}") from error
        if not args.no_browser:
            Thread(target=open_page_when_ready, args=(server, url), daemon=True).start()
        server.run(sockets=[listener])
    if not server.started:
        raise SystemExit("融合知识库启动失败，请查看上方日志。")


if __name__ == "__main__":
    main()
