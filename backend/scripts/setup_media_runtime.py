"""Install project-local CPU engines using a Python 3.12 interpreter."""
import json
import os
from pathlib import Path
import sys


def main():
    if sys.version_info[:2] != (3, 12):
        raise SystemExit("请用Python 3.12执行本脚本，主应用可继续使用原来的Python")
    root = Path(__file__).resolve().parents[1]
    target = root / '.venv-media/Lib/site-packages'
    cache = root / '.cache-media'
    target.mkdir(parents=True, exist_ok=True)
    cache.mkdir(exist_ok=True)
    import pip._vendor.platformdirs.windows as windows
    # Restricted Windows accounts may lack Shell Folders registry values.
    if sys.platform == 'win32':
        windows.get_win_folder = lambda name: str(cache)
    from pip._internal.cli.main import main as pip_main
    status = pip_main(['--isolated', 'install', '--disable-pip-version-check', '--no-warn-script-location',
        '--ignore-installed', '--upgrade', '--target', str(target), '--cache-dir', str(cache / 'pip'),
        '-r', str(root / 'requirements-media.txt')])
    if status:
        raise SystemExit(status)
    sys.path.insert(0, str(target))
    os.environ['HF_HUB_DISABLE_XET'] = '1'
    os.environ['HF_HOME'] = str(root / 'storage/media-models/hub')
    from huggingface_hub import snapshot_download
    from faster_whisper import WhisperModel
    from rapidocr_onnxruntime import RapidOCR
    model_path = root / 'storage/media-models/faster-whisper-base'
    snapshot_download('Systran/faster-whisper-base', local_dir=str(model_path),
                      allow_patterns=['config.json', 'model.bin', 'tokenizer.json', 'vocabulary.*', 'preprocessor_config.json'])
    RapidOCR()
    WhisperModel(str(model_path), device='cpu', compute_type='int8', local_files_only=True)
    sys.path.insert(0, str(root))
    from app.persistence import atomic_write
    config = {'python': sys.executable, 'site_packages': '.venv-media/Lib/site-packages',
              'asr_model': 'storage/media-models/faster-whisper-base'}
    atomic_write(root / 'storage/media-runtime.json', json.dumps(config, ensure_ascii=False).encode('utf-8'))
    print("本地OCR与转写运行环境已安装，配置已保存到storage/media-runtime.json")


if __name__ == '__main__':
    main()
