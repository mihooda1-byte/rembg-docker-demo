import json
import os
import resource
import socket
import time
from pathlib import Path

import modal


APP_NAME = "rembg-cpu-modal-poc"
MODEL_NAME = "u2netp"
MODEL_CACHE = "/tmp/rembg-models"
COLD_WAIT_SECONDS = 90
SCALEDOWN_WINDOW_SECONDS = 60

image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("ca-certificates", "libgomp1")
    .pip_install_from_requirements("requirements.txt")
    .env(
        {
            "MODEL_NAME": MODEL_NAME,
            "ENABLE_GRADIO": "false",
            "U2NET_HOME": MODEL_CACHE,
        }
    )
    .add_local_python_source("app.inference")
)

app = modal.App(APP_NAME)
_process_call_count = 0


def _process_max_rss_kib() -> int:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss


def _model_file_snapshot() -> dict:
    model_path = Path(MODEL_CACHE) / f"{MODEL_NAME}.onnx"

    if not model_path.is_file():
        return {
            "path": str(model_path),
            "exists": False,
            "size_bytes": None,
        }

    return {
        "path": str(model_path),
        "exists": True,
        "size_bytes": model_path.stat().st_size,
    }


def _cache_info_dict(cache_info) -> dict:
    return {
        "hits": cache_info.hits,
        "misses": cache_info.misses,
        "maxsize": cache_info.maxsize,
        "currsize": cache_info.currsize,
    }


@app.function(
    image=image,
    cpu=2,
    memory=2048,
    timeout=180,
    max_containers=1,
    min_containers=0,
    scaledown_window=SCALEDOWN_WINDOW_SECONDS,
    retries=0,
)
def remove_background(image_data: bytes) -> dict:
    global _process_call_count

    _process_call_count += 1
    process_call_number = _process_call_count
    function_started_at = time.perf_counter()

    import_started_at = time.perf_counter()
    from app.inference import get_runtime_info, get_session, remove_background_bytes
    inference_import_seconds = time.perf_counter() - import_started_at

    session_cache_before = _cache_info_dict(get_session.cache_info())
    model_file_before = _model_file_snapshot()

    session_started_at = time.perf_counter()
    get_session()
    session_prepare_seconds = time.perf_counter() - session_started_at

    session_cache_after = _cache_info_dict(get_session.cache_info())
    model_file_after = _model_file_snapshot()

    background_processing_started_at = time.perf_counter()
    result = remove_background_bytes(image_data)
    background_processing_seconds = time.perf_counter() - background_processing_started_at
    processing_seconds = time.perf_counter() - function_started_at
    runtime_info = get_runtime_info()

    return {
        "output_png": result,
        "inference_import_seconds": inference_import_seconds,
        "session_prepare_seconds": session_prepare_seconds,
        "session_prepare_measurement": (
            "Elapsed time in get_session(); may include model download and "
            "ONNX session initialization, which are not separated."
        ),
        "background_processing_seconds": background_processing_seconds,
        "background_processing_measurement": (
            "Elapsed time for remove_background_bytes(), including image "
            "preprocessing and postprocessing."
        ),
        "processing_seconds": processing_seconds,
        "processing_measurement": (
            "Elapsed time from Function-side start through "
            "remove_background_bytes() completion; it includes inference "
            "dependency import and session preparation."
        ),
        "model_file_before": model_file_before,
        "model_file_after": model_file_after,
        "model_file_measurement": (
            "Expected model path only; file existence does not prove whether "
            "download occurred, and absence does not alone prove acquisition "
            "failure."
        ),
        "session_cache_before": session_cache_before,
        "session_cache_after": session_cache_after,
        "model": runtime_info["model"],
        "active_providers": runtime_info["active_providers"],
        "process_id": os.getpid(),
        "process_hostname": socket.gethostname(),
        "process_call_number": process_call_number,
        "first_process_call": process_call_number == 1,
        "process_max_rss_kib": _process_max_rss_kib(),
        "memory_measurement": "process max RSS from resource.getrusage, KiB; not container-wide",
    }


def _validate_output_path(output_path: str) -> Path:
    output_file = Path(output_path)

    if os.path.lexists(output_file):
        raise FileExistsError(f"Output file already exists: {output_file}")
    if not output_file.parent.is_dir():
        raise FileNotFoundError(f"Output parent directory does not exist: {output_file.parent}")

    return output_file


def _output_location(output_file: Path) -> str:
    return os.path.normcase(os.path.realpath(os.path.abspath(output_file)))


def _invoke_once(input_data: bytes) -> dict:
    client_started_at = time.perf_counter()
    result = remove_background.remote(input_data)
    client_seconds = time.perf_counter() - client_started_at
    result["client_remote_call_seconds"] = client_seconds
    return result


def _save_result(result: dict, output_file: Path) -> dict:
    output_data = result.pop("output_png")
    with output_file.open("xb") as output_stream:
        output_stream.write(output_data)

    result["output_path"] = str(output_file)
    return result


def _warm_reuse_confirmed(first_result: dict, second_result: dict) -> bool:
    return bool(
        first_result["process_hostname"] == second_result["process_hostname"]
        and first_result["process_id"] == second_result["process_id"]
        and second_result["process_call_number"] == first_result["process_call_number"] + 1
        and second_result["first_process_call"] is False
    )


