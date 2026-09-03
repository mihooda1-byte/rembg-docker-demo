import os
from functools import lru_cache

import onnxruntime as ort
from rembg import new_session, remove


MODEL_NAME = os.getenv("MODEL_NAME", "u2net")
CPU_PROVIDER = "CPUExecutionProvider"


@lru_cache(maxsize=1)
def get_session():
    """rembgのモデルをCPU用に一度だけ読み込む。"""
    session = new_session(
        MODEL_NAME,
        providers=[CPU_PROVIDER],
    )

    active_providers = session.inner_session.get_providers()

    if CPU_PROVIDER not in active_providers:
        raise RuntimeError(
            "rembgの推論セッションでCPUが有効になっていません。"
            f" 有効な実行環境: {active_providers}"
        )

    return session


def remove_background_bytes(image_data: bytes) -> bytes:
    """受け取った画像データから背景を削除し、PNGデータを返す。"""
    return remove(
        image_data,
        session=get_session(),
        force_return_bytes=True,
    )


def get_runtime_info() -> dict:
    """使用モデルとCPU providerの動作状況を返す。"""
    session = get_session()

    return {
        "model": MODEL_NAME,
        "available_providers": ort.get_available_providers(),
        "active_providers": session.inner_session.get_providers(),
    }