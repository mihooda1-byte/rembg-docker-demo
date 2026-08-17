from io import BytesIO

import gradio as gr
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import Response
from PIL import Image, UnidentifiedImageError

from app.inference import get_runtime_info, remove_background_bytes


app = FastAPI(
    title="rembg GPU API",
    description="Dockerコンテナ上でU2-Netを実行する画像背景削除API",
    version="1.0.0",
)


@app.get("/")
def root():
    return {
        "message": "rembg GPU API is running",
        "fastapi_docs": "/docs",
        "gradio_gui": "/gradio",
    }


@app.get("/health")
def health():
    """モデル名とGPUの使用状況を確認する。"""
    try:
        return {
            "status": "ok",
            **get_runtime_info(),
        }
    except Exception as error:
        raise HTTPException(status_code=500, detail=str(error)) from error


@app.post(
    "/api/remove-background",
    responses={
        200: {
            "content": {"image/png": {}},
            "description": "背景を削除したPNG画像",
        }
    },
)
async def remove_background(file: UploadFile = File(...)):
    """curlなどから受け取った画像の背景を削除する。"""
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(
            status_code=400,
            detail="画像ファイルを送信してください。",
        )

    image_data = await file.read()

    if not image_data:
        raise HTTPException(
            status_code=400,
            detail="画像ファイルが空です。",
        )

    try:
        result = remove_background_bytes(image_data)
    except UnidentifiedImageError as error:
        raise HTTPException(
            status_code=400,
            detail="画像を読み込めませんでした。",
        ) from error
    except Exception as error:
        raise HTTPException(status_code=500, detail=str(error)) from error

    return Response(
        content=result,
        media_type="image/png",
        headers={
            "Content-Disposition": 'attachment; filename="result.png"',
        },
    )


def run_gradio(image: Image.Image) -> Image.Image:
    """Gradioの画面とGradio APIから使用する処理。"""
    if image is None:
        raise gr.Error("画像を選択してください。")

    input_buffer = BytesIO()
    image.convert("RGB").save(input_buffer, format="PNG")

    result = remove_background_bytes(input_buffer.getvalue())

    output_image = Image.open(BytesIO(result))
    output_image.load()

    return output_image


with gr.Blocks(title="rembg GPU Demo") as gradio_demo:
    gr.Markdown(
        """
        # rembg（U2-Net）GPUデモ

        画像を選択して「背景を削除」を押してください。
        推論はDockerコンテナ内のNVIDIA GPUで実行されます。
        """
    )

    with gr.Row():
        input_image = gr.Image(
            type="pil",
            label="入力画像",
        )
        output_image = gr.Image(
            type="pil",
            label="推論結果",
        )

    run_button = gr.Button(
        "背景を削除",
        variant="primary",
    )
    clear_button = gr.ClearButton(
        [input_image, output_image],
        value="クリア",
    )

    run_button.click(
        fn=run_gradio,
        inputs=input_image,
        outputs=output_image,
        api_name="remove_background",
    )


app = gr.mount_gradio_app(
    app,
    gradio_demo,
    path="/gradio",
    max_file_size="20mb",
)