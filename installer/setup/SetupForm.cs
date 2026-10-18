// The setup's window: Windows's own frame without its title bar (as the Model Depot window), a fixed size, and the
// setup's page in WebView2. The page asks for what it needs with postMessage ({id, cmd, ...}); the answer comes back
// with the same id, and the work under way reports with events ({event, ...}).
using System;
using System.Collections.Generic;
using System.Drawing;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using System.Threading;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

namespace ModelDepotSetup
{
    sealed class SetupForm : Form
    {
        static readonly Size PageDip = new Size(900, 720);
        static readonly Color Paper = Color.FromArgb(0xf3, 0xf3, 0xf2);
        const string Host = "setup.model-depot";

        readonly WebView2 web = new WebView2();
        readonly JavaScriptSerializer json = new JavaScriptSerializer { MaxJsonLength = 16 << 20 };
        readonly Engine engine;
        readonly string uninstallTarget;     // the installation to remove; null when installing
        CancellationTokenSource work;        // the install or removal under way
        bool closing;                        // the page agreed to close

        public SetupForm(string exeDir, string uninstallTarget)
        {
            this.uninstallTarget = uninstallTarget;
            engine = new Engine(exeDir);
            Text = uninstallTarget == null ? "Model Depot Setup" : "Model Depot";
            using (var res = typeof(SetupForm).Assembly.GetManifestResourceStream("ModelDepot.ico"))
                if (res != null) Icon = new Icon(res);
            FormBorderStyle = FormBorderStyle.FixedSingle;
            MaximizeBox = false;
            BackColor = Paper;
            StartPosition = Hidden ? FormStartPosition.Manual : FormStartPosition.CenterScreen;
            ShowInTaskbar = !Hidden;        // set before the window exists: changing it later makes the window again
            web.Dock = DockStyle.Fill;
            web.DefaultBackgroundColor = Paper;
            web.Visible = false;
            Controls.Add(web);
        }

        int Dpi => IsHandleCreated ? (int)Native.GetDpiForWindow(Handle) : 96;

        // For checks run while someone uses the computer (MODEL_DEPOT_SETUP_HIDDEN=1): the window opens beyond every
        // screen, without taking the focus or a taskbar button; the page is driven over WebView2's debugging port.
        static readonly bool Hidden = Environment.GetEnvironmentVariable("MODEL_DEPOT_SETUP_HIDDEN") == "1";

        protected override bool ShowWithoutActivation => Hidden;

        protected override void OnHandleCreated(EventArgs e)
        {
            base.OnHandleCreated(e);
            ClientSize = new Size(PageDip.Width * Dpi / 96, PageDip.Height * Dpi / 96);
            if (Hidden)
            {
                var all = SystemInformation.VirtualScreen;
                Location = new Point(all.Left - Width - 200, all.Top);
                return;
            }
            var area = Screen.FromHandle(Handle).WorkingArea;
            Location = new Point(area.Left + Math.Max(0, (area.Width - Width) / 2), area.Top + Math.Max(0, (area.Height - Height) / 2));
        }

        protected override void WndProc(ref Message m)
        {
            const int WM_NCCALCSIZE = 0x0083, WM_NCACTIVATE = 0x0086, WM_DPICHANGED = 0x02E0;
            if (m.Msg == WM_NCCALCSIZE && m.WParam != IntPtr.Zero)
            {
                // the title bar is given to the page; the thin frame and the system's corners and shadow stay
                var before = (Native.NCCALCSIZE_PARAMS)Marshal.PtrToStructure(m.LParam, typeof(Native.NCCALCSIZE_PARAMS));
                base.WndProc(ref m);
                var after = (Native.NCCALCSIZE_PARAMS)Marshal.PtrToStructure(m.LParam, typeof(Native.NCCALCSIZE_PARAMS));
                after.rgrc0.Top = before.rgrc0.Top;
                Marshal.StructureToPtr(after, m.LParam, false);
                m.Result = IntPtr.Zero;
                return;
            }
            if (m.Msg == WM_NCACTIVATE)
            {
                m.LParam = new IntPtr(-1);
                base.WndProc(ref m);
                return;
            }
            if (m.Msg == WM_DPICHANGED)
            {
                base.WndProc(ref m);
                ClientSize = new Size(PageDip.Width * Dpi / 96, PageDip.Height * Dpi / 96);
                return;
            }
            base.WndProc(ref m);
        }

