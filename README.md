# rembg CPU Docker Demo

学習済みモデルrembg（U2-Net）をCPUだけで実行し、画像の背景を削除するDockerデモ環境です。NVIDIA GPU、CUDA、cuDNNは不要です。

以下の3種類の方法から推論を実行できます。

- GradioのGUI
- GradioのAPI
- Gradioとは独立したFastAPIの推論API

## 想定環境

- Docker
- Python 3.12
- rembg：2.0.79
- onnxruntime：1.23.2
- FastAPI：0.128.0
- Gradio：6.2.0

CPU版のbuild、推論、memory使用量、Docker image容量をローカルで確認済みです。以下の数値は会社PC上での単回測定による参考値であり、入力画像、Docker環境、測定タイミングにより変動します。

## ディレクトリ構成

```text
rembg-docker-demo/
├── app/
│   ├── __init__.py
│   ├── inference.py
│   └── main.py
├── .dockerignore
├── .gitignore
├── Dockerfile
├── README.md
└── requirements.txt
```

## 構成

- `app/inference.py`
  - rembgの推論sessionを作成します。
  - `CPUExecutionProvider`だけを明示的に使用します。
  - 読み込んだモデルをprocess内で再利用します。
- `app/main.py`
  - FastAPIのendpointを提供します。
  - GradioのGUIとAPIをFastAPI上にmountします。
  - `/health`は初回アクセス時にモデル取得とsession初期化を伴う可能性があります。
- `Dockerfile`
  - Python、CPU版ONNX Runtime、rembgなどを含むDocker imageを作成します。
  - `PORT`環境変数を使用し、未設定時は8000で起動します。
  - `U2NET_HOME=/tmp/rembg-models`をモデルcache pathとして使用します。
  - `MODEL_NAME`と`ENABLE_GRADIO`を環境変数で変更できます。

`MODEL_NAME`のdefaultは`u2net`です。`MODEL_NAME=u2netp`を指定すると軽量モデルを使用できます。
`ENABLE_GRADIO=true`が通常設定で、Gradio GUI/APIを有効にします。`ENABLE_GRADIO=false`ではGradioをimport・初期化・mountせず、FastAPIの`/health`、`/docs`、`POST /api/remove-background`だけを提供します。

## Dockerイメージのビルド

projectのroot directoryで実行します。

```powershell
docker build --progress=plain -t rembg-cpu-demo .
```

HerokuおよびさくらAppRun向けには`linux/amd64` imageが必要か、各platformの現行仕様とbuild結果をdeployment前に確認してください。

## コンテナの起動

```powershell
docker run --rm `
    -p 8000:8000 `
    -e PORT=8000 `
  -e MODEL_NAME=u2net `
  -e ENABLE_GRADIO=true `
    -v rembg-models:/tmp/rembg-models `
    --name rembg-cpu `
    rembg-cpu-demo
```

`rembg-models`というnamed volumeを`/tmp/rembg-models`へmountすることで、ダウンロードしたU2-Netモデルをcontainer終了後も再利用できます。

cache pathは`U2NET_HOME=/tmp/rembg-models`で、rembg 2.0.79が参照する環境変数と実コードの設定を一致させています。defaultの`/tmp/rembg-models`はDockerfileでroot所有directoryとして事前作成せず、実行時にrembgへ作成させるため、非root環境でも書き込めます。

初回の推論または`/health`アクセスでは、約176 MBのU2-Netモデル取得と推論session初期化が発生する可能性があります。

Heroku Ecoなどのメモリ制限環境では、Gradioを無効化して軽量モデルを指定できます。

```powershell
docker run --rm `
  -p 8000:8000 `
  -e PORT=8000 `
  -e MODEL_NAME=u2netp `
  -e ENABLE_GRADIO=false `
  -v rembg-models-api:/tmp/rembg-models `
  --name rembg-cpu-api `
  rembg-cpu-demo
```

この設定でも`/health`、`/docs`、`POST /api/remove-background`は利用できます。`/gradio/`は提供されません。通常設定では既存のGradio GUI/APIを利用できます。

512 MB制限のDocker検証では、`--memory=512m`、`MODEL_NAME=u2netp`、`ENABLE_GRADIO=false`、専用named volume、host port `8010`を使用し、起動直後から2秒間隔で`GET /`を確認します。`/`が`200`になった後に`/docs`、`/health`、背景除去APIを各1回実行し、`docker stats`、`docker inspect`、Dockerログでpeak memory、`OOMKilled`、エラーを確認します。Gradioは無効化されるため、`/gradio/`の`404`も確認します。

### Heroku Eco相当の512 MiBローカル検証結果

- Docker memory limit：512 MiB
- `MODEL_NAME=u2netp`
- `ENABLE_GRADIO=false`
- 起動時memory：463.2 MiB
- `/health`後memory：483.6 MiB
- `u2netp` model：約4.57 MB
- FastAPI背景除去：3.46秒、HTTP 200、有効なPNG
- `/docs`：200
- `/gradio/`：404
- `OOMKilled`：false
- 停止後Exit code：0

512 MiBで完走しましたが、上限の約94%を使用しており余裕が小さいため、Heroku上での実測が必要です。

## ローカル実測の参考値

以下は会社PC上での単回測定による参考値です。入力画像、Docker環境、測定タイミングにより変動します。

