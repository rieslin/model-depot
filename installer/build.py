"""Build the Windows installer: gather everything into one staging folder laid out as it will be installed, pack each
part into a 7-Zip archive, and put the setup beside them.

    python installer\\build.py                  stage every part, then package
    python installer\\build.py --only comfyui   stage one part again (app, python, llm, comfyui, workflows, setup)
    python installer\\build.py --package        package what is staged, without staging

The paths of this computer (the ComfyUI to copy, llama.cpp, the embeddable Python, 7-Zip, the C# compiler, where to
stage and where to put the result) are read from installer\\build.local.json, which is not in the repository;
build.example.json shows its shape.

The staging folder, as installed:
    app\\                Model Depot itself (the tracked files it runs from)
    python\\             the embeddable Python, with aiohttp and Pillow
    llm\\                llama.cpp's server, for the local language model (installed as llm\\llama.cpp)
    ComfyUI\\            the portable ComfyUI with its custom nodes, without models, outputs, logs or caches
    library\\            what goes into the model library: a folder per model type, ComfyUI's model configs, and the
                        models that ship with the package (the PixAI tagger)
    workflows\\          the default workflows (official templates), and the optional ones in their own folder
    ui\\                 the setup's page, with Model Depot's own app.css and fonts

The package (the output folder):
    Model Depot Setup.exe      the setup (installer\\setup)
    setup\\manifest.json       the parts: name, archive, where it goes, size unpacked, number of files
    setup\\<part>.7z           one archive per part
    setup\\7za.exe             7-Zip's standalone program, which the setup unpacks with
    setup\\ui\\                the setup's page
    models\\                   empty: model files put here are sorted into the library when Model Depot first runs
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
PARTS = ("app", "python", "llm", "comfyui", "workflows", "setup")
# the archives, in the order they are unpacked: (part, staged folder, where it goes)
ARCHIVES = (("app", "app", "app"), ("python", "python", "python"), ("llm", "llm", "llm"), ("comfyui", "ComfyUI", "comfyui"),
            ("workflows", "workflows", "workflows"), ("library", "library", "library"))

# What of the repository the installed program needs.
APP_PREFIXES = ("depot/", "web/", "scripts/", "desktop/app/")
APP_FILES = ("run.py", "requirements.txt", "README.md", "README.zh-CN.md", "LICENSE", "THIRD-PARTY-NOTICES.md",
             "desktop/loader.html", "Start.cmd", "Stop.cmd", "启动.cmd", "停止.cmd")
# The license files that go beside the setup in the package.
PACKAGE_LICENSES = ("LICENSE", "THIRD-PARTY-NOTICES.md")

MODEL_EXTENSIONS = {".ckpt", ".pt", ".pt2", ".bin", ".pth", ".safetensors", ".pkl", ".sft", ".gguf", ".onnx"}

# The official ComfyUI templates installed as the default workflows, with the name each gets in ComfyUI's workflow
# list.
OFFICIAL_WORKFLOWS = (
    ("image_z_image_turbo.json", "Z-Image Turbo - Text to Image.json"),
    ("image_z_image.json", "Z-Image Base - Text to Image.json"),
    ("image_krea2_turbo_t2i.json", "Krea 2 Turbo - Text to Image.json"),
    ("image_krea2_turbo_t2i_int8.json", "Krea 2 Turbo INT8 - Text to Image.json"),
    ("image_qwen_image_2_1_t2i.json", "Qwen Image 2.1 - Text to Image.json"),
    ("image_qwen_image_2_1_image_edit.json", "Qwen Image 2.1 - Image Edit.json"),
    ("image_anima_base_v1.json", "Anima Base v1 - Text to Image.json"),
)
TEMPLATES = r"python_embeded\Lib\site-packages\comfyui_workflow_templates_json\templates"
# the optional workflows (installer\workflows\optional) go into this folder of ComfyUI's workflow list
OPTIONAL_FOLDER = "可选工作流"


def load_settings():
    path = os.path.join(HERE, "build.local.json")
    if not os.path.isfile(path):
        sys.exit("installer\\build.local.json is missing; copy build.example.json and fill in this computer's paths")
    with open(path, encoding="utf-8-sig") as f:
        s = json.load(f)
    for key in ("stage", "output", "comfyui", "llama_cpp", "python_embed_zip", "site_packages", "sevenzip", "csc"):
        if not s.get(key) or not os.path.isabs(s[key]):
            sys.exit("build.local.json: %s must be an absolute path" % key)
    s.setdefault("bundled_models", [])
    s.setdefault("optional_workflows", os.path.join(HERE, "workflows", "optional"))
    return s


def say(text):
    print(text, flush=True)


def fresh(path):
    if os.path.isdir(path):
        shutil.rmtree(path)
    os.makedirs(path)


def robocopy(src, dst, exclude_dirs=(), exclude_files=()):
    """Mirror src into dst (only what changed is copied again). Robocopy's codes below 8 mean success."""
    cmd = ["robocopy", src, dst, "/MIR", "/MT:16", "/R:2", "/W:2", "/NFL", "/NDL", "/NJH", "/NP"]
    if exclude_dirs:
        cmd += ["/XD"] + list(exclude_dirs)
    if exclude_files:
        cmd += ["/XF"] + list(exclude_files)
    code = subprocess.run(cmd).returncode
    if code >= 8:
        sys.exit("robocopy %s failed with code %d" % (src, code))


