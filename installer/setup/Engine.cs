// What the setup does, behind its page.
//
// The package folder:
//   Model Depot Setup.exe
//   setup\manifest.json     the parts, written by installer\build.py: [{name, file, target, bytes, files}]
//   setup\*.7z              one 7-Zip archive per part
//   setup\7za.exe           7-Zip's standalone program, to unpack them
//   setup\ui\               the setup's page
//   models\                 model files to sort into the library once Model Depot runs
//
// Installed (docs\安装包对接说明.md): <install>\app, python, llm\llama.cpp, ComfyUI, models, data,
// uninstall; the model library where the user chose. With a portable ComfyUI already on the computer, that one is used
// instead of the bundled copy: nothing of it changes but the workflows added in a "Model Depot" folder of its workflow
// list and the bundled models its library lacks. The places are kept in <install>\app\install.json and under
// HKCU\Software\Model Depot; Windows's list of programs finds the uninstaller under HKCU\...\Uninstall\ModelDepot.
// Everything is for the user who runs the setup: no administrator rights.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Web.Script.Serialization;
using Microsoft.Win32;

namespace ModelDepotSetup
{
    sealed class Engine
    {
        const string RegKey = @"Software\Model Depot";
        const string UninstallKey = @"Software\Microsoft\Windows\CurrentVersion\Uninstall\ModelDepot";
        const int MinDriver = 580;      // PyTorch cu130 and llama.cpp's CUDA 13 build both need it
        static readonly string[] ModelExtensions = { ".safetensors", ".sft", ".ckpt", ".pt", ".pt2", ".pth", ".bin", ".pkl", ".gguf", ".onnx" };
        // what removing the program leaves: the user's models, pictures, inputs and saved workflows (and, in
        // custom_nodes, the custom nodes other programs or the user put there: only the setup's own are removed)
        static readonly string[] ComfyKept = { "models", "user", "output", "input", "custom_nodes" };
        // The custom nodes that 0.6.0 and 0.6.1 shipped, for an installation that did not note its own.
        static readonly string[] OldOwnNodes = { "ComfyUI-Crystools", "ComfyUI-Impact-Pack", "ComfyUI-Impact-Subpack", "ComfyUI-KJNodes",
            "ComfyUI-Krea2-Ostris-Edit", "ComfyUI-PixAI-Tagger", "comfyui_controlnet_aux", "ComfyUI_essentials", "ComfyUI_UltimateSDUpscale",
            "rgthree-comfy", "seedvr2_videoupscaler", "was-node-suite-comfyui" };
        // the folder for model files to sort, next to the package and in the installation; named so that nobody takes
        // it for ComfyUI's own models folder (the review of 2026-10-20); installations before 0.6.2 used "models"
        public const string InboxName = "Model Inbox";

        readonly string exeDir;
        readonly JavaScriptSerializer json = new JavaScriptSerializer { MaxJsonLength = 16 << 20 };
        Dictionary<string, object> manifest;

        public Engine(string exeDir) { this.exeDir = exeDir; }

        string Payload => Path.Combine(exeDir, "setup");

        public string UiFolder
        {
            get
            {
                foreach (var dir in new[] { Path.Combine(exeDir, "setup", "ui"), Path.Combine(exeDir, "ui") })
                    if (File.Exists(Path.Combine(dir, "index.html"))) return dir;
                return null;
            }
        }

        static string Lang => CultureInfo.CurrentUICulture.Name.StartsWith("zh", StringComparison.OrdinalIgnoreCase) ? "zh" : "en";

        Dictionary<string, object> Manifest
        {
            get
            {
                if (manifest == null)
                {
                    var path = Path.Combine(Payload, "manifest.json");
                    if (!File.Exists(path)) throw new SetupProblem("no_payload", Payload);
                    manifest = json.Deserialize<Dictionary<string, object>>(File.ReadAllText(path, Encoding.UTF8));
                }
                return manifest;
            }
        }

        List<Dictionary<string, object>> Parts => ((System.Collections.ArrayList)Manifest["parts"]).Cast<Dictionary<string, object>>().ToList();

        static long Long(object v) => Convert.ToInt64(v, CultureInfo.InvariantCulture);

        long BytesOf(Func<string, bool> which) => Parts.Where(p => which((string)p["target"])).Sum(p => Long(p["bytes"]));

        // ---- what the page shows first ---------------------------------------------------------------------------------
        public object Info()
        {
            var parts = Parts.Select(p => new Dictionary<string, object> { { "name", p["name"] }, { "bytes", p["bytes"] } }).ToList();
            var existing = Existing();
            var defaults = existing ?? Defaults(BestDrive());
            return new Dictionary<string, object>
            {
                { "mode", "install" }, { "lang", Lang }, { "version", Manifest["version"] }, { "comfyui", Manifest["comfyui"] },
                { "parts", parts }, { "bytes", BytesOf(t => true) }, { "defaults", defaults }, { "gpu", Graphics() },
                { "inbox", Inbox(new[] { Path.Combine(exeDir, InboxName), Path.Combine(exeDir, "models") }.FirstOrDefault(Directory.Exists) ?? Path.Combine(exeDir, InboxName)) },
                { "upgrade", existing != null },
                { "comfy", new Dictionary<string, object> { { "found", FindComfy(existing) }, { "own", existing == null || OwnComfy(existing) } } },
            };
        }

        static Dictionary<string, object> Defaults(string install) => new Dictionary<string, object>
        {
            { "install", install }, { "library", Path.Combine(install, "ComfyUI", "ComfyUI", "models") }, { "data", Path.Combine(install, "data") },
        };

