"""Compile the two Windows programs with the C# compiler of Visual Studio's Build Tools, for a computer that has those
but no .NET SDK (the SDK build is `dotnet build desktop\\ModelDepot.csproj -c Release`; both give the same program):

    python installer\\compile.py desktop      the window around the page: desktop\\app\\ModelDepot.exe
    python installer\\compile.py setup        the setup: installer\\setup\\bin\\ModelDepotSetup.exe

The compiler's path is "csc" in installer\\build.local.json. Both target .NET Framework 4.8, which Windows 10 and 11
have, and reference the framework's own assemblies and the two WebView2 assemblies kept in desktop\\app.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
DESKTOP = os.path.join(REPO, "desktop")
WEBVIEW = [os.path.join(DESKTOP, "app", n) for n in ("Microsoft.Web.WebView2.Core.dll", "Microsoft.Web.WebView2.WinForms.dll")]
FRAMEWORK = ["System.dll", "System.Core.dll", "System.Drawing.dll", "System.Windows.Forms.dll", "System.Net.Http.dll",
             "System.Web.Extensions.dll", "Microsoft.CSharp.dll", "System.IO.Compression.dll",
             "System.IO.Compression.FileSystem.dll"]


def settings():
    with open(os.path.join(HERE, "build.local.json"), encoding="utf-8-sig") as f:
        s = json.load(f)
    if not s.get("csc") or not os.path.isfile(s["csc"]):
        sys.exit("build.local.json: csc must name the C# compiler (Build Tools: MSBuild\\Current\\Bin\\Roslyn\\csc.exe)")
    return s


def version():
    """The program's version, from depot/__init__.py: the exes carry the same one."""
    with open(os.path.join(REPO, "depot", "__init__.py"), encoding="utf-8") as f:
        return re.search(r'__version__ = "([^"]+)"', f.read()).group(1)


def compile_exe(csc, sources, out, icon, manifest, resources=(), references=(), title=""):
    """One WinForms exe for .NET Framework 4.8, x64, carrying the program's version."""
    v = version()
    info = os.path.join(tempfile.mkdtemp(), "AssemblyInfo.cs")
    with open(info, "w", encoding="utf-8") as f:
        f.write('[assembly: System.Reflection.AssemblyTitle("%s")]\n[assembly: System.Reflection.AssemblyProduct("Model Depot")]\n'
                '[assembly: System.Reflection.AssemblyVersion("%s.0")]\n[assembly: System.Reflection.AssemblyFileVersion("%s.0")]\n'
                '[assembly: System.Reflection.AssemblyInformationalVersion("%s")]\n' % (title, v, v, v))
    os.makedirs(os.path.dirname(out), exist_ok=True)
    # the manifest's own version follows too
    with open(manifest, encoding="utf-8") as f:
        text = f.read()
    text = re.sub(r'(<assemblyIdentity version=")[0-9.]+(")', r"\g<1>%s.0\g<2>" % v, text, count=1)
    stamped = os.path.join(os.path.dirname(info), "app.manifest")
    with open(stamped, "w", encoding="utf-8") as f:
        f.write(text)
    cmd = [csc, "/nologo", "/target:winexe", "/platform:x64", "/langversion:latest", "/optimize+", "/debug-",
           "/out:" + out, "/win32icon:" + icon, "/win32manifest:" + stamped]
    cmd += ["/r:" + r for r in FRAMEWORK + list(references)]
    cmd += ["/resource:%s,%s" % (path, name) for path, name in resources]
    cmd += list(sources) + [info]
    result = subprocess.run(cmd, capture_output=True, text=True)
    shutil.rmtree(os.path.dirname(info), ignore_errors=True)
    if result.returncode == 0:
        print("version %s" % v)
    if result.returncode != 0:
        sys.exit("csc failed:\n" + result.stdout + result.stderr)
    print("built " + out)


def desktop(s):
    sources = sorted(os.path.join(DESKTOP, n) for n in os.listdir(DESKTOP) if n.endswith(".cs"))
    out = os.path.join(DESKTOP, "app", "ModelDepot.exe")
    compile_exe(s["csc"], sources, out, os.path.join(DESKTOP, "ModelDepot.ico"), os.path.join(DESKTOP, "app.manifest"),
                resources=[(os.path.join(DESKTOP, "ModelDepot.ico"), "ModelDepot.ico")], references=WEBVIEW,
                title="Model Depot")
    shutil.copyfile(os.path.join(DESKTOP, "App.config"), out + ".config")


def setup(s):
    src = os.path.join(HERE, "setup")
    sources = sorted(os.path.join(src, n) for n in os.listdir(src) if n.endswith(".cs"))
    # the two WebView2 assemblies and the native loader travel inside the exe (Program.cs unpacks them)
    resources = [(os.path.join(DESKTOP, "ModelDepot.ico"), "ModelDepot.ico")]
    resources += [(p, os.path.basename(p)) for p in WEBVIEW + [os.path.join(DESKTOP, "app", "WebView2Loader.dll")]]
    compile_exe(s["csc"], sources, os.path.join(src, "bin", "ModelDepotSetup.exe"), os.path.join(DESKTOP, "ModelDepot.ico"),
                os.path.join(src, "app.manifest"), resources=resources, references=WEBVIEW, title="Model Depot Setup")


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in ("desktop", "setup"):
        sys.exit(__doc__)
    {"desktop": desktop, "setup": setup}[sys.argv[1]](settings())