# ---- the parts ---------------------------------------------------------------------------------------------------
def stage_app(s):
    dst = os.path.join(s["stage"], "app")
    fresh(dst)
    listed = subprocess.run(["git", "-C", REPO, "ls-files", "-z"], capture_output=True, check=True).stdout
    names = [n.decode("utf-8") for n in listed.split(b"\0") if n]
    count = 0
    for name in names:
        if not (name.startswith(APP_PREFIXES) or name in APP_FILES):
            continue
        target = os.path.join(dst, *name.split("/"))
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.copy2(os.path.join(REPO, *name.split("/")), target)
        count += 1
    say("app: %d files" % count)


def stage_python(s):
    dst = os.path.join(s["stage"], "python")
    fresh(dst)
    with zipfile.ZipFile(s["python_embed_zip"]) as z:
        z.extractall(dst)
    pth = [n for n in os.listdir(dst) if n.endswith("._pth")]
    if len(pth) != 1:
        sys.exit("python: expected one ._pth file in the embeddable Python")
    # The embeddable Python looks only where its ._pth file says: add site-packages and let site run.
    with open(os.path.join(dst, pth[0]), "w", encoding="ascii") as f:
        f.write("%s.zip\n.\nLib\\site-packages\nimport site\n" % pth[0][:-5])
    site = os.path.join(dst, "Lib", "site-packages")
    shutil.copytree(s["site_packages"], site,
                    ignore=shutil.ignore_patterns("__pycache__", "pip", "pip-*", "_distutils_hack",
                                                  "distutils-precedence.pth", "setuptools*", "pkg_resources"))
    check = subprocess.run([os.path.join(dst, "python.exe"), os.path.join(REPO, "run.py"), "--check"],
                           capture_output=True, text=True)
    if check.returncode != 0:
        sys.exit("python: run.py --check failed:\n%s%s" % (check.stdout, check.stderr))
    say("python: " + check.stdout.strip().replace("\n", "; "))