- Docker image容量：967,299,564 bytes
- U2-Netモデル容量：175,997,641 bytes
- 初回`/health`：12.480440秒、モデル初期化peak memory：1.131 GiB
- FastAPI背景除去API：0.391885秒、処理後memory：1.263 GiB
- named volume cache再利用後の`/health`：0.667079秒
- Gradio API：0.930153秒、処理後memory：1.392 GiB

1 GB級の環境では現在構成のmemoryが不足する可能性が高いため、Herokuでは2 GB以上のdynoを候補にして実測が必要です。

## アクセス先

container起動後、次のURLを使用します。

| 機能 | URL |
|---|---|
| トップページ | http://localhost:8000/ |
| CPU provider・モデル動作確認 | http://localhost:8000/health |
| FastAPIドキュメント | http://localhost:8000/docs |
| Gradio GUI | http://localhost:8000/gradio/ |

## CPU providerの動作確認

PowerShellから次のコマンドを実行します。

```powershell
Invoke-RestMethod http://localhost:8000/health |
    ConvertTo-Json -Depth 3
```

正常時は`active_providers`に`CPUExecutionProvider`が表示されます。

```json
[
  "CPUExecutionProvider"
]
```

`/health`は`get_session()`を呼ぶため、軽量なliveness checkではありません。初回アクセスではモデル取得とsession初期化に伴って応答が遅くなり、network、disk、memoryも使用します。最初のPoCでは既存のendpointと挙動を維持します。

## Gradio GUIから推論する

browserで次のURLを開きます。

```text
http://localhost:8000/gradio/
```

画像を選択して「背景を削除」を押すと、背景を削除した画像が表示されます。

現在のGradio既定動作では、GUIの出力はWEBPです。

## FastAPIから推論する

PowerShellでは`curl`ではなく、`curl.exe`を使用します。

```powershell
curl.exe -X POST `
  -F "file=@input.jpg" `
  http://localhost:8000/api/remove-background `
  --output output.png
```

成功すると、背景を削除した画像が`output.png`として保存されます。

## Gradio APIから推論する

```python
from gradio_client import Client, handle_file

client = Client("http://localhost:8000/gradio/")

result = client.predict(
    image=handle_file("input.jpg"),
    api_name="/remove_background",
)

print(result)
```

現在のGradio既定動作では、Gradio APIの出力もWEBPです。FastAPI背景除去APIとは異なり、PNG出力を保証しません。

利用可能なGradio APIは次のコードで確認できます。

```python
from gradio_client import Client

client = Client("http://localhost:8000/gradio/")
client.view_api()
```

## コンテナの停止

containerを起動しているterminalで`Ctrl + C`を押します。別のPowerShellから停止する場合は次を実行します。

```powershell
docker stop rembg-cpu
```

## Herokuで使用する場合

同じDocker imageをHeroku Container Runtimeで使う想定です。UvicornはHerokuが設定する`PORT`でlistenし、ローカルで`PORT`が未設定の場合は8000を使用します。

Herokuではcontainer filesystemが一時的でDocker volumeをmountできません。そのため、`/tmp/rembg-models`へ取得したモデルcacheはdynoのrestart・置換時に失われ、再取得が必要です。

U2-Net、ONNX Runtime、FastAPI、Gradioを同じprocessで動かすため、小さいdynoではmemory不足になる可能性があります。特に1 GB級では現在構成が不足する可能性が高く、2 GB以上を候補にしてdeployment前に、起動直後、モデルload後、FastAPI推論時、Gradio推論時のmemoryを測定してください。

secretsやHeroku API keyはDockerfile、Docker image、`.env`、Git履歴へ含めないでください。

## さくらAppRunへ展開する場合

今回のimage容量は2 GiB未満でしたが、約176 MBのU2-Netモデルを一時領域256 MiBへ保存すると余裕は小さい点に注意が必要です。起動4分以内の条件も含め、package展開、temporary file、入力画像、出力画像を含めて別途検証が必要です。

## 動作確認項目

CPU版では以下を今後確認します。

- CUDA、cuDNN、NVIDIA libraryを含まずにbuildできる
- `CPUExecutionProvider`だけでsessionが作成される
- `/health`、`/docs`、`/gradio`が応答する
- FastAPIからPNG形式の推論結果を取得できる
- named volumeからモデルcacheを再利用できる
- `PORT`未設定時は8000、設定時は指定portでlistenする
- Docker imageの圧縮後・展開後容量
- 起動直後、モデルload後、推論peak時のmemory使用量

## GPU版での過去の確認記録

以下は変更前のGPU版で確認された履歴であり、CPU版の動作確認結果ではありません。

- Gradio GUI、Gradio API、FastAPIから背景除去を実行
- NVIDIA RTX 3090上で`CUDAExecutionProvider`を使用
- FastAPIとGradioからPNG出力を確認

既存の`docs/images/`以下のスクリーンショットもGPU版の履歴です。CPU版の検証後に、必要に応じてCPU版の記録へ更新します。

## 参考資料

- [rembg](https://github.com/danielgatis/rembg)
- [FastAPI](https://fastapi.tiangolo.com/)
- [Gradio](https://www.gradio.app/)
- [Python Docker Official Image](https://hub.docker.com/_/python)
- [Heroku Container Registry & Runtime](https://devcenter.heroku.com/articles/container-registry-and-runtime)