def _cold_process_comparison(
    first_result: dict,
    second_result: dict,
    actual_wait_seconds: float,
) -> dict:
    hostname_changed = first_result["process_hostname"] != second_result["process_hostname"]
    process_id_changed = first_result["process_id"] != second_result["process_id"]
    idle_exceeded_scaledown_window = actual_wait_seconds > SCALEDOWN_WINDOW_SECONDS
    process_identity_changed = hostname_changed or process_id_changed
    function_state_reset_observed = bool(
        second_result["first_process_call"]
        and second_result["process_call_number"] == 1
    )
    session_cache_reset_observed = bool(
        first_result["session_cache_after"]["currsize"] == 1
        and second_result["session_cache_before"]["currsize"] == 0
        and second_result["session_cache_before"]["hits"] == 0
        and second_result["session_cache_before"]["misses"] == 0
    )
    temporary_model_cache_reset_observed = bool(
        first_result["model_file_after"]["exists"]
        and not second_result["model_file_before"]["exists"]
    )
    state_and_temporary_cache_reset_after_idle_observed = bool(
        idle_exceeded_scaledown_window
        and function_state_reset_observed
        and session_cache_reset_observed
        and temporary_model_cache_reset_observed
    )
    new_process_after_idle_confirmed = bool(
        actual_wait_seconds > SCALEDOWN_WINDOW_SECONDS
        and second_result["first_process_call"]
        and second_result["process_call_number"] == 1
        and (hostname_changed or process_id_changed)
    )

    return {
        "idle_exceeded_scaledown_window": idle_exceeded_scaledown_window,
        "process_hostname_changed": hostname_changed,
        "process_id_changed": process_id_changed,
        "process_identity_changed": process_identity_changed,
        "second_process_call_number": second_result["process_call_number"],
        "second_first_process_call": second_result["first_process_call"],
        "function_state_reset_observed": function_state_reset_observed,
        "session_cache_reset_observed": session_cache_reset_observed,
        "temporary_model_cache_reset_observed": temporary_model_cache_reset_observed,
        "state_and_temporary_cache_reset_after_idle_observed": (
            state_and_temporary_cache_reset_after_idle_observed
        ),
        "new_process_after_idle_confirmed": new_process_after_idle_confirmed,
    }


def _remove_process_identifiers(result: dict) -> None:
    result.pop("process_hostname", None)
    result.pop("process_id", None)


@app.local_entrypoint()
def main(
    input_path: str,
    output_path: str,
    warm: bool = False,
    warm_output_path: str | None = None,
    cold_after_scaledown: bool = False,
    cold_output_path: str | None = None,
) -> None:
    input_file = Path(input_path)

    if not input_file.is_file():
        raise FileNotFoundError(f"Input file is not a regular file: {input_file}")
    if warm and cold_after_scaledown:
        raise ValueError("warm and cold_after_scaledown cannot be enabled together")
    if not warm and warm_output_path is not None:
        raise ValueError("warm_output_path requires warm to be enabled")
    if not cold_after_scaledown and cold_output_path is not None:
        raise ValueError("cold_output_path requires cold_after_scaledown to be enabled")

    first_output_file = _validate_output_path(output_path)
    second_output_file = None
    if warm:
        if warm_output_path is None:
            raise ValueError("warm_output_path is required when warm is enabled")
        second_output_file = _validate_output_path(warm_output_path)
        if _output_location(first_output_file) == _output_location(second_output_file):
            raise ValueError("Warm output paths must refer to different locations")
    elif cold_after_scaledown:
        if cold_output_path is None:
            raise ValueError("cold_output_path is required when cold_after_scaledown is enabled")
        second_output_file = _validate_output_path(cold_output_path)
        if _output_location(first_output_file) == _output_location(second_output_file):
            raise ValueError("Cold output paths must refer to different locations")

    input_data = input_file.read_bytes()
    first_result = _invoke_once(input_data)

    if not warm and not cold_after_scaledown:
        _save_result(first_result, first_output_file)
        print(json.dumps(first_result, ensure_ascii=True, sort_keys=True))
        return

    if cold_after_scaledown:
        _save_result(first_result, first_output_file)
        first_completed_at = time.perf_counter()
        wait_started_at = time.perf_counter()
        time.sleep(COLD_WAIT_SECONDS)
        actual_wait_seconds = time.perf_counter() - wait_started_at
        second_call_elapsed_seconds = time.perf_counter() - first_completed_at

        try:
            second_result = _invoke_once(input_data)
        except Exception:
            raise

        _save_result(second_result, second_output_file)
        comparison = _cold_process_comparison(first_result, second_result, actual_wait_seconds)
        _remove_process_identifiers(first_result)
        _remove_process_identifiers(second_result)
        print(
            json.dumps(
                {
                    "mode": "cold_after_scaledown",
                    "results": [first_result, second_result],
                    "configured_wait_seconds": COLD_WAIT_SECONDS,
                    "actual_wait_seconds": actual_wait_seconds,
                    "first_completion_to_second_call_seconds": second_call_elapsed_seconds,
                    "scaledown_window_seconds": SCALEDOWN_WINDOW_SECONDS,
                    "cold_process_comparison": comparison,
                },
                ensure_ascii=True,
                sort_keys=True,
            )
        )
        return

    try:
        second_result = _invoke_once(input_data)
    except Exception:
        _save_result(first_result, first_output_file)
        raise

    _save_result(first_result, first_output_file)
    _save_result(second_result, second_output_file)
    first_result["warm_reuse_confirmed"] = _warm_reuse_confirmed(first_result, second_result)
    second_result["warm_reuse_confirmed"] = first_result["warm_reuse_confirmed"]
    print(
        json.dumps(
            {
                "mode": "warm",
                "results": [first_result, second_result],
                "warm_reuse_confirmed": first_result["warm_reuse_confirmed"],
            },
            ensure_ascii=True,
            sort_keys=True,
        )
    )
