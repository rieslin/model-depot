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
        // what removing the program leaves: the user's models, pictures, inputs and saved workflows
        static readonly string[] ComfyKept = { "models", "user", "output", "input" };

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
                { "inbox", Inbox(Path.Combine(exeDir, "models")) }, { "upgrade", existing != null },
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

        static bool Older(string version, string than)
        {
            int[] Parse(string s) => Regex.Matches(s ?? "", "\\d+").Cast<Match>().Select(m => int.Parse(m.Value, CultureInfo.InvariantCulture)).ToArray();
            var a = Parse(version);
            var b = Parse(than);
            if (a.Length == 0 || b.Length == 0) return false;
            for (int i = 0; i < Math.Max(a.Length, b.Length); i++)
            {
                int x = i < a.Length ? a[i] : 0, y = i < b.Length ? b[i] : 0;
                if (x != y) return x < y;
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
                    // the library and the saved workflows never lose a file of the user's: what is there stays
                    bool keep = part["target"] as string == "library" || part["target"] as string == "workflows";
                    Unpack(Path.Combine(Payload, (string)part["file"]), dest, keep, token, pct =>
                        report(new Dictionary<string, object>
                        {
                            { "stage", "unpack" }, { "part", part["name"] }, { "done", done0 + size * pct / 100 }, { "total", total },
                            { "elapsed", (DateTime.UtcNow - started).TotalSeconds },
                        }));
                    before += size;
                }
                token.ThrowIfCancellationRequested();
                report(new Dictionary<string, object> { { "stage", "configure" }, { "done", total }, { "total", total }, { "elapsed", (DateTime.UtcNow - started).TotalSeconds } });
                Configure(install, library, data, lang, total, external);
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

        // One archive into a folder with 7za, reporting its percentage. Stopping kills it.
        void Unpack(string archive, string dest, bool keepExisting, CancellationToken token, Action<long> progress)
        {
            if (!File.Exists(archive)) throw new SetupProblem("no_payload", archive);
            var args = string.Format("x \"{0}\" -o\"{1}\" -y {2} -bsp1 -bso0 -bse1 -mmt=on", archive, dest, keepExisting ? "-aos" : "-aoa");
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

        void Configure(string install, string library, string data, string lang, long total, string external)
        {
            bool own = external == null;
            var comfy = external ?? Path.Combine(install, "ComfyUI");
            Directory.CreateDirectory(data);
            Directory.CreateDirectory(Path.Combine(install, "models"));
            Directory.CreateDirectory(Path.Combine(install, "llm", "models"));
            if (own) ConfigureComfy(comfy, library, lang);

            // what Model Depot reads next to run.py (paths.install_info), and where another setup finds this one
            var info = new Dictionary<string, object>
            {
                { "version", Manifest["version"] }, { "install_dir", install }, { "data_dir", data },
                { "python", Path.Combine(install, "python", "python.exe") }, { "comfyui_dir", comfy }, { "comfyui_own", own },
                { "models_dir", library }, { "llm_dir", Path.Combine(install, "llm") }, { "inbox", new[] { Path.Combine(install, "models") } },
                { "inbox_once", Directory.Exists(Path.Combine(exeDir, "models")) ? new[] { Path.Combine(exeDir, "models") } : new string[0] },
                { "language", lang },
            };
            File.WriteAllText(Path.Combine(install, "app", "install.json"), json.Serialize(info), new UTF8Encoding(false));

            // the uninstaller: this exe and its page
            var uninstall = Path.Combine(install, "uninstall");
            Directory.CreateDirectory(uninstall);
            var self = Process.GetCurrentProcess().MainModule.FileName;
            File.Copy(self, Path.Combine(uninstall, "ModelDepotSetup.exe"), true);
            CopyTree(UiFolder, Path.Combine(uninstall, "ui"));

            var exe = Path.Combine(install, "app", "desktop", "app", "ModelDepot.exe");
            var comfyBat = Path.Combine(comfy, "run_nvidia_gpu.bat");
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

        // The bundled ComfyUI: the library where the user put it, the SeedVR2 folder, the page in the setup's language.
        void ConfigureComfy(string comfy, string library, string lang)
        {
            var app = Path.Combine(comfy, "ComfyUI");
            var defaultLibrary = Path.Combine(app, "models");
            Directory.CreateDirectory(defaultLibrary);

            // ComfyUI reads the library from where the user put it
            if (!string.Equals(Path.GetFullPath(library), Path.GetFullPath(defaultLibrary), StringComparison.OrdinalIgnoreCase))
            {
                var scripts = Directory.GetFiles(comfy, "*.bat").Concat(Directory.Exists(Path.Combine(comfy, "advanced"))
                    ? Directory.GetFiles(Path.Combine(comfy, "advanced"), "*.bat") : new string[0]);
                foreach (var bat in scripts)
                {
                    var text = File.ReadAllText(bat, Encoding.Default);
                    if (text.Contains("--models-directory") || !text.Contains("main.py")) continue;
                    text = Regex.Replace(text, @"main\.py(?=[ \r\n])", m => "main.py --models-directory \"" + library + "\"");
                    File.WriteAllText(bat, text, Encoding.Default);
                }
            }
            // the SeedVR2 upscaler keeps its model and VAE in the library's SEEDVR2 folder, which ComfyUI does not search
            File.WriteAllText(Path.Combine(app, "extra_model_paths.yaml"),
                "# Written by the Model Depot setup: the SeedVR2 upscaler keeps its model and VAE in the library's SEEDVR2 folder.\n" +
                "seedvr2:\n    base_path: " + library.Replace('\\', '/').TrimEnd('/') + "/\n    diffusion_models: SEEDVR2\n    vae: SEEDVR2\n",
                new UTF8Encoding(false));
            Directory.CreateDirectory(Path.Combine(library, "SEEDVR2"));
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
                { "inbox", Path.Combine(install, "models") },
            };
            if (own) kept["comfy_user"] = Path.Combine(install, "ComfyUI", "ComfyUI");
            else kept["comfy_external"] = info["comfyui_dir"];
            return new Dictionary<string, object>
            {
                { "mode", "uninstall" }, { "lang", Lang }, { "version", info.TryGetValue("version", out var v) ? v : "" },
                { "install", install }, { "kept", kept },
            };
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
            bool ownComfy = OwnComfyInfo(ReadInfo(install));
            StopRunning(install);
            var comfy = Path.Combine(install, "ComfyUI");
            var app = Path.Combine(comfy, "ComfyUI");
            var doomed = new List<string> { Path.Combine(install, "app"), Path.Combine(install, "python"), Path.Combine(install, "llm", "llama.cpp"),
                                            Path.Combine(comfy, "python_embeded"), Path.Combine(comfy, "update"), Path.Combine(comfy, "advanced") };
            if (Directory.Exists(app))
            {
                doomed.AddRange(Directory.GetDirectories(app).Where(d => !ComfyKept.Contains(Path.GetFileName(d), StringComparer.OrdinalIgnoreCase)));
                doomed.AddRange(Directory.GetFiles(app));
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
            foreach (var empty in new[] { Path.Combine(install, "llm", "models"), Path.Combine(install, "llm") })
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