        // The fixed drive with the most room: the models want it.
        static string BestDrive()
        {
            var best = DriveInfo.GetDrives().Where(d => d.DriveType == DriveType.Fixed && d.IsReady)
                .OrderByDescending(d => d.AvailableFreeSpace).FirstOrDefault();
            return Path.Combine(best?.RootDirectory.FullName ?? @"C:\", "Model Depot");
        }

        // An installation already on this computer: installing again goes to the same places (its data is kept).
        Dictionary<string, object> Existing()
        {
            using (var key = Registry.CurrentUser.OpenSubKey(RegKey))
            {
                var install = key?.GetValue("InstallDir") as string;
                if (string.IsNullOrEmpty(install) || !Directory.Exists(install)) return null;
                return new Dictionary<string, object>
                {
                    { "install", install },
                    { "library", key.GetValue("LibraryDir") as string ?? Path.Combine(install, "ComfyUI", "ComfyUI", "models") },
                    { "data", key.GetValue("DataDir") as string ?? Path.Combine(install, "data") },
                    { "comfy", key.GetValue("ComfyUIDir") as string ?? Path.Combine(install, "ComfyUI") },
                };
            }
        }

        static string Norm(string path)
        {
            try { return Path.GetFullPath(path).TrimEnd('\\'); }
            catch { return path ?? ""; }
        }

        static bool OwnComfy(Dictionary<string, object> existing) =>
            string.Equals(Norm((string)existing["comfy"]), Norm(Path.Combine((string)existing["install"], "ComfyUI")), StringComparison.OrdinalIgnoreCase);

        // ---- a ComfyUI already on the computer ---------------------------------------------------------------------------
        // A portable ComfyUI: <root>\ComfyUI\folder_paths.py (what Model Depot itself looks for, depot\comfy.py) beside
        // <root>\python_embeded\python.exe. Its ComfyUI folder, picked by hand, stands for the same one.
        static string PortableRoot(string folder)
        {
            try
            {
                folder = Path.GetFullPath((folder ?? "").Trim().Trim('"')).TrimEnd('\\');
                foreach (var root in new[] { folder, Path.GetDirectoryName(folder) })
                    if (!string.IsNullOrEmpty(root) && File.Exists(Path.Combine(root, "ComfyUI", "folder_paths.py")) &&
                        File.Exists(Path.Combine(root, "python_embeded", "python.exe")))
                        return root;
            }
            catch { }
            return null;
        }

        // The custom nodes the package's ComfyUI carries, by folder name (written by installer\build.py).
        List<string> NodeNames => Manifest.TryGetValue("custom_node_names", out var v) && v is System.Collections.ArrayList list
            ? list.Cast<object>().Select(o => o.ToString()).ToList() : new List<string>();

        // The version of each custom node in the package (its pyproject.toml), written by installer\build.py.
        Dictionary<string, object> NodeVersions => Manifest.TryGetValue("custom_node_versions", out var v) && v is Dictionary<string, object> d
            ? d : new Dictionary<string, object>();

        // The fingerprints of the workflow files 0.6.0 and 0.6.1 wrote, which those installations did not record
        // (installer\workflows\shipped-before.json, carried by the manifest).
        bool EarlierWorkflow(string rel, string hash) =>
            Manifest.TryGetValue("earlier_workflow_files", out var v) && v is Dictionary<string, object> d
            && d.TryGetValue(rel.Replace('\\', '/'), out var list) && list is System.Collections.ArrayList hashes
            && hashes.Cast<object>().Any(h => h as string == hash);

        static string NodeVersion(string folder)
        {
            try
            {
                var m = Regex.Match(File.ReadAllText(Path.Combine(folder, "pyproject.toml")), "(?m)^version\\s*=\\s*[\"']([^\"']+)");
                return m.Success ? m.Groups[1].Value : "";
            }
            catch { return ""; }
        }

        // Custom nodes installed by hand keep their repository's name; ComfyUI Manager's registry writes it in lower case
        // and may drop the "ComfyUI" in it: compared without either.
        static string NodeKey(string name) =>
            Regex.Replace(Regex.Replace(name.ToLowerInvariant(), "comfyui", ""), "[^a-z0-9]", "");

        // Where that ComfyUI reads its models: --models-directory (or --base-directory) in its launcher, else its own folder.
        static string ModelsOf(string root)
        {
            var scripts = new[] { "run_nvidia_gpu.bat", "run_nvidia_gpu_fast_fp16_accumulation.bat", "run_cpu.bat" }
                .Select(n => Path.Combine(root, n)).Where(File.Exists).Concat(Directory.GetFiles(root, "*.bat"));
            foreach (var bat in scripts.Distinct(StringComparer.OrdinalIgnoreCase))
            {
                string text;
                try { text = File.ReadAllText(bat, Encoding.Default); } catch { continue; }
                if (!text.Contains("main.py")) continue;
                var m = Regex.Match(text, "--(models|base)-directory(?:=|\\s+)(?:\"([^\"]+)\"|(\\S+))");
                if (!m.Success) continue;
                var dir = m.Groups[2].Success ? m.Groups[2].Value : m.Groups[3].Value;
                if (!Path.IsPathRooted(dir)) dir = Path.Combine(root, dir);
                return m.Groups[1].Value == "base" ? Path.Combine(dir, "models") : dir;
            }
            return Path.Combine(root, "ComfyUI", "models");
        }

        // Numbers are compared as digit strings: a part may be longer than any integer type (rgthree-comfy's
        // "1.0.2605082257" overflowed the int this once read into, and the update stopped).
        static bool Older(string version, string than)
        {
            string[] Parse(string s) => Regex.Matches(s ?? "", "\\d+").Cast<Match>().Select(m => m.Value.TrimStart('0')).ToArray();
            var a = Parse(version);
            var b = Parse(than);
            if (a.Length == 0 || b.Length == 0) return false;
            for (int i = 0; i < Math.Max(a.Length, b.Length); i++)
            {
                string x = i < a.Length ? a[i] : "", y = i < b.Length ? b[i] : "";
                int c = x.Length != y.Length ? x.Length.CompareTo(y.Length) : string.CompareOrdinal(x, y);
                if (c != 0) return c < 0;
            }
            return false;
        }

        // What the page shows of one: {path, version, older, models, nodes, missing: [folder names]}.
        Dictionary<string, object> Comfy(string root)
        {
            var app = Path.Combine(root, "ComfyUI");
            var version = "";
            try
            {
                var m = Regex.Match(File.ReadAllText(Path.Combine(app, "comfyui_version.py")), "__version__\\s*=\\s*[\"']([^\"']+)");
                if (m.Success) version = m.Groups[1].Value;
            }
            catch { }
            var present = new HashSet<string>();
            try
            {
                foreach (var d in Directory.GetDirectories(Path.Combine(app, "custom_nodes")))
                    if (!d.EndsWith(".disabled", StringComparison.OrdinalIgnoreCase)) present.Add(NodeKey(Path.GetFileName(d)));
            }
            catch { }
            var bundled = NodeNames;
            return new Dictionary<string, object>
            {
                { "path", root }, { "version", version }, { "older", Older(version, Manifest["comfyui"] as string) }, { "models", ModelsOf(root) },
                { "nodes", bundled.Count }, { "missing", bundled.Where(n => !present.Contains(NodeKey(n))).ToList() },
            };
        }

        public object InspectComfy(string folder)
        {
            var root = PortableRoot(folder);
            if (root == null) throw new SetupProblem("not_comfyui", folder);
            return Comfy(root);
        }

        // Portable ComfyUIs in the usual places: two levels below each fixed drive and the user's own folders. The one an
        // earlier installation used comes first; the bundled copy of an earlier installation is not offered.
        List<Dictionary<string, object>> FindComfy(Dictionary<string, object> existing)
        {
            var roots = new List<string>();
            void Consider(string dir)
            {
                var root = PortableRoot(dir);
                if (root != null && !roots.Contains(root, StringComparer.OrdinalIgnoreCase)) roots.Add(root);
            }
            string ownCopy = null;
            if (existing != null)
            {
                if (OwnComfy(existing)) ownCopy = Path.Combine((string)existing["install"], "ComfyUI");
                else Consider((string)existing["comfy"]);
            }
            var skip = new[] { "Windows", "Program Files", "Program Files (x86)", "ProgramData", "$Recycle.Bin", "System Volume Information", "Recovery" };
            IEnumerable<string> Children(string dir)
            {
                try
                {
                    return new DirectoryInfo(dir).EnumerateDirectories()
                        .Where(d => (d.Attributes & (FileAttributes.Hidden | FileAttributes.System)) == 0 && !skip.Contains(d.Name, StringComparer.OrdinalIgnoreCase))
                        .Select(d => d.FullName).Take(400).ToList();
                }
                catch { return new string[0]; }
            }
            var places = DriveInfo.GetDrives().Where(d => d.DriveType == DriveType.Fixed && d.IsReady).Select(d => d.RootDirectory.FullName).ToList();
            var home = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
            places.AddRange(new[] { home, Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory),
                                    Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments), Path.Combine(home, "Downloads") });
            foreach (var place in places.Where(Directory.Exists).Distinct(StringComparer.OrdinalIgnoreCase))
                foreach (var child in Children(place))
                {
                    Consider(child);
                    if (PortableRoot(child) == null) foreach (var grandchild in Children(child)) Consider(grandchild);
                }
            return roots.Where(r => ownCopy == null || !string.Equals(r, ownCopy, StringComparison.OrdinalIgnoreCase)).Select(Comfy).ToList();
        }

