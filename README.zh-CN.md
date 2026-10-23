<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/images/logo-dark.png">
    <img src="docs/images/logo-light.png" alt="Model Depot" width="475">
  </picture>
</p>

<p align="center"><a href="README.md">English</a> · <b>简体中文</b></p>

Model Depot 是本机图像生成模型的管理程序。它清点电脑上的 Checkpoint、扩散模型、LoRA、VAE 等模型文件，凭文件指纹与 [civitai](https://civitai.com) 核对每个文件的来源，从 civitai 下载和更新模型，并为每个模型保存一张推荐参数资料卡。[ComfyUI](https://github.com/Comfy-Org/ComfyUI) 等出图程序可经本地接口读取模型清单与推荐参数。全部数据保存在本机。

版本 0.6.4 · 提供 Windows 应用与 Windows 安装包 · 中英文界面 · [GNU GPL 3.0](LICENSE)

## 功能

**模型库**
- 按 ComfyUI 的方式扫描模型文件夹（包括 `extra_model_paths.yaml` 与 `--models-directory`），由文件头判断类型与基础模型。
- 凭 SHA-256 指纹在 civitai.com 与 civitai.red 上识别每个文件，文件改名或移动后仍能对应原有资料。civitai 的资料在本地保存快照，模型下架后照常可查。
- 模型库为按基础模型分组的卡片墙，带索引、类型筛选、搜索、分组折叠与多选。封面可用眼睛开关隐藏；此项只是显示上的偏好，不是筛选。
- 读取旧版 WebUI 留在模型旁的 `.civitai.info`、`.json`、`.preview.png` 文件，只读不改。

**模型档案**
- 以作者的示例图开头，每张图附提示词、出图设置与所用资源；示例图首次查看时下载并保存，离线也可查看。
- 触发词可一键复制，并标出可选的词。
- 推荐参数资料卡由示例图的出图设置与作者说明中写明的设置生成。作者说明可交给本地语言模型（llama.cpp 加 GGUF 模型）读取；未配置本地模型时按规则读取。
- 可修改名称、基础模型、推荐参数、触发词与备注；可添加自己的图片（读取 WebUI 或 ComfyUI 写入 PNG、JPEG、WebP 文件的出图参数），并可将任一张静态图片设为封面。
- civitai 无法识别的模型（例如自行训练的 LoRA）按训练工具写入文件的信息给出名称、触发词与训练信息。

**下载与更新**
- 在模型库中任意位置粘贴一个或多个 civitai 链接，选择版本与文件，确认或修改建议的存放位置后加入队列。
- Hugging Face 上单个文件的链接同样可以读取：文本编码器、VAE、SeedVR2 与 ComfyUI 官方文件多发布在那里。存放位置按文件在仓库中的路径建议。
- 队列一次下载一个文件，中断或重启后从断点继续；文件指纹与 civitai 的记录（或 Hugging Face 公布的指纹）一致后才收入模型库，不覆盖已有文件。
- `GET /api/v1/companions` 列出各基础模型系列在模型之外所需的文件（文本编码器、VAE 等），附官方下载地址与指纹，并注明模型库中有无。
- 手动检查更新：在模型的 civitai 页面上找出同一模型的新版本，并与作者放在同一页面的其他条目区分开。

**整理**
- 删除只移入回收站，模型旁的 WebUI 资料文件一并移入，不做永久删除。
- 待整理文件夹：放入其中的模型文件先列出各自将移入的位置，确认后移入模型库。

**供其他程序使用**
- 本地 HTTP 接口位于 `/api/v1/` 之下，提供模型清单、单个模型的详情与资料卡、封面与示例图，模型名采用 ComfyUI 的写法；另以事件推送（SSE）通知模型库变化与下载进度。

## 开始使用

### 用 Windows 安装包安装

安装包一并装入 Model Depot 及其自用的 Python、含插件与工作流的 ComfyUI 便携版、本地语言模型的运行程序 llama.cpp，以及 PixAI 打标模型，安装过程无需联网。本机已装有 ComfyUI 便携版时，安装程序可直接沿用，不再另装一份：该 ComfyUI 的原有文件不变，工作流放入其工作流列表中的「Model Depot」文件夹，缺少的插件在安装时列出，可经 ComfyUI 管理器安装。安装时选择安装位置、模型库位置与 Model Depot 的数据文件夹。安装包不含模型文件：将模型文件放入安装包旁的 `Model Inbox` 文件夹，Model Depot 首次启动时列出各文件将移入的位置，确认后移入模型库。自带的 ComfyUI 只带少数插件，每个都有用途（GGUF 格式模型所需的 ComfyUI-GGUF、PixAI 打标、Impact Pack 与 Subpack、controlnet_aux），并附 ComfyUI 与 llama.cpp 所需的 Visual C++ 运行库文件。运行新版安装包即在原位置更新；ComfyUI 的设置、启动脚本、其他程序或手动安装的插件，以及修改过的工作流均保留。卸载只删除安装包自带的插件。

系统要求：Windows 10 或 11；NVIDIA RTX 20 系列或更新的显卡，驱动 580 或更新；约 9 GB 可用空间，模型另需空间。安装程序未经数字签名，首次运行时 Windows 可能要求确认。

### 从源代码运行

需要 Python 3.10 或更高版本，以及 `aiohttp` 与 `Pillow`（`pip install -r requirements.txt`）。ComfyUI 便携版自带的 Python 已包含二者。

在 Windows 上双击 `启动.cmd`（或 `Start.cmd`）在后台启动 Model Depot 并在浏览器中打开页面，双击 `停止.cmd`（或 `Stop.cmd`）将其关闭。首次启动时会查找可用的 Python，安装任何组件之前先行询问。

其他系统：

```
python run.py            # 启动
python run.py --stop     # 停止
python run.py --check    # 检查当前 Python 能否运行 Model Depot
```

页面地址为 http://127.0.0.1:8194/。首次打开时，设置向导依次设置界面语言、模型文件夹（本机装有 ComfyUI 时可直接读取）、网络连接（默认使用系统代理，也可直接连接或填写代理；「测试连接」可查看连不上 civitai 时卡在哪一步），以及可选的 civitai API 密钥；只有作者要求登录的模型才需要该密钥。

### Windows 应用

`desktop\app\ModelDepot.exe` 在独立窗口中显示 Model Depot，程序未运行时先将其启动。关闭窗口后 Model Depot 在通知区域继续运行；在图标的右键菜单中选择「退出」即停止。需要 Windows 10 或 11 及 Microsoft Edge WebView2 运行库，两者均已自带。

## 网络与隐私

- Model Depot 默认只监听 127.0.0.1。若要允许其他设备调用接口，须在 `config.json` 中修改 `server.host`，并启用 `remote_access`，设置不少于 8 个字符的访问密钥。
- 识别模型、读取模型页与下载文件时访问 civitai.com、civitai.red 及 civitai 的图片服务器；请求刻意放慢，结果缓存。civitai API 密钥只发送给 civitai.com 与 civitai.red。
- 连不上 civitai 时，Model Depot 在启动时与请求失败后各探测一次；确认连不上后，按已保存的资料离线使用，不再自行访问，直到手动重试、修改网络设置或重新启动。
- 粘贴 Hugging Face 文件链接时访问 Hugging Face；在设置中选择安装本地语言模型时，从 GitHub 下载 llama.cpp 的发布版本，从 Hugging Face 下载模型文件。除此之外不访问其他地址，也不上报任何信息。
- Model Depot 不审核内容。按分级隐藏封面只是显示上的偏好，可以关闭。

## 数据

Model Depot 写入的全部内容都在一个数据文件夹中：默认为项目下的 `data/`，也可由环境变量 `MODEL_DEPOT_DATA` 指定，用安装包安装时则为安装时选定的位置。

| 位置 | 内容 |
|---|---|
| `config.json` | 设置 |
| `library/` | 每个模型一份资料与一张封面，以指纹命名；保存的 civitai 模型页；添加的图片副本。该文件夹是需要备份的部分，其中不含本机路径。 |
| `index.sqlite` | 索引，必要时由模型文件与资料重建 |
| `cache/` | 作者示例图的小图，可以删除 |
| `logs/` | 日志 |

页面上未提供的设置在 `config.json` 中修改，修改后需重新启动：`server.host`、`server.port`（默认 8194）、`remote_access`、`hashing.workers`，以及 `prewarm`（预热：每次扫描结束后预先读取模型页与示例图，默认关闭）。

## 文档

- [docs/安装包对接说明.md](docs/安装包对接说明.md)：用安装包安装后各部分的位置，以及其他程序如何查找与使用

## 开发

| 位置 | 内容 |
|---|---|
| `depot/` | 后台程序：扫描、指纹、civitai、下载、资料卡、接口（Python，aiohttp） |
| `web/` | 页面：原生 ES 模块，无构建步骤，字体由本地提供 |
| `desktop/` | Windows 应用（C#、WinForms、WebView2、.NET Framework 4.8）；编译结果 `desktop/app/` 保存在仓库中 |
| `installer/` | Windows 安装包：`build.py` 暂存并打包各部分，`compile.py` 编译两个程序，`setup/` 为安装程序 |
| `scripts/` | Windows 的启动与停止脚本 |

Windows 应用用 `dotnet build desktop\ModelDepot.csproj -c Release` 编译；只装有 Visual Studio Build Tools 的电脑用 `python installer\compile.py desktop`。替换 `desktop\app\` 中的文件之前，先用 `desktop\app\ModelDepot.exe --quit` 关闭正在运行的程序。

安装包用 `python installer\build.py` 制作；制作所用电脑的路径写在 `installer\build.local.json` 中，格式见 `installer\build.example.json`。

## 许可证

Copyright (C) 2026 ZEUS INSiGHTS

Model Depot 是自由软件，采用 [GNU 通用公共许可证第 3 版](LICENSE)（GNU GPL 3.0）。任何人均可使用、修改与再分发；分发修改后的版本时，须以同一许可证发布，并提供源代码。依该许可证第 7 条第 (e) 款，不授予「Model Depot」名称、字标与图标的使用权。

第三方组件沿用各自的许可证，列于 [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md)。

Model Depot 是独立项目，与 Civitai、ComfyUI 均无隶属关系。