def stage_llm(s):
    dst = os.path.join(s["stage"], "llm")
    fresh(dst)
    count = 0
    for name in os.listdir(s["llama_cpp"]):
        src = os.path.join(s["llama_cpp"], name)
        if not os.path.isfile(src):
            continue
        # the server and every library; llama.cpp's other programs are not needed
        if name.lower().endswith(".dll") or name.lower() in ("llama-server.exe", "license", "license.txt"):
            shutil.copy2(src, os.path.join(dst, name))
            count += 1
    if not os.path.isfile(os.path.join(dst, "llama-server.exe")):
        sys.exit("llm: llama-server.exe is not in %s" % s["llama_cpp"])
    # llama.cpp's release archives carry no license file; its MIT license must travel with the binaries
    if not any(n.lower() in ("license", "license.txt") for n in os.listdir(dst)):
        shutil.copy2(os.path.join(HERE, "licenses", "llama.cpp LICENSE.txt"), os.path.join(dst, "LICENSE.txt"))
        count += 1
    version = subprocess.run([os.path.join(dst, "llama-server.exe"), "--version"], capture_output=True, text=True)
    say("llm: %d files, %s" % (count, (version.stdout + version.stderr).strip().splitlines()[0]))


def stage_comfyui(s):
    src = s["comfyui"]
    dst = os.path.join(s["stage"], "ComfyUI")
    app = os.path.join(src, "ComfyUI")
    # Everything but the user's own files: models (the library part below brings what ships), outputs, inputs,
    # the temporary folder, logs, caches, disabled custom nodes, and the settings files that hold this computer's
    # paths (the custom nodes write them again on their first start; the setup writes extra_model_paths.yaml).
    exclude_dirs = [os.path.join(app, d) for d in ("models", "input", "output", "temp", "user")]
    exclude_dirs += [os.path.join(app, "custom_nodes", ".disabled"), os.path.join(src, ".app_activity"),
                     "before-update-*", "__pycache__"]
    exclude_files = ["*.pyc", "*.log", os.path.join(app, "extra_model_paths.yaml"),
                     os.path.join(app, "custom_nodes", "was-node-suite-comfyui", "was_suite_config.json"),
                     os.path.join(app, "custom_nodes", "ComfyUI-Impact-Pack", "impact-pack.ini")]
    robocopy(src, dst, exclude_dirs, exclude_files)
    for name in ("input", "output", "temp", "models"):
        os.makedirs(os.path.join(dst, "ComfyUI", name), exist_ok=True)

    # Packages replaced in the copy only ("comfyui_pip" in the settings): numba 0.65 does not accept NumPy 2.5, which
    # keeps was-node-suite from loading; numba 0.68 with llvmlite 0.50 does.
    upgrades = s.get("comfyui_pip") or []
    if upgrades:
        cmd = [os.path.join(dst, "python_embeded", "python.exe"), "-m", "pip", "install", "--no-deps", "--no-compile",
               "--disable-pip-version-check", "--no-warn-script-location"] + upgrades
        if s.get("proxy"):
            cmd += ["--proxy", s["proxy"]]
        subprocess.run(cmd, check=True)
    # compiled caches left in the copy (a test run of it, an import to check it) are not shipped
    for current, dirs, _names in os.walk(dst):
        if "__pycache__" in dirs:
            shutil.rmtree(os.path.join(current, "__pycache__"), ignore_errors=True)
            dirs.remove("__pycache__")

    # The user folder: ComfyUI's settings and the custom nodes' own settings, without logs, the history database,
    # the manager's caches and snapshots, or saved workflows (the workflows part brings those).
    user_src, user_dst = os.path.join(app, "user"), os.path.join(dst, "ComfyUI", "user")
    fresh(user_dst)
    keep = (r"default\comfy.settings.json", r"default\ComfyUI-Impact-Subpack\model-whitelist.txt",
            r"__manager\config.ini", r"__manager\channels.list")
    for rel in keep:
        if os.path.isfile(os.path.join(user_src, rel)):
            os.makedirs(os.path.dirname(os.path.join(user_dst, rel)), exist_ok=True)
            shutil.copy2(os.path.join(user_src, rel), os.path.join(user_dst, rel))

    # The library: one folder per model type, ComfyUI's model configs, and the models that ship with the package.
    lib_src, lib_dst = os.path.join(app, "models"), os.path.join(s["stage"], "library")
    fresh(lib_dst)
    types = sorted(d for d in os.listdir(lib_src) if os.path.isdir(os.path.join(lib_src, d)))
    for t in types:
        os.makedirs(os.path.join(lib_dst, t))
        for name in os.listdir(os.path.join(lib_src, t)):
            path = os.path.join(lib_src, t, name)
            small = os.path.isfile(path) and os.path.getsize(path) < (1 << 20)
            if small and os.path.splitext(name)[1].lower() not in MODEL_EXTENSIONS:
                shutil.copy2(path, os.path.join(lib_dst, t, name))       # configs, "put ... here" notes
        if t == "configs":
            shutil.copytree(os.path.join(lib_src, t), os.path.join(lib_dst, t), dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns(*["*" + e for e in MODEL_EXTENSIONS]))
    for rel in s["bundled_models"]:
        source = os.path.join(lib_src, *rel.split("/"))
        if not os.path.isdir(source):
            sys.exit("comfyui: bundled model %s is not in %s" % (rel, lib_src))
        shutil.copytree(source, os.path.join(lib_dst, *rel.split("/")), dirs_exist_ok=True)
    say("comfyui: copied; library has %d type folders and %d bundled models" % (len(types), len(s["bundled_models"])))