        // The NVIDIA card and its driver, from nvidia-smi: {name, driver, ok, problem: "" | "no_nvidia" | "old_driver"}.
        static Dictionary<string, object> Graphics()
        {
            string output = "";
            try
            {
                var p = Process.Start(new ProcessStartInfo("nvidia-smi", "--query-gpu=name,driver_version --format=csv,noheader")
                { UseShellExecute = false, RedirectStandardOutput = true, CreateNoWindow = true });
                var reading = p.StandardOutput.ReadToEndAsync();
                if (reading.Wait(15000)) output = reading.Result;
                else try { p.Kill(); } catch { }
            }
            catch { }
            var line = output.Split('\n').Select(l => l.Trim()).FirstOrDefault(l => l.Contains(","));
            if (line == null) return new Dictionary<string, object> { { "name", "" }, { "driver", "" }, { "ok", false }, { "problem", "no_nvidia" } };
            var cells = line.Split(',').Select(c => c.Trim()).ToArray();
            int.TryParse(cells[1].Split('.')[0], out var major);
            bool ok = major >= MinDriver;
            return new Dictionary<string, object> { { "name", cells[0] }, { "driver", cells[1] }, { "ok", ok }, { "problem", ok ? "" : "old_driver" } };
        }

        // The model files waiting in a folder (the one beside the setup): how many and how large.
        static Dictionary<string, object> Inbox(string folder)
        {
            long files = 0, bytes = 0;
            if (Directory.Exists(folder))
                foreach (var f in Directory.EnumerateFiles(folder, "*", SearchOption.AllDirectories))
                    if (ModelExtensions.Contains(Path.GetExtension(f).ToLowerInvariant()))
                    {
                        files++;
                        bytes += new FileInfo(f).Length;
                    }
            return new Dictionary<string, object> { { "path", folder }, { "files", files }, { "bytes", bytes } };
        }

        // ---- checking the places chosen ----------------------------------------------------------------------------------
        static string Clean(string path)
        {
            path = (path ?? "").Trim().Trim('"');
            if (path.Length == 0) return "";
            try { return Path.GetFullPath(path).TrimEnd('\\') + (path.Length == 3 && path.EndsWith(":\\") ? "\\" : ""); }
            catch { return null; }
        }

