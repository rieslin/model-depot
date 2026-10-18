<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/images/logo-dark.png">
    <img src="docs/images/logo-light.png" alt="Model Depot" width="475">
  </picture>
</p>

<p align="center"><b>English</b> · <a href="README.zh-CN.md">简体中文</a></p>

Model Depot is a local manager for image-generation models. It takes stock of the Checkpoints, diffusion models, LoRAs, VAEs and other model files on your computer, identifies each one by its fingerprint against [civitai](https://civitai.com), downloads and updates models from civitai, and keeps a card of recommended settings for every model. Image programs such as [ComfyUI](https://github.com/Comfy-Org/ComfyUI) can read the model list and the settings over a local API. Everything is stored on your own computer.

Version 0.6.1 · Windows app and Windows installer · English and Chinese interface · [GNU GPL 3.0](LICENSE)

## Features

**Library**
- Scans the model folders ComfyUI uses (including `extra_model_paths.yaml` and `--models-directory`) and recognises each file's type and base model from its header.
- Identifies every file by its SHA-256 fingerprint on civitai.com and civitai.red, so renaming or moving a file never loses its record. Records from civitai are kept as local snapshots and stay available after a model is taken down.
- Shows the library as a card wall grouped by base model, with an index, type filters, search, collapsible groups and multi-select. Covers can be hidden with an eye toggle; this is a display preference, not a filter.
- Imports the `.civitai.info`, `.json` and `.preview.png` files an older WebUI left beside the models, without changing them.

**Model dossier**
- Leads with the author's example images, each with its prompt, settings and the resources it used; images are fetched once and kept for offline use.
- Trigger words with one-click copy, marking which are optional.
- A recommended-settings card drafted from the example images and from the settings the author's notes state. A local language model (llama.cpp with a GGUF model) can read the notes; without one, rule-based reading is used.
- Edit the name, base model, settings, trigger words and notes; add your own images (their generation settings are read from what WebUI or ComfyUI wrote into PNG, JPEG and WebP files) and choose any still image as the cover.
- Models civitai does not know, such as LoRAs you trained yourself, get a name, trigger words and training facts from the metadata the training tool wrote into the file.

**Downloads and updates**
- Paste one or several civitai links anywhere in the library. Pick the version and file, accept or change the suggested folder, and add to the queue.
- The queue downloads one file at a time, resumes after an interruption or a restart, and adds a file to the library only after its fingerprint matches civitai's record. Existing files are never overwritten.
- A manual update check finds newer versions of the same model on its civitai page, telling them apart from other entries the author keeps there.

**Housekeeping**
- Deleting moves files to the Recycle Bin, together with the WebUI files beside them; nothing is erased permanently.
- A sorting folder: model files dropped into it are listed with the folder each will go to, and moved into the library once you confirm.

**For other programs**
- A local HTTP API under `/api/v1/` gives the model list, each model's details and card, covers and example images, with model names written the way ComfyUI writes them. Server-sent events report library changes and download progress.

## Getting started

### Install with the Windows installer

The setup installs Model Depot together with its own Python, a portable ComfyUI with custom nodes and workflows, llama.cpp for the local language model and the PixAI tagger model, without downloading anything during installation. When a portable ComfyUI is already on the computer, the setup offers to use it instead of installing another: nothing of it changes, the workflows go into a "Model Depot" folder of its workflow list, and the custom nodes it lacks are listed for installing with ComfyUI Manager. The setup asks where to install, where the model library goes and where Model Depot keeps its data. Model files are not included: put them into the `models` folder beside the setup, and Model Depot sorts them into the library on first start once you confirm.

Requirements: Windows 10 or 11, an NVIDIA RTX 20 series graphics card or later with driver 580 or later, and about 9 GB of free space plus room for the models. The setup is not digitally signed, so Windows may ask for confirmation the first time it runs.

### From source

Requirements: Python 3.10 or later with `aiohttp` and `Pillow` (`pip install -r requirements.txt`). The Python of a ComfyUI portable build already has both.

On Windows, double-click `Start.cmd` to start Model Depot in the background and open it in your browser, and `Stop.cmd` to stop it. On first start it looks for a suitable Python and asks before installing anything.

On any system:

```
python run.py            # start
python run.py --stop     # stop
python run.py --check    # check that this Python can run Model Depot
```

The page is at http://127.0.0.1:8194/. A setup wizard asks for the language, the model folders (detected from ComfyUI when present), the network connection (direct or through a proxy) and, optionally, a civitai API key, which is needed only for models whose creators require a login.

### Windows app

`desktop\app\ModelDepot.exe` shows Model Depot in a window of its own and starts it when it is not running. Closing the window keeps Model Depot running in the notification area; choose Exit from the icon's menu to stop it. It needs Windows 10 or 11 with the Microsoft Edge WebView2 Runtime, which both include.

## Network and privacy

- By default Model Depot listens on 127.0.0.1 only. For other devices to call the API, `server.host` must be changed and `remote_access` enabled with an access key of at least 8 characters, both in `config.json`.
- It contacts civitai.com, civitai.red and civitai's image hosts to identify models, read model pages and download files, at a deliberately slow pace with results cached. The civitai API key is sent only to civitai.com and civitai.red.
- When you choose to install the local language model from the settings, llama.cpp is downloaded from its GitHub releases and the model from Hugging Face. Nothing else is contacted, and nothing is reported anywhere.
- Model Depot applies no content screening. Hiding covers by rating is a display preference and can be turned off.

## Data

Everything Model Depot writes goes into one data folder: `data/` in the project, the folder named by the environment variable `MODEL_DEPOT_DATA`, or the one chosen during setup.

| Path | Contents |
|---|---|
| `config.json` | Settings |
| `library/` | One record and one cover per model, named by fingerprint; saved civitai model pages; copies of images you added. This is the part worth backing up, and it contains no local paths. |
| `index.sqlite` | The index, rebuilt from the model files and the records when needed |
| `cache/` | Small copies of the authors' example images; safe to delete |
| `logs/` | Logs |

Settings the page does not show are edited in `config.json`, followed by a restart: `server.host`, `server.port` (default 8194), `remote_access`, `hashing.workers`, and `prewarm` (fetch model pages and example images ahead of time after each scan; off by default).

## Documentation

- [docs/安装包对接说明.md](docs/安装包对接说明.md): where the Windows installer puts each part, and how another program finds them (in Chinese)

## Development

| Path | Contents |
|---|---|
| `depot/` | The server: scanning, fingerprints, civitai, downloads, cards, the API (Python, aiohttp) |
| `web/` | The page: native ES modules without a build step, fonts served locally |
| `desktop/` | The Windows app (C#, WinForms, WebView2, .NET Framework 4.8); the built files in `desktop/app/` are committed |
| `installer/` | The Windows installer: `build.py` stages and packs every part, `compile.py` builds the two executables, `setup/` is the setup program |
| `scripts/` | The Windows start and stop scripts |

The Windows app is built with `dotnet build desktop\ModelDepot.csproj -c Release`, or with `python installer\compile.py desktop` where only Visual Studio Build Tools are installed. Close the running app with `desktop\app\ModelDepot.exe --quit` before its files are replaced.

The setup is built with `python installer\build.py`; the paths of the building computer go into `installer\build.local.json`, whose shape is shown in `installer\build.example.json`.

## License

Copyright (C) 2026 ZEUS INSiGHTS

Model Depot is free software under the [GNU General Public License version 3](LICENSE). You may use, modify and redistribute it; a modified version that is distributed must be released under the same license, with its source code. Under section 7(e) of the license, no rights are granted to use the name "Model Depot", its wordmark or its icon.

Third-party components keep their own licenses, listed in [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md).

Model Depot is an independent project, not affiliated with Civitai or ComfyUI.