def stage_workflows(s):
    dst = os.path.join(s["stage"], "workflows")
    fresh(dst)
    templates = os.path.join(s["comfyui"], TEMPLATES)
    for src_name, name in OFFICIAL_WORKFLOWS:
        src = os.path.join(templates, src_name)
        if not os.path.isfile(src):
            sys.exit("workflows: the template %s is not in this ComfyUI" % src_name)
        shutil.copy2(src, os.path.join(dst, name))
    extra_src = s["optional_workflows"]
    extra = sorted(n for n in os.listdir(extra_src) if n.endswith(".json")) if os.path.isdir(extra_src) else []
    if extra:
        os.makedirs(os.path.join(dst, OPTIONAL_FOLDER))
        for name in extra:
            shutil.copy2(os.path.join(extra_src, name), os.path.join(dst, OPTIONAL_FOLDER, name))
    say("workflows: %d official, %d optional" % (len(OFFICIAL_WORKFLOWS), len(extra)))


def stage_setup(s):
    """The setup's page with Model Depot's own style sheet and fonts, and the setup itself, compiled."""
    dst = os.path.join(s["stage"], "ui")
    fresh(dst)
    shutil.copytree(os.path.join(HERE, "setup", "ui"), dst, dirs_exist_ok=True)
    shutil.copy2(os.path.join(REPO, "web", "app.css"), os.path.join(dst, "app.css"))
    shutil.copytree(os.path.join(REPO, "web", "fonts"), os.path.join(dst, "fonts"))
    subprocess.run([sys.executable, os.path.join(HERE, "compile.py"), "setup"], check=True)
    say("setup: page and program ready")


# ---- the package -----------------------------------------------------------------------------------------------------
def version():
    with open(os.path.join(REPO, "depot", "__init__.py"), encoding="utf-8") as f:
        return re.search(r'__version__ = "([^"]+)"', f.read()).group(1)


def comfyui_version(s):
    with open(os.path.join(s["comfyui"], "ComfyUI", "comfyui_version.py"), encoding="utf-8") as f:
        return re.search(r'__version__ = "([^"]+)"', f.read()).group(1)


def tree_size(folder):
    """(bytes, files, latest change in nanoseconds) of everything under a folder."""
    total = files = latest = 0
    for current, _dirs, names in os.walk(folder):
        for name in names:
            st = os.stat(os.path.join(current, name))
            total += st.st_size
            files += 1
            latest = max(latest, st.st_mtime_ns)
    return total, files, latest