        public object Check(string install, string library, string data, string comfy)
        {
            var problems = new List<object>();
            var warnings = new List<object>();
            bool external = !string.IsNullOrWhiteSpace(comfy);
            if (external && PortableRoot(comfy) == null)
                problems.Add(new Dictionary<string, object> { { "field", "comfy" }, { "code", "not_comfyui" } });
            var places = new Dictionary<string, string> { { "install", Clean(install) }, { "library", Clean(library) }, { "data", Clean(data) } };
            var windows = Environment.GetFolderPath(Environment.SpecialFolder.Windows);
            var programFiles = new[] { Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles),
                                       Environment.GetFolderPath(Environment.SpecialFolder.ProgramFilesX86) };
            foreach (var kv in places)
            {
                var p = kv.Value;
                if (string.IsNullOrEmpty(p) || !Path.IsPathRooted(p) || p.Length < 4)
                {
                    problems.Add(new Dictionary<string, object> { { "field", kv.Key }, { "code", "bad_path" } });
                    continue;
                }
                if (p.StartsWith(windows, StringComparison.OrdinalIgnoreCase) ||
                    programFiles.Any(pf => !string.IsNullOrEmpty(pf) && p.StartsWith(pf, StringComparison.OrdinalIgnoreCase)))
                    problems.Add(new Dictionary<string, object> { { "field", kv.Key }, { "code", "protected" } });
                else if (!Writable(p))
                    problems.Add(new Dictionary<string, object> { { "field", kv.Key }, { "code", "not_writable" } });
                if (p.Any(ch => ch > 127))
                    warnings.Add(new Dictionary<string, object> { { "field", kv.Key }, { "code", "non_ascii" } });
            }
            var installed = ReadInfo(places["install"] ?? "");
            if (installed.TryGetValue("version", out var have) && have is string haveText && Older((string)Manifest["version"], haveText))
                problems.Add(new Dictionary<string, object> { { "field", "install" }, { "code", "newer_installed" }, { "detail", haveText } });
            long program = BytesOf(t => t != "library" && !(external && t == "comfyui")), lib = BytesOf(t => t == "library");
            var need = new Dictionary<string, long>(StringComparer.OrdinalIgnoreCase);
            void Add(string path, long n)
            {
                if (string.IsNullOrEmpty(path) || !Path.IsPathRooted(path)) return;
                var root = Path.GetPathRoot(path);
                need[root] = (need.TryGetValue(root, out var v) ? v : 0) + n;
            }
            Add(places["install"], program);
            Add(places["library"], lib);
            long free = 0;
            foreach (var kv in need)
            {
                long room = 0;
                try { room = new DriveInfo(kv.Key).AvailableFreeSpace; } catch { }
                if (kv.Key.Equals(Path.GetPathRoot(places["install"] ?? ""), StringComparison.OrdinalIgnoreCase)) free = room;
                if (room < kv.Value + (512L << 20))
                    problems.Add(new Dictionary<string, object> { { "field", kv.Key.Equals(Path.GetPathRoot(places["install"] ?? ""), StringComparison.OrdinalIgnoreCase) ? "install" : "library" },
                                                                  { "code", "no_room" }, { "need", kv.Value }, { "free", room } });
            }
            return new Dictionary<string, object>
            {
                { "install", places["install"] }, { "library", places["library"] }, { "data", places["data"] },
                { "need", program + lib }, { "free", free }, { "problems", problems }, { "warnings", warnings },
            };
        }

        // Can a folder be made (or written) here? The nearest folder that exists is tried.
        static bool Writable(string path)
        {
            var dir = path;
            while (!string.IsNullOrEmpty(dir) && !Directory.Exists(dir)) dir = Path.GetDirectoryName(dir);
            if (string.IsNullOrEmpty(dir)) return false;
            var probe = Path.Combine(dir, ".md-setup-" + Guid.NewGuid().ToString("N").Substring(0, 8));
            try
            {
                Directory.CreateDirectory(probe);
                Directory.Delete(probe);
                return true;
            }
            catch { return false; }
        }

        // ---- the folder picker ---------------------------------------------------------------------------------------------
        public object List(string path)
        {
            path = (path ?? "").Trim().Trim('"');
            if (path.Length == 0 || !Directory.Exists(path))
            {
                var drives = DriveInfo.GetDrives().Where(d => d.IsReady && (d.DriveType == DriveType.Fixed || d.DriveType == DriveType.Removable))
                    .Select(d => new Dictionary<string, object> { { "name", d.Name.TrimEnd('\\') }, { "path", d.RootDirectory.FullName } }).ToList();
                return new Dictionary<string, object> { { "path", "" }, { "parent", null }, { "dirs", drives } };
            }
            path = Path.GetFullPath(path);
            var dirs = new List<Dictionary<string, object>>();
            try
            {
                foreach (var d in new DirectoryInfo(path).EnumerateDirectories())
                {
                    if ((d.Attributes & (FileAttributes.Hidden | FileAttributes.System)) != 0) continue;
                    dirs.Add(new Dictionary<string, object> { { "name", d.Name }, { "path", d.FullName } });
                    if (dirs.Count >= 500) break;
                }
            }
            catch { }
            var parent = Directory.GetParent(path);
            return new Dictionary<string, object>
            {
                { "path", path }, { "parent", parent?.FullName ?? "" },
                { "dirs", dirs.OrderBy(d => (string)d["name"], StringComparer.CurrentCultureIgnoreCase).ToList() },
            };
        }

        public object MakeFolder(string parent, string name)
        {
            name = (name ?? "").Trim();
            if (name.Length == 0 || name.IndexOfAny(Path.GetInvalidFileNameChars()) >= 0 || name == "." || name == "..")
                throw new SetupProblem("bad_name", name);
            var path = Path.Combine(parent, name);
            try { Directory.CreateDirectory(path); }
            catch (Exception ex) { throw new SetupProblem("not_writable", ex.Message); }
            return path;
        }

        // ---- installing ------------------------------------------------------------------------------------------------------
        // An existing ComfyUI gets the workflows in a folder of their own, beside the user's.
        const string WorkflowFolder = "Model Depot";

        static string Target(string target, string install, string library, string external)
        {
            switch (target)
            {
                case "app": return Path.Combine(install, "app");
                case "python": return Path.Combine(install, "python");
                case "llm": return Path.Combine(install, "llm", "llama.cpp");
                case "comfyui": return Path.Combine(install, "ComfyUI");
                case "library": return library;
                case "workflows":
                    return external == null ? Path.Combine(install, "ComfyUI", "ComfyUI", "user", "default", "workflows")
                        : Path.Combine(external, "ComfyUI", "user", "default", "workflows", WorkflowFolder);
            }
            throw new SetupProblem("bad_payload", target);
        }

        public void Install(string install, string library, string data, string comfy, string lang, Action<Dictionary<string, object>> report, CancellationToken token)
        {
            var checkResult = (Dictionary<string, object>)Check(install, library, data, comfy);
            if (((List<object>)checkResult["problems"]).Count > 0) throw new SetupProblem("check_failed");
            install = (string)checkResult["install"];
            library = (string)checkResult["library"];
            data = (string)checkResult["data"];
            lang = lang == "en" ? "en" : "zh";
            // an existing portable ComfyUI: the bundled one is not unpacked, and nothing of the existing one is replaced
            var external = string.IsNullOrWhiteSpace(comfy) ? null : PortableRoot(comfy);

            StopRunning(install);
            var parts = Parts.Where(p => external == null || (string)p["target"] != "comfyui").ToList();
            long total = parts.Sum(p => Long(p["bytes"])), before = 0;
            var made = new List<string>();      // what this run made, to take away again if it stops
            bool installMade = !Directory.Exists(install);
            bool update = File.Exists(Path.Combine(install, "app", "install.json"));
            var started = DateTime.UtcNow;
            try
            {
                Directory.CreateDirectory(install);
                foreach (var part in parts)
                {
                    token.ThrowIfCancellationRequested();
                    var dest = Target((string)part["target"], install, library, external);
                    if (!Directory.Exists(dest) && part["target"] as string != "library") made.Add(dest);
                    Directory.CreateDirectory(dest);
                    long size = Long(part["bytes"]);
                    long done0 = before;
                    var target = (string)part["target"];
                    Action<long> progress = pct => report(new Dictionary<string, object>
                    {
                        { "stage", "unpack" }, { "part", part["name"] }, { "done", done0 + size * pct / 100 }, { "total", total },
                        { "elapsed", (DateTime.UtcNow - started).TotalSeconds },
                    });
                    var archive = Path.Combine(Payload, (string)part["file"]);
                    if (target == "workflows")
                        workflowRecord = MergeWorkflows(archive, dest, ReadInfo(install), token, progress);
                    else
                        // the library never loses a file of the user's: what is there stays. An update of the bundled
                        // ComfyUI leaves the user's own settings, launch scripts and newer custom nodes as they are.
                        Unpack(archive, dest, target == "library", token, progress,
                               update && target == "comfyui" ? ComfyKeptOnUpdate(dest) : null);
                    before += size;
                }
                token.ThrowIfCancellationRequested();
                report(new Dictionary<string, object> { { "stage", "configure" }, { "done", total }, { "total", total }, { "elapsed", (DateTime.UtcNow - started).TotalSeconds } });
                Configure(install, library, data, lang, total, external, update);
            }
            catch (Exception ex) when (update)
            {
                // the program files are partly replaced already; running the setup again completes them
                throw new SetupProblem("update_incomplete", ex is OperationCanceledException ? "" : (ex as SetupProblem)?.Detail ?? ex.Message);
            }
            catch (Exception)
            {
                foreach (var dir in made.AsEnumerable().Reverse()) TryDelete(dir);
                if (installMade && Directory.Exists(install) && !Directory.EnumerateFileSystemEntries(install).Any()) TryDelete(install);
                throw;
            }
        }

        Dictionary<string, object> workflowRecord;     // the workflow files this run wrote, with their fingerprints

        // What an update of the bundled ComfyUI does not replace (paths inside the archive, as 7za's -x! takes them):
        // the user's settings and the manager's (user\), the launch scripts (the setup may have added the library's
        // place to them; the user may have added options), and a custom node whose installed version is newer than the
        // package's (Image Atelier installs the PixAI tagger too). A custom node the package no longer carries is not
        // removed: it may be in use.
        List<string> ComfyKeptOnUpdate(string comfy)
        {
            var kept = new List<string> { "ComfyUI\\user", "*.bat", "advanced\\*.bat", "ComfyUI\\extra_model_paths.yaml" };
            var versions = NodeVersions;
            foreach (var name in NodeNames)
            {
                var have = NodeVersion(Path.Combine(comfy, "ComfyUI", "custom_nodes", name));
                var ship = versions.TryGetValue(name, out var v) ? v as string : "";
                if (have != "" && ship != "" && Older(ship, have)) kept.Add("ComfyUI\\custom_nodes\\" + name);
            }
            return kept;
        }

        // The workflows: unpacked beside, then copied in file by file. A file the user has not changed since the setup
        // wrote it (its fingerprint is the one noted then) is replaced by the package's; one the user changed or made is
        // kept. So corrected workflows reach an installation, and the user's edits stay.
        Dictionary<string, object> MergeWorkflows(string archive, string dest, Dictionary<string, object> info, CancellationToken token, Action<long> progress)
        {
            var noted = info.TryGetValue("workflow_files", out var w) && w is Dictionary<string, object> d ? d : new Dictionary<string, object>();
            var temp = Path.Combine(Path.GetTempPath(), "ModelDepotSetup", "workflows-" + Guid.NewGuid().ToString("N").Substring(0, 8));
            var record = new Dictionary<string, object>();
            try
            {
                Unpack(archive, temp, false, token, progress);
                foreach (var file in Directory.GetFiles(temp, "*", SearchOption.AllDirectories))
                {
                    var rel = file.Substring(temp.Length).TrimStart('\\');
                    var to = Path.Combine(dest, rel);
                    var shipped = Sha256(file);
                    bool write = !File.Exists(to);
                    if (!write)
                    {
                        var now = Sha256(to);
                        write = now == shipped || (noted.TryGetValue(rel, out var was) ? was as string == now : EarlierWorkflow(rel, now));
                    }
                    if (write)
                    {
                        Directory.CreateDirectory(Path.GetDirectoryName(to));
                        File.Copy(file, to, true);
                        record[rel] = shipped;
                    }
                    else if (noted.TryGetValue(rel, out var keptHash)) record[rel] = keptHash;    // changed by the user: kept
                }
            }
            finally { TryDelete(temp); }
            return record;
        }

        static string Sha256(string path)
        {
            using (var sha = System.Security.Cryptography.SHA256.Create())
            using (var f = File.OpenRead(path))
                return BitConverter.ToString(sha.ComputeHash(f)).Replace("-", "").ToLowerInvariant();
        }

        // One archive into a folder with 7za, reporting its percentage. Stopping kills it.
        void Unpack(string archive, string dest, bool keepExisting, CancellationToken token, Action<long> progress, List<string> except = null)
        {
            if (!File.Exists(archive)) throw new SetupProblem("no_payload", archive);
            var args = string.Format("x \"{0}\" -o\"{1}\" -y {2} -bsp1 -bso0 -bse1 -mmt=on", archive, dest, keepExisting ? "-aos" : "-aoa");
            if (except != null) args += string.Concat(except.Select(x => " \"-x!" + x + "\""));
            var p = Process.Start(new ProcessStartInfo(Path.Combine(Payload, "7za.exe"), args)
            { UseShellExecute = false, RedirectStandardOutput = true, CreateNoWindow = true, StandardOutputEncoding = Encoding.UTF8 });
            var output = new StringBuilder();
            using (token.Register(() => { try { if (!p.HasExited) p.Kill(); } catch { } }))
            {
                var buffer = new char[256];
                var digits = new StringBuilder();
                long last = -1;
                int n;
                while ((n = p.StandardOutput.Read(buffer, 0, buffer.Length)) > 0)
                {
                    for (int i = 0; i < n; i++)
                    {
                        var ch = buffer[i];
                        if (output.Length < 4000) output.Append(ch);
                        if (char.IsDigit(ch)) { digits.Append(ch); continue; }
                        if (ch == '%' && digits.Length > 0 && digits.Length <= 3)
                        {
                            long pct = long.Parse(digits.ToString(), CultureInfo.InvariantCulture);
                            // a report that fails must not stop the reading: 7za waits once its output is not read
                            if (pct != last) { last = pct; try { progress(pct); } catch { } }
                        }
                        digits.Clear();
                    }
                }
                p.WaitForExit();
            }
            token.ThrowIfCancellationRequested();
            if (p.ExitCode != 0)
            {
                var text = output.ToString();
                if (Regex.IsMatch(text, "not enough space|disk is full|ERROR_DISK_FULL", RegexOptions.IgnoreCase)) throw new SetupProblem("no_room", dest);
                throw new SetupProblem("unpack_failed", Path.GetFileName(archive) + ": " + Regex.Replace(text, @"[\b\r]+", " ").Trim());
            }
            progress(100);
        }

        void Configure(string install, string library, string data, string lang, long total, string external, bool update)
        {
            bool own = external == null;
            var comfy = external ?? Path.Combine(install, "ComfyUI");
            var before = ReadInfo(install);
            Directory.CreateDirectory(data);
            // an installation from before 0.6.2 keeps its "models" folder for files to sort: Model Depot's settings name
            // it, and a second, new folder would never be sorted
            var inbox = Path.Combine(install, InboxName);
            var oldInbox = Path.Combine(install, "models");
            if (update && !Directory.Exists(inbox) && Directory.Exists(oldInbox)) inbox = oldInbox;
            Directory.CreateDirectory(inbox);
            Directory.CreateDirectory(Path.Combine(install, "llm", "models"));
            if (own) ConfigureComfy(comfy, library, lang, update);
            var packageInbox = new[] { Path.Combine(exeDir, InboxName), Path.Combine(exeDir, "models") }.FirstOrDefault(Directory.Exists);

            // what Model Depot reads next to run.py (paths.install_info), and where another setup finds this one
            var info = new Dictionary<string, object>
            {
                { "version", Manifest["version"] }, { "install_dir", install }, { "data_dir", data },
                { "python", Path.Combine(install, "python", "python.exe") }, { "comfyui_dir", comfy }, { "comfyui_own", own },
                { "models_dir", library }, { "llm_dir", Path.Combine(install, "llm") }, { "inbox", new[] { inbox } },
                { "inbox_once", packageInbox != null ? new[] { packageInbox } : new string[0] },
                { "language", lang },
                // what is the setup's own, for an update and for removing: the custom nodes it brought, the workflow
                // files it wrote (with their fingerprints, so that the user's edits are told apart)
                { "own_custom_nodes", own ? NodeNames : new List<string>() },
                { "workflow_files", workflowRecord ?? (before.TryGetValue("workflow_files", out var wf) ? wf : new Dictionary<string, object>()) },
            };
            File.WriteAllText(Path.Combine(install, "app", "install.json"), json.Serialize(info), new UTF8Encoding(false));

            // the uninstaller: this exe and its page
            var uninstall = Path.Combine(install, "uninstall");
            Directory.CreateDirectory(uninstall);
            var self = Process.GetCurrentProcess().MainModule.FileName;
            File.Copy(self, Path.Combine(uninstall, "ModelDepotSetup.exe"), true);
            CopyTree(UiFolder, Path.Combine(uninstall, "ui"));

            var exe = Path.Combine(install, "app", "desktop", "app", "ModelDepot.exe");
            var comfyBat = Path.Combine(comfy, LauncherName);
            using (var key = Registry.CurrentUser.CreateSubKey(RegKey))
            {
                key.SetValue("InstallDir", install);
                key.SetValue("ComfyUIDir", comfy);
                key.SetValue("ComfyUIOwn", own ? 1 : 0, RegistryValueKind.DWord);
                key.SetValue("LibraryDir", library);
                key.SetValue("LlmDir", Path.Combine(install, "llm"));
                key.SetValue("DataDir", data);
                key.SetValue("Version", (string)Manifest["version"]);
            }
            using (var key = Registry.CurrentUser.CreateSubKey(UninstallKey))
            {
                key.SetValue("DisplayName", "Model Depot");
                key.SetValue("DisplayVersion", (string)Manifest["version"]);
                key.SetValue("Publisher", "Model Depot");
                key.SetValue("InstallLocation", install);
                key.SetValue("DisplayIcon", exe);
                key.SetValue("UninstallString", "\"" + Path.Combine(uninstall, "ModelDepotSetup.exe") + "\" /uninstall");
                key.SetValue("EstimatedSize", (int)Math.Min(int.MaxValue, total / 1024), RegistryValueKind.DWord);
                key.SetValue("NoModify", 1, RegistryValueKind.DWord);
                key.SetValue("NoRepair", 1, RegistryValueKind.DWord);
            }

            // an existing ComfyUI is started the way its owner already starts it: no shortcut of ours
            foreach (var folder in new[] { Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory), StartMenu })
            {
                try
                {
                    Directory.CreateDirectory(folder);
                    Shortcut(Path.Combine(folder, "Model Depot.lnk"), exe, "", Path.GetDirectoryName(exe), exe);
                    if (own) Shortcut(Path.Combine(folder, "ComfyUI.lnk"), comfyBat, "", comfy, Path.Combine(comfy, "python_embeded", "python.exe"));
                }
                catch (Exception) { }
            }
        }

        // The launcher the ComfyUI shortcut starts: it opens ComfyUI's page when ComfyUI is already running (started by
        // Image Atelier in the background, say) instead of starting a second one that fails on the busy port.
        const string LauncherName = "Start ComfyUI.cmd";
        const string Launcher =
            "@echo off\r\n" +
            "rem Written by the Model Depot setup: opens ComfyUI's page when ComfyUI is already running, else starts it.\r\n" +
            "powershell -NoProfile -ExecutionPolicy Bypass -Command \"$c = New-Object Net.Sockets.TcpClient; try { $c.Connect('127.0.0.1', 8188); exit 0 } catch { exit 1 } finally { $c.Close() }\"\r\n" +
            "if %errorlevel%==0 (\r\n    start \"\" http://127.0.0.1:8188/\r\n    exit /b 0\r\n)\r\n" +
            "cd /d \"%~dp0\"\r\ncall run_nvidia_gpu.bat\r\n";

        // The bundled ComfyUI: the library where the user put it, the page in the setup's language, the launcher. An
        // update changes only the launcher: the rest is the user's by then.
        void ConfigureComfy(string comfy, string library, string lang, bool update)
        {
            var app = Path.Combine(comfy, "ComfyUI");
            var defaultLibrary = Path.Combine(app, "models");
            Directory.CreateDirectory(defaultLibrary);
            File.WriteAllText(Path.Combine(comfy, LauncherName), Launcher, Encoding.ASCII);
            if (update) return;

            // ComfyUI reads the library from where the user put it. A path the system's code page cannot write (Chinese
            // on a Western Windows) goes into the script as UTF-8, which cmd reads after "chcp 65001".
            if (!string.Equals(Path.GetFullPath(library), Path.GetFullPath(defaultLibrary), StringComparison.OrdinalIgnoreCase))
            {
                var strict = Encoding.GetEncoding(Encoding.Default.CodePage, EncoderFallback.ExceptionFallback, DecoderFallback.ExceptionFallback);
                bool fits;
                try { strict.GetBytes(library); fits = true; } catch (EncoderFallbackException) { fits = false; }
                var scripts = Directory.GetFiles(comfy, "*.bat").Concat(Directory.Exists(Path.Combine(comfy, "advanced"))
                    ? Directory.GetFiles(Path.Combine(comfy, "advanced"), "*.bat") : new string[0]);
                foreach (var bat in scripts)
                {
                    var text = File.ReadAllText(bat, Encoding.Default);
                    if (text.Contains("--models-directory") || !text.Contains("main.py")) continue;
                    text = Regex.Replace(text, @"main\.py(?=[ \r\n])", m => "main.py --models-directory \"" + library + "\"");
                    if (fits) File.WriteAllText(bat, text, Encoding.Default);
                    else File.WriteAllText(bat, "@chcp 65001 >nul\r\n" + text, new UTF8Encoding(false));
                }
            }
            // SeedVR2's model and VAE go into ComfyUI's standard folders (diffusion_models, vae), as ComfyUI's own
            // templates have them: no folder of their own registered twice (the VAE list showed the 7.9 GB model).
            // ComfyUI's page in the language of the setup
            var settingsPath = Path.Combine(app, "user", "default", "comfy.settings.json");
            Directory.CreateDirectory(Path.GetDirectoryName(settingsPath));
            Dictionary<string, object> settings;
            try { settings = json.Deserialize<Dictionary<string, object>>(File.ReadAllText(settingsPath, Encoding.UTF8)); }
            catch { settings = new Dictionary<string, object>(); }
            settings["Comfy.Locale"] = lang;
            File.WriteAllText(settingsPath, json.Serialize(settings), new UTF8Encoding(false));
        }

        static string StartMenu => Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Programs), "Model Depot");

        static void Shortcut(string path, string target, string args, string workDir, string icon)
        {
            var shellType = Type.GetTypeFromProgID("WScript.Shell");
            dynamic shell = Activator.CreateInstance(shellType);
            try
            {
                dynamic link = shell.CreateShortcut(path);
                link.TargetPath = target;
                link.Arguments = args;
                link.WorkingDirectory = workDir;
                if (File.Exists(icon)) link.IconLocation = icon + ",0";
                link.Save();
            }
            finally
            {
                System.Runtime.InteropServices.Marshal.FinalReleaseComObject(shell);
            }
        }

        // Model Depot and ComfyUI of this installation must not be running while their files change.
        static void StopRunning(string install)
        {
            var exe = Path.Combine(install, "app", "desktop", "app", "ModelDepot.exe");
            if (File.Exists(exe)) RunAndWait(exe, "--quit", 40000);
            var python = Path.Combine(install, "python", "python.exe");
            var run = Path.Combine(install, "app", "run.py");
            if (File.Exists(python) && File.Exists(run)) RunAndWait(python, "\"" + run + "\" --stop", 40000);
            var running = RunningUnder(Path.Combine(install, "ComfyUI"));
            if (running.Count > 0) throw new SetupProblem("comfyui_running", string.Join(", ", running));
        }

        static void RunAndWait(string exe, string args, int ms)
        {
            try
            {
                var p = Process.Start(new ProcessStartInfo(exe, args) { UseShellExecute = false, CreateNoWindow = true });
                p.WaitForExit(ms);
            }
            catch { }
        }

        // Programs started from inside a folder (ComfyUI's Python), by the file they run from.
        static List<string> RunningUnder(string folder)
        {
            var found = new List<string>();
            var prefix = Path.GetFullPath(folder).TrimEnd('\\') + "\\";
            foreach (var p in Process.GetProcesses())
            {
                try
                {
                    var file = p.MainModule.FileName;
                    if (file.StartsWith(prefix, StringComparison.OrdinalIgnoreCase)) found.Add(Path.GetFileName(file) + " (" + p.Id + ")");
                }
                catch { }
            }
            return found;
        }

        public void OpenModelDepot(string install)
        {
            var exe = Path.Combine(install, "app", "desktop", "app", "ModelDepot.exe");
            if (File.Exists(exe)) Process.Start(new ProcessStartInfo(exe) { UseShellExecute = true, WorkingDirectory = Path.GetDirectoryName(exe) });
        }

        // ---- removing --------------------------------------------------------------------------------------------------------
        public object UninstallInfo(string install)
        {
            var info = ReadInfo(install);
            bool own = OwnComfyInfo(info);
            var kept = new Dictionary<string, object>
            {
                { "library", info.TryGetValue("models_dir", out var l) ? l : Path.Combine(install, "ComfyUI", "ComfyUI", "models") },
                { "data", info.TryGetValue("data_dir", out var d) ? d : Path.Combine(install, "data") },
                { "inbox", Directory.Exists(Path.Combine(install, InboxName)) ? Path.Combine(install, InboxName) : Path.Combine(install, "models") },
            };
            if (own)
            {
                kept["comfy_user"] = Path.Combine(install, "ComfyUI", "ComfyUI");
                var others = ForeignNodes(install, info);
                if (others.Count > 0) kept["comfy_plugins"] = others;
            }
            else kept["comfy_external"] = info["comfyui_dir"];
            return new Dictionary<string, object>
            {
                { "mode", "uninstall" }, { "lang", Lang }, { "version", info.TryGetValue("version", out var v) ? v : "" },
                { "install", install }, { "kept", kept },
            };
        }

        // The custom nodes of the installation's ComfyUI that are not the setup's own: other programs' or the user's.
        static List<string> OwnNodes(Dictionary<string, object> info) =>
            info.TryGetValue("own_custom_nodes", out var v) && v is System.Collections.ArrayList list
                ? list.Cast<object>().Select(o => o.ToString()).ToList() : OldOwnNodes.ToList();

        static List<string> ForeignNodes(string install, Dictionary<string, object> info)
        {
            var nodes = Path.Combine(install, "ComfyUI", "ComfyUI", "custom_nodes");
            if (!Directory.Exists(nodes)) return new List<string>();
            var own = new HashSet<string>(OwnNodes(info).Select(NodeKey));
            return Directory.GetDirectories(nodes).Select(Path.GetFileName)
                .Where(n => !n.StartsWith(".") && !n.StartsWith("__") && !own.Contains(NodeKey(n.Replace(".disabled", "")))).ToList();
        }

        // An installation from before the choice of ComfyUI always has its own.
        static bool OwnComfyInfo(Dictionary<string, object> info) =>
            !(info.TryGetValue("comfyui_own", out var own) && own is bool b && !b && info.TryGetValue("comfyui_dir", out var dir) && dir is string);

        Dictionary<string, object> ReadInfo(string install)
        {
            try { return json.Deserialize<Dictionary<string, object>>(File.ReadAllText(Path.Combine(install, "app", "install.json"), Encoding.UTF8)); }
            catch { return new Dictionary<string, object>(); }
        }

        public void Uninstall(string install, Action<Dictionary<string, object>> report, CancellationToken token)
        {
            if (string.IsNullOrEmpty(install) || !Directory.Exists(install)) throw new SetupProblem("not_installed", install);
            // only <install>\ComfyUI is ever removed below: an existing ComfyUI the installation used stays as it is
            var info = ReadInfo(install);
            bool ownComfy = OwnComfyInfo(info);
            StopRunning(install);
            var comfy = Path.Combine(install, "ComfyUI");
            var app = Path.Combine(comfy, "ComfyUI");
            var doomed = new List<string> { Path.Combine(install, "app"), Path.Combine(install, "python"), Path.Combine(install, "llm", "llama.cpp"),
                                            Path.Combine(comfy, "python_embeded"), Path.Combine(comfy, "update"), Path.Combine(comfy, "advanced") };
            if (Directory.Exists(app))
            {
                doomed.AddRange(Directory.GetDirectories(app).Where(d => !ComfyKept.Contains(Path.GetFileName(d), StringComparer.OrdinalIgnoreCase)));
                doomed.AddRange(Directory.GetFiles(app));
                // the custom nodes the setup brought; other programs' and the user's stay
                var nodes = Path.Combine(app, "custom_nodes");
                if (Directory.Exists(nodes))
                {
                    var own = new HashSet<string>(OwnNodes(info).Select(NodeKey));
                    doomed.AddRange(Directory.GetDirectories(nodes).Where(d => own.Contains(NodeKey(Path.GetFileName(d).Replace(".disabled", ""))) ||
                                                                               Path.GetFileName(d) == "__pycache__"));
                    doomed.AddRange(Directory.GetFiles(nodes));
                }
            }
            if (Directory.Exists(comfy)) doomed.AddRange(Directory.GetFiles(comfy));
            int done = 0;
            foreach (var path in doomed)
            {
                report(new Dictionary<string, object> { { "stage", "remove" }, { "done", done }, { "total", doomed.Count } });
                TryDelete(path);
                done++;
            }
            foreach (var folder in new[] { Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory), StartMenu })
                foreach (var name in ownComfy ? new[] { "Model Depot.lnk", "ComfyUI.lnk" } : new[] { "Model Depot.lnk" })
                    TryDelete(Path.Combine(folder, name));
            TryDelete(StartMenu);
            Registry.CurrentUser.DeleteSubKeyTree(UninstallKey, false);
            Registry.CurrentUser.DeleteSubKeyTree(RegKey, false);
            TryDelete(Path.Combine(install, "uninstall"));
            foreach (var empty in new[] { Path.Combine(app, "custom_nodes"), Path.Combine(install, "llm", "models"), Path.Combine(install, "llm") })
                if (Directory.Exists(empty) && !Directory.EnumerateFileSystemEntries(empty).Any()) TryDelete(empty);
            report(new Dictionary<string, object> { { "stage", "remove" }, { "done", doomed.Count }, { "total", doomed.Count } });
        }

        // ---- files ---------------------------------------------------------------------------------------------------------
        public static void CopyTree(string from, string to)
        {
            Directory.CreateDirectory(to);
            foreach (var file in Directory.GetFiles(from)) File.Copy(file, Path.Combine(to, Path.GetFileName(file)), true);
            foreach (var dir in Directory.GetDirectories(from)) CopyTree(dir, Path.Combine(to, Path.GetFileName(dir)));
        }

        static void TryDelete(string path)
        {
            for (int i = 0; i < 3; i++)
            {
                try
                {
                    if (Directory.Exists(path))
                    {
                        foreach (var f in Directory.EnumerateFiles(path, "*", SearchOption.AllDirectories)) File.SetAttributes(f, FileAttributes.Normal);
                        Directory.Delete(path, true);
                    }
                    else if (File.Exists(path))
                    {
                        File.SetAttributes(path, FileAttributes.Normal);
                        File.Delete(path);
                    }
                    return;
                }
                catch { Thread.Sleep(300); }
            }
        }
    }
}