        protected override async void OnLoad(EventArgs e)
        {
            base.OnLoad(e);
            var ui = engine.UiFolder;
            try
            {
                if (ui == null) throw new DirectoryNotFoundException("setup\\ui");
                CoreWebView2Environment.SetLoaderDllFolderPath(Program.Temp);
                var env = await CoreWebView2Environment.CreateAsync(null, Path.Combine(Program.Temp, "WebView2"));
                await web.EnsureCoreWebView2Async(env);
            }
            catch (Exception ex)
            {
                MessageBox.Show(this, "Model Depot Setup could not open its window.\nModel Depot 安装程序无法打开窗口。\n\n" + ex.Message, "Model Depot");
                Close();
                return;
            }
            var core = web.CoreWebView2;
            core.Settings.IsNonClientRegionSupportEnabled = true;       // the page's top bar moves the window
            core.Settings.AreDevToolsEnabled = Environment.GetEnvironmentVariable("MODEL_DEPOT_DEVTOOLS") == "1";
            core.Settings.IsStatusBarEnabled = false;
            core.Settings.IsZoomControlEnabled = false;
            core.Settings.IsPinchZoomEnabled = false;
            core.Settings.IsSwipeNavigationEnabled = false;
            core.Settings.AreBrowserAcceleratorKeysEnabled = false;
            core.Settings.AreDefaultContextMenusEnabled = false;
            core.SetVirtualHostNameToFolderMapping(Host, ui, CoreWebView2HostResourceAccessKind.Deny);
            core.WebMessageReceived += OnMessage;
            core.NavigationCompleted += (s, a) => web.Visible = true;
            core.NewWindowRequested += (s, a) => a.Handled = true;
            core.Navigate("https://" + Host + "/index.html" + (uninstallTarget == null ? "" : "?uninstall=1"));
        }

        // The close button, Alt+F4: the page decides (it asks before stopping an installation under way).
        protected override void OnFormClosing(FormClosingEventArgs e)
        {
            if (!closing && e.CloseReason == CloseReason.UserClosing && web.CoreWebView2 != null)
            {
                e.Cancel = true;
                Post(new Dictionary<string, object> { { "event", "close-request" } });
                return;
            }
            work?.Cancel();
            base.OnFormClosing(e);
        }

        protected override void OnFormClosed(FormClosedEventArgs e)
        {
            base.OnFormClosed(e);
            web.Dispose();
            // What the setup put in the temporary folder (WebView2's loader and the browser's profile) and, for the
            // uninstaller, its copy of itself (Program.cs) are removed a few seconds after it has exited.
            // WebView2's browser processes can hold the profile for some seconds after the window is gone: tried again
            // every second for half a minute.
            var folders = new List<string> { Program.Temp };
            var exeDir = Path.GetDirectoryName(Application.ExecutablePath);
            if (uninstallTarget != null && string.Equals(Path.GetFileName(exeDir), "ModelDepotUninstall", StringComparison.OrdinalIgnoreCase))
                folders.Add(exeDir);
            var removal = string.Join(" & ", folders.Select(f => "(if exist \"" + f + "\" rmdir /s /q \"" + f + "\" 2>nul)"));
            try
            {
                System.Diagnostics.Process.Start(new System.Diagnostics.ProcessStartInfo("cmd.exe",
                    "/c for /l %i in (1,1,30) do @(ping -n 2 127.0.0.1 >nul & " + removal + ")")
                { UseShellExecute = false, CreateNoWindow = true, WorkingDirectory = Path.GetTempPath() });
            }
            catch { }
        }