def package(s):
    out = s["output"]
    setup_dir = os.path.join(out, "setup")
    os.makedirs(setup_dir, exist_ok=True)
    seven = s["sevenzip"]
    parts = []
    for name, staged, target in ARCHIVES:
        folder = os.path.join(s["stage"], staged)
        size, files, latest = tree_size(folder)
        archive = os.path.join(setup_dir, name + ".7z")
        stamp = os.path.join(s["stage"], name + ".packed")
        # an archive is made again only when its part changed: its size, number of files or latest change
        key = "%d %d %d" % (size, files, latest)
        if not (os.path.isfile(archive) and os.path.isfile(stamp) and open(stamp).read() == key):
            if os.path.isfile(archive):
                os.remove(archive)
            say("packing %s (%.2f GB, %d files) ..." % (name, size / 1e9, files))
            subprocess.run([seven, "a", "-t7z", "-m0=lzma2", "-mx=7", "-mmt=on", "-bso0", "-bsp0", archive,
                            os.path.join(folder, "*")], check=True)
            with open(stamp, "w") as f:
                f.write(key)
        parts.append({"name": name, "file": name + ".7z", "target": target, "bytes": size, "files": files,
                      "packed": os.path.getsize(archive)})
    nodes = os.path.join(s["stage"], "ComfyUI", "ComfyUI", "custom_nodes")
    workflows = os.path.join(s["stage"], "workflows")
    optional = os.path.join(workflows, OPTIONAL_FOLDER)
    manifest = {
        "version": version(), "comfyui": comfyui_version(s), "parts": parts,
        "counts": {
            "custom_nodes": sum(1 for d in os.listdir(nodes) if os.path.isdir(os.path.join(nodes, d)) and not d.startswith((".", "__"))),
            "official": sum(1 for n in os.listdir(workflows) if n.endswith(".json")),
            "optional": sum(1 for n in os.listdir(optional) if n.endswith(".json")) if os.path.isdir(optional) else 0,
        },
    }
    with open(os.path.join(setup_dir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)
    shutil.copy2(seven, os.path.join(setup_dir, "7za.exe"))
    licence = os.path.join(os.path.dirname(os.path.dirname(seven)), "License.txt")
    if os.path.isfile(licence):
        shutil.copy2(licence, os.path.join(setup_dir, "7-Zip License.txt"))
    if os.path.isdir(os.path.join(setup_dir, "ui")):
        shutil.rmtree(os.path.join(setup_dir, "ui"))
    shutil.copytree(os.path.join(s["stage"], "ui"), os.path.join(setup_dir, "ui"))
    shutil.copy2(os.path.join(HERE, "setup", "bin", "ModelDepotSetup.exe"), os.path.join(out, "Model Depot Setup.exe"))
    os.makedirs(os.path.join(out, "models"), exist_ok=True)
    for name in os.listdir(os.path.join(HERE, "package")):
        shutil.copy2(os.path.join(HERE, "package", name), os.path.join(out, name))
    for name in PACKAGE_LICENSES:
        shutil.copy2(os.path.join(REPO, name), os.path.join(out, name))
    total = 0
    for current, _dirs, names in os.walk(out):
        total += sum(os.path.getsize(os.path.join(current, n)) for n in names)
    say("done: %s (%.2f GB; %.2f GB once installed)" % (out, total / 1e9, sum(p["bytes"] for p in parts) / 1e9))


def main():
    parser = argparse.ArgumentParser(description="Build the Model Depot installer")
    parser.add_argument("--only", choices=PARTS, action="append", help="stage only this part (repeatable)")
    parser.add_argument("--package", action="store_true", help="package what is staged, without staging")
    args = parser.parse_args()
    s = load_settings()
    if not args.package:
        for part in args.only or PARTS:
            globals()["stage_" + part](s)
    if args.package or not args.only:
        package(s)


if __name__ == "__main__":
    main()
