# Third-Party Notices

Model Depot is licensed under the [GNU General Public License version 3](LICENSE). It is built on, and its Windows setup ships, the components below. Each remains under its own license; nothing here changes those terms.

第三方组件及其许可证。Model Depot 本身采用 GNU GPL 3.0；下列组件各自沿用其原有许可证。

## Included in this repository

| Component | Files | License |
|---|---|---|
| [Anaheim](https://github.com/googlefonts/anaheimFont), Copyright 2012 The Anaheim Project Authors | `web/fonts/Anaheim-*.woff2` | [SIL Open Font License 1.1](https://openfontlicense.org/open-font-license-official-text/), full text in `web/fonts/OFL-anaheim.txt` |
| [Chivo](https://github.com/Omnibus-Type/Chivo), Copyright 2019 The Chivo Project Authors | `web/fonts/Chivo-*.woff2` | [SIL Open Font License 1.1](https://openfontlicense.org/open-font-license-official-text/), full text in `web/fonts/OFL-chivo.txt` |
| [Microsoft Edge WebView2 SDK](https://www.nuget.org/packages/Microsoft.Web.WebView2/1.0.4258.31) 1.0.4258.31 | `desktop/app/Microsoft.Web.WebView2.Core.dll`, `Microsoft.Web.WebView2.WinForms.dll`, `WebView2Loader.dll`; also embedded in the setup | [BSD 3-Clause License](https://www.nuget.org/packages/Microsoft.Web.WebView2/1.0.4258.31/License), reproduced [below](#microsoft-edge-webview2-sdk) |

## Required at run time

Installed with `pip install -r requirements.txt`, and shipped in the Python of the Windows setup together with their license files (`python\Lib\site-packages\*.dist-info\licenses`).

| Component | License |
|---|---|
| [Python](https://www.python.org/) 3.14 (the embeddable package, in the Windows setup) | [PSF License Agreement](https://docs.python.org/3/license.html) |
| [aiohttp](https://github.com/aio-libs/aiohttp) 3.14, including [llhttp](https://github.com/nodejs/llhttp) | [Apache License 2.0](https://www.apache.org/licenses/LICENSE-2.0); llhttp: [MIT License](https://opensource.org/license/mit) |
| [aiohappyeyeballs](https://github.com/aio-libs/aiohappyeyeballs) | [PSF License 2.0](https://docs.python.org/3/license.html) |
| [aiosignal](https://github.com/aio-libs/aiosignal), [frozenlist](https://github.com/aio-libs/frozenlist), [multidict](https://github.com/aio-libs/multidict), [propcache](https://github.com/aio-libs/propcache), [yarl](https://github.com/aio-libs/yarl) | [Apache License 2.0](https://www.apache.org/licenses/LICENSE-2.0) |
| [attrs](https://github.com/python-attrs/attrs) | [MIT License](https://opensource.org/license/mit) |
| [idna](https://github.com/kjd/idna) | [BSD 3-Clause License](https://opensource.org/license/bsd-3-clause) |
| [Pillow](https://github.com/python-pillow/Pillow) 12 | [MIT-CMU License](https://github.com/python-pillow/Pillow/blob/main/LICENSE) |
| [PyYAML](https://github.com/yaml/pyyaml), optional: used when present, not shipped | [MIT License](https://opensource.org/license/mit) |

## Shipped with the Windows setup

These are separate programs installed beside Model Depot. Model Depot does not contain or link their code; it starts them and talks to them over local HTTP. Each keeps its license files in its own folder after installation.

| Component | License |
|---|---|
| [ComfyUI](https://github.com/Comfy-Org/ComfyUI) 0.42.5, portable build | [GNU General Public License 3.0](https://www.gnu.org/licenses/gpl-3.0.html) |
| [ComfyUI-Manager](https://github.com/Comfy-Org/ComfyUI-Manager) | [GNU General Public License 3.0](https://www.gnu.org/licenses/gpl-3.0.html) |
| [ComfyUI workflow templates](https://github.com/Comfy-Org/workflow_templates), the source of the default workflows | [MIT License](https://opensource.org/license/mit) |
| The Python packages of the portable ComfyUI, PyTorch among them | Their own licenses, in `ComfyUI\python_embeded\Lib\site-packages\*.dist-info` |
| [llama.cpp](https://github.com/ggml-org/llama.cpp) build 10327 (`llama-server` and its libraries), Copyright 2023-2026 The ggml authors | [MIT License](https://opensource.org/license/mit), full text in `llm\llama.cpp\LICENSE.txt` |
| NVIDIA CUDA runtime libraries shipped with llama.cpp (`cudart64_13.dll`, `cublas64_13.dll`, `cublasLt64_13.dll`) | [NVIDIA CUDA Toolkit End User License Agreement](https://docs.nvidia.com/cuda/eula/index.html), redistributable files |
| LLVM OpenMP runtime shipped with llama.cpp (`libomp140.x86_64.dll`) | Microsoft Visual C++ redistributable files, under the [Visual Studio license terms](https://visualstudio.microsoft.com/license-terms/) |
| Microsoft Visual C++ runtime (`msvcp140*.dll`, `vcruntime140*.dll`, `concrt140.dll`, `vcomp140.dll`), placed beside ComfyUI's Python, in PyTorch's `torch\lib` and beside `llama-server`, which cannot start without them | Microsoft Visual C++ redistributable files, under the [Visual Studio license terms](https://visualstudio.microsoft.com/license-terms/) |
| [7-Zip](https://www.7-zip.org/) 26.04 (`7za.exe`, used by the setup to unpack; not installed), Copyright 1999-2026 Igor Pavlov | [GNU LGPL 2.1](https://www.gnu.org/licenses/old-licenses/lgpl-2.1.html), with parts under the BSD 3-Clause and BSD 2-Clause licenses; full text in `setup\7-Zip License.txt` |
| [PixAI Tagger v1.0](https://huggingface.co/pixai-labs/pixai-tagger-v1.0), model weights and code | [Apache License 2.0](https://www.apache.org/licenses/LICENSE-2.0) |

When the model library is not in ComfyUI's own `models` folder, a new installation adds `--models-directory` to ComfyUI's `run_*.bat` launchers; an update leaves the launchers, ComfyUI's settings and `extra_model_paths.yaml` as they are. The setup adds a `Start ComfyUI.cmd` launcher that opens a running ComfyUI's page instead of starting a second one. In the copied ComfyUI, numba and llvmlite are upgraded (to 0.68.0 and 0.50.0), which accept NumPy 2.5, and the `gguf` package is added for ComfyUI-GGUF.

Custom nodes installed with ComfyUI (each for a reason: GGUF model files, the optional workflows, detail passes, preprocessing):

| Custom node | License |
|---|---|
| [ComfyUI-GGUF](https://github.com/city96/ComfyUI-GGUF) | [Apache License 2.0](https://www.apache.org/licenses/LICENSE-2.0) |
| [ComfyUI-Impact-Pack](https://github.com/ltdrdata/ComfyUI-Impact-Pack) | [GNU General Public License 3.0](https://www.gnu.org/licenses/gpl-3.0.html) |
| [ComfyUI-Impact-Subpack](https://github.com/ltdrdata/ComfyUI-Impact-Subpack) | [GNU Affero General Public License 3.0](https://www.gnu.org/licenses/agpl-3.0.html) |
| ComfyUI-PixAI-Tagger, Copyright 2026 rieslin | [MIT License](https://opensource.org/license/mit) |
| [comfyui_controlnet_aux](https://github.com/Fannovel16/comfyui_controlnet_aux) | [Apache License 2.0](https://www.apache.org/licenses/LICENSE-2.0) |

The optional workflows (`installer/workflows/optional`) are part of Model Depot and fall under its GNU General Public License version 3.

## License texts

### Microsoft Edge WebView2 SDK

```
Copyright (C) Microsoft Corporation. All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are
met:

   * Redistributions of source code must retain the above copyright
notice, this list of conditions and the following disclaimer.
   * Redistributions in binary form must reproduce the above
copyright notice, this list of conditions and the following disclaimer
in the documentation and/or other materials provided with the
distribution.
   * The name of Microsoft Corporation, or the names of its contributors
may not be used to endorse or promote products derived from this
software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
"AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR
A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT
OWNER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,
SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT
LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE,
DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY
THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
(INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
```

The WebView2 SDK package carries its own third-party notices, available from Microsoft at [3rdpartysource.microsoft.com](https://3rdpartysource.microsoft.com).