        // WebView2 may be touched on the window's own thread only: the work's reports are handed over to it.
        void Post(Dictionary<string, object> message)
        {
            var text = json.Serialize(message);
            void Send()
            {
                if (!IsDisposed && web.CoreWebView2 != null) web.CoreWebView2.PostWebMessageAsJson(text);
            }
            if (!InvokeRequired) { Send(); return; }
            try { BeginInvoke((Action)Send); }
            catch (InvalidOperationException) { }       // the window is closing
        }

        async void OnMessage(object sender, CoreWebView2WebMessageReceivedEventArgs e)
        {
            Dictionary<string, object> msg;
            try { msg = json.Deserialize<Dictionary<string, object>>(e.WebMessageAsJson); }
            catch { return; }
            object id = msg.TryGetValue("id", out var i) ? i : null;
            string cmd = msg.TryGetValue("cmd", out var c) ? c as string : null;
            var reply = new Dictionary<string, object> { { "id", id } };
            try
            {
                reply["result"] = await Run(cmd, msg);
                reply["ok"] = true;
            }
            catch (SetupProblem p)
            {
                reply["ok"] = false;
                reply["code"] = p.Code;
                reply["detail"] = p.Detail;
            }
            catch (Exception ex)
            {
                reply["ok"] = false;
                reply["code"] = "failed";
                reply["detail"] = ex.Message;
            }
            Post(reply);
        }

        static string Arg(Dictionary<string, object> msg, string key) => msg.TryGetValue(key, out var v) ? v as string ?? "" : "";

        async Task<object> Run(string cmd, Dictionary<string, object> msg)
        {
            switch (cmd)
            {
                case "info":
                    return await Task.Run(() => uninstallTarget == null ? engine.Info() : engine.UninstallInfo(uninstallTarget));
                case "check":
                    return await Task.Run(() => engine.Check(Arg(msg, "install"), Arg(msg, "library"), Arg(msg, "data"), Arg(msg, "comfy")));
                case "comfy":
                    return await Task.Run(() => engine.InspectComfy(Arg(msg, "path")));
                case "list":
                    return engine.List(Arg(msg, "path"));
                case "mkdir":
                    return engine.MakeFolder(Arg(msg, "path"), Arg(msg, "name"));
                case "install":
                    return Start(t => engine.Install(Arg(msg, "install"), Arg(msg, "library"), Arg(msg, "data"), Arg(msg, "comfy"), Arg(msg, "lang"), Report, t));
                case "uninstall":
                    return Start(t => engine.Uninstall(uninstallTarget, Report, t));
                case "cancel":
                    work?.Cancel();
                    return true;
                case "open":
                    engine.OpenModelDepot(Arg(msg, "install"));
                    return true;
                case "window":
                    var action = Arg(msg, "action");
                    if (action == "min") WindowState = FormWindowState.Minimized;
                    else if (action == "close") { closing = true; Close(); }
                    return true;
            }
            throw new SetupProblem("bad_request", cmd ?? "");
        }

        // The install or removal runs on its own; its progress and its end arrive as events.
        object Start(Action<CancellationToken> job)
        {
            if (work != null) throw new SetupProblem("busy");
            work = new CancellationTokenSource();
            var token = work.Token;
            Task.Run(() =>
            {
                try
                {
                    job(token);
                    Post(new Dictionary<string, object> { { "event", "done" } });
                }
                catch (OperationCanceledException)
                {
                    Post(new Dictionary<string, object> { { "event", "cancelled" } });
                }
                catch (SetupProblem p)
                {
                    Post(new Dictionary<string, object> { { "event", "failed" }, { "code", p.Code }, { "detail", p.Detail } });
                }
                catch (Exception ex)
                {
                    Post(new Dictionary<string, object> { { "event", "failed" }, { "code", "failed" }, { "detail", ex.Message } });
                }
                finally
                {
                    work = null;
                }
            });
            return true;
        }

        void Report(Dictionary<string, object> progress)
        {
            progress["event"] = "progress";
            Post(progress);
        }
    }
}
