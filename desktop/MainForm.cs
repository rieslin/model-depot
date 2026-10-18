using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Linq;
using System.Net;
using System.Net.Http;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;
using Microsoft.Win32;

namespace ModelDepot
{
    // The window: Windows's own frame without its title bar, with Model Depot's page filling it. The page's top bar is
    // where the window is dragged, and it draws the three window buttons, which post "min", "max" and "close" here.
    // Closing hides the window in the notification area and leaves Model Depot running (its API keeps answering,
    // downloads go on). The icon there opens the window again; its menu exits, which stops the background program too.
    // The menu and the window's questions are drawn in the page's own style (Look.cs).
    public class MainForm : Form
    {
        const int TopGripDip = 4;                                       // the strip above the page to resize by, at 96 dpi
        static readonly Size LeastDip = new Size(1100, 700);            // below this the top bar no longer fits in one row
        static readonly Size FirstDip = new Size(1440, 900);            // the page's reference size
        static readonly Color Paper = Color.FromArgb(0xf3, 0xf3, 0xf2); // the page's --paper
        static readonly Color Ground = Color.FromArgb(0xe7, 0xe8, 0xe7); // the page's --ground: the loading screen's, and the page's first paint

        readonly WebView2 web = new WebView2();
        readonly NotifyIcon tray = new NotifyIcon();
        readonly EventWaitHandle showSignal, quitSignal;
        readonly string root;           // the program folder (it holds run.py)
        readonly string dataDir;        // MODEL_DEPOT_DATA, else the setup's choice, else data\ in the program folder
        readonly string stateDir;       // this window's own files: its size and place, its browser profile
        readonly HttpClient http = new HttpClient(new HttpClientHandler { UseProxy = false }) { Timeout = TimeSpan.FromSeconds(3) };
        string appUrl = "";             // the running program's address, ending in "/"
        bool pageReady;                 // the program's own page is showing
        bool starting;                  // a start is under way
        bool exiting;                   // leaving for real: the menu's Exit, a quit signal, Windows shutting down
        bool loaderShown;               // the loading screen (desktop\loader.html) is the page in the window
        TaskCompletionSource<bool> loaderLoaded, handed;     // it has loaded; it has played its hand-over
        FormWindowState shownState = FormWindowState.Normal;    // the state to come back to after minimizing
        Size pageDip = FirstDip;        // the page's size in the normal state, at 96 dpi: what the user last gave it
        readonly System.Windows.Forms.Timer fitTimer = new System.Windows.Forms.Timer { Interval = 800 };

        public MainForm(EventWaitHandle show, EventWaitHandle quit)
        {
            showSignal = show;
            quitSignal = quit;
            root = FindRoot();
            // MODEL_DEPOT_DATA, else the folder the Windows setup chose (install.json next to run.py), else data\
            var data = Environment.GetEnvironmentVariable("MODEL_DEPOT_DATA");
            if (string.IsNullOrWhiteSpace(data))
            {
                var info = ReadJson(Path.Combine(root, "install.json"));
                if (info != null && info.TryGetValue("data_dir", out var chosen)) data = chosen as string;
            }
            dataDir = string.IsNullOrWhiteSpace(data) ? Path.Combine(root, "data") : data;
            stateDir = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Model Depot" + Program.Profile);
            Words.Load(root, dataDir);

            Text = "Model Depot";
            Icon = LoadIcon(Size.Empty);
            // Windows's own frame, so that the window is resized by its edges as any other is (its sizing borders lie
            // just outside the visible edge); the title bar is given to the page (WM_NCCALCSIZE below)
            FormBorderStyle = FormBorderStyle.Sizable;
            // the window and the browser are the loading screen's ground until the first page is up: WebView2 paints
            // black while it starts, so it stays hidden until its first page has loaded (OnNavigationCompleted)
            BackColor = Ground;
            StartPosition = FormStartPosition.Manual;
            web.Dock = DockStyle.Fill;
            web.DefaultBackgroundColor = Ground;
            web.Visible = false;
            Controls.Add(web);
            fitTimer.Tick += (s, e) => { fitTimer.Stop(); FitToScreen(); };
            SystemEvents.DisplaySettingsChanged += OnDisplayChanged;
            SetUpTray();
        }

        // The screen's real scale is known only once the window exists: sizes in 96-dpi units are turned into pixels
        // here, not in the constructor (at 175% a window sized before would hold only 57% of the page it should).
        protected override void OnHandleCreated(EventArgs e)
        {
            base.OnHandleCreated(e);
            ApplyMinimum();
            if (!RestoreSavedBounds()) PlaceFirstTime(Screen.FromHandle(Handle).WorkingArea);
        }

        // The scale of the screen the window is on (96 = 100%); asked of Windows itself once the window exists.
        int Dpi => IsHandleCreated ? (int)GetDpiForWindow(Handle) : DeviceDpi;

        int TopGrip => TopGripDip * Dpi / 96;

        // The frame Windows keeps around the page at a scale: its sizing borders left, right and below (the top one
        // is the page's, with the strip above it).
        static Size FrameAt(int dpi)
        {
            int padded = GetSystemMetricsForDpi(92 /* SM_CXPADDEDBORDER */, (uint)dpi);
            return new Size(GetSystemMetricsForDpi(32 /* SM_CXFRAME */, (uint)dpi) + padded,
                            GetSystemMetricsForDpi(33 /* SM_CYFRAME */, (uint)dpi) + padded);
        }

        // The window's size, frame included, for a page of `dip` (96-dpi units) at a scale.
        static Size WindowSizeFor(Size dip, int dpi)
        {
            var frame = FrameAt(dpi);
            return new Size((int)Math.Round(dip.Width * dpi / 96.0) + 2 * frame.Width, (int)Math.Round(dip.Height * dpi / 96.0) + frame.Height);
        }

        // The page's size now, in 96-dpi units.
        Size PageDip => new Size((int)Math.Round(ClientSize.Width * 96.0 / Dpi), (int)Math.Round(ClientSize.Height * 96.0 / Dpi));

        // Restored from maximized, Windows puts the window's size back by itself; WinForms then sets it once more, from
        // the page's size and a title bar the window does not have, and the window grew by one at every restore. While
        // it does so, the size Windows gave is kept.
        bool restoring;
        int lastSizeType;               // the kind of the last WM_SIZE: 0 restored, 1 minimized, 2 maximized

        protected override void SetBoundsCore(int x, int y, int width, int height, BoundsSpecified specified)
        {
            if (restoring && GetWindowRect(Handle, out var r)) { width = r.Right - r.Left; height = r.Bottom - r.Top; }
            base.SetBoundsCore(x, y, width, height, specified);
        }

        void ApplyMinimum()
        {
            var wa = Screen.FromHandle(Handle).WorkingArea;
            var least = WindowSizeFor(LeastDip, Dpi);
            MinimumSize = new Size(Math.Min(least.Width, wa.Width), Math.Min(least.Height, wa.Height));
        }

        // A rectangle moved, and shrunk if need be, to lie within a screen's work area.
        static Rectangle Within(Rectangle r, Rectangle wa)
        {
            int w = Math.Min(r.Width, wa.Width), h = Math.Min(r.Height, wa.Height);
            int x = Math.Max(wa.Left, Math.Min(r.Left, wa.Right - w)), y = Math.Max(wa.Top, Math.Min(r.Top, wa.Bottom - h));
            return new Rectangle(x, y, w, h);
        }

        // After the screen's scale or the set of screens changed (a remote desktop's virtual screen coming and going,
        // say), the window takes the size the user gave it again, at the scale it is at now. Windows scales a window
        // to the other screen and back by itself, but it was seen to leave Model Depot at the other screen's scale.
        void FitToScreen()
        {
            if (!IsHandleCreated) return;
            ApplyMinimum();
            if (WindowState != FormWindowState.Normal) return;
            var size = PageDip == pageDip ? Size : WindowSizeFor(pageDip, Dpi);     // still right: left as it is, to the pixel
            var r = Within(new Rectangle(Location, size), Screen.FromHandle(Handle).WorkingArea);
            if (r != Bounds) Bounds = r;
        }

        protected override void OnDpiChanged(DpiChangedEventArgs e)
        {
            base.OnDpiChanged(e);
            if (IsHandleCreated) BeginInvoke((Action)FitToScreen);
        }

        void OnDisplayChanged(object sender, EventArgs e)
        {
            // raised on another thread, and more than once while the screens settle: fitted once, a moment after
            if (IsHandleCreated) BeginInvoke((Action)(() => { fitTimer.Stop(); fitTimer.Start(); }));
        }

        // Every size of the icon file: the exe's own icon resource yields only one.
        static Icon LoadIcon(Size size)
        {
            try
            {
                using (var s = typeof(MainForm).Assembly.GetManifestResourceStream("ModelDepot.ico"))
                    return size.IsEmpty ? new Icon(s) : new Icon(s, size);
            }
            catch { return SystemIcons.Application; }
        }

        // ---- the icon in the notification area ---------------------------------------------------------------------
        void SetUpTray()
        {
            tray.Icon = LoadIcon(SystemInformation.SmallIconSize);
            tray.Text = "Model Depot";
            tray.MouseClick += (s, e) =>
            {
                if (e.Button == MouseButtons.Left) { ShowFromTray(); return; }
                if (e.Button != MouseButtons.Right) return;
                // made when it opens, in the language chosen at that moment and at the scale of the screen it opens on
                new DepotMenu(new[] { Words.T("tray_open"), Words.T("tray_exit") },
                    new Action[] { ShowFromTray, ExitFromTray }, Look.DpiAtCursor()).Open();
            };
            tray.Visible = true;
        }

        void HideToTray()
        {
            if (!Visible) return;
            SaveBounds();
            Hide();
            // the first time only, say where the window went
            var mark = Path.Combine(stateDir, "tray-hint.txt");
            if (File.Exists(mark)) return;
            tray.ShowBalloonTip(8000, "Model Depot", Words.T("tray_hint"), ToolTipIcon.None);
            try { Directory.CreateDirectory(stateDir); File.WriteAllText(mark, DateTime.Now.ToString("s")); } catch { /* shown again next time */ }
        }

        public void ShowFromTray()
        {
            FitToScreen();      // the screens may have changed while it was away
            if (!Visible) Show();
            if (WindowState == FormWindowState.Minimized) WindowState = shownState;
            Activate();
            // the program may have stopped meanwhile (Stop.cmd, a crash): start it again
            if (!starting && web.CoreWebView2 != null) _ = CheckProgram();
        }

        async Task CheckProgram()
        {
            if (await RunningUrl() == null) await StartAsync();
        }

        async void ExitFromTray()
        {
            if (!DepotDialog.Ask(this, Words.T("exit_title"), Words.T("exit_body"), Words.T("exit_ok"), Words.T("cancel"))) return;
            await StopProgram();
            QuitNow();
        }

        // Asks the program to stop; it finishes what it is writing and ends on its own.
        async Task StopProgram()
        {
            var url = appUrl.Length > 0 ? appUrl : await RunningUrl();
            if (string.IsNullOrEmpty(url)) return;
            try
            {
                using (var body = new StringContent("{}", Encoding.UTF8, "application/json"))
                using (await http.PostAsync(url + "api/admin/shutdown", body)) { }
            }
            catch { /* not running */ }
        }

        public void QuitNow()
        {
            exiting = true;
            tray.Visible = false;
            Close();
        }

        void ListenForSignals()
        {
            var signals = new WaitHandle[] { showSignal, quitSignal };
            try
            {
                while (true)
                {
                    int which = WaitHandle.WaitAny(signals);
                    if (which == 0) BeginInvoke((Action)ShowFromTray);
                    else { BeginInvoke((Action)QuitNow); return; }
                }
            }
            catch { /* the app is closing */ }
        }

        // ---- the window's frame ------------------------------------------------------------------------------------
        protected override void OnSizeChanged(EventArgs e)
        {
            // the strip to resize by from above exists only while the window has a size of its own
            var top = WindowState == FormWindowState.Normal ? TopGrip : 0;
            if (Padding.Top != top) Padding = new Padding(0, top, 0, 0);
            if (WindowState != FormWindowState.Minimized) shownState = WindowState;
            base.OnSizeChanged(e);
            PostWindowState();
        }

        protected override void WndProc(ref Message m)
        {
            const int WM_SIZE = 0x0005, WM_WINDOWPOSCHANGED = 0x0047, WM_NCCALCSIZE = 0x0083, WM_NCHITTEST = 0x0084, WM_NCACTIVATE = 0x0086;
            const int WM_EXITSIZEMOVE = 0x0232, HTCLIENT = 1, HTTOP = 12, HTTOPLEFT = 13, HTTOPRIGHT = 14;
            switch (m.Msg)
            {
                case WM_WINDOWPOSCHANGED:
                {
                    // WinForms puts its own size back while it handles the window's coming back from maximized or
                    // minimized; its WindowState already says normal by then, so the last WM_SIZE tells what it was
                    var was = restoring;
                    restoring = lastSizeType != 0 && !IsZoomed(Handle) && !IsIconic(Handle);
                    try { base.WndProc(ref m); } finally { restoring = was; }
                    return;
                }
                case WM_SIZE:
                    base.WndProc(ref m);
                    lastSizeType = m.WParam.ToInt32();      // 0 restored, 1 minimized, 2 maximized
                    return;
                case WM_NCCALCSIZE when m.WParam != IntPtr.Zero:
                {
                    // Windows takes the sizing borders and the title bar off the window to leave the page's place; the
                    // title bar is given back to the page. Maximized, the window reaches past the screen's edges by its
                    // borders, so the page starts that far below the window's top.
                    var before = (NCCALCSIZE_PARAMS)Marshal.PtrToStructure(m.LParam, typeof(NCCALCSIZE_PARAMS));
                    base.WndProc(ref m);
                    var after = (NCCALCSIZE_PARAMS)Marshal.PtrToStructure(m.LParam, typeof(NCCALCSIZE_PARAMS));
                    after.rgrc0.Top = before.rgrc0.Top + (IsZoomed(Handle) ? after.rgrc0.Left - before.rgrc0.Left : 0);
                    Marshal.StructureToPtr(after, m.LParam, false);
                    m.Result = IntPtr.Zero;
                    return;
                }
                case WM_NCHITTEST:
                {
                    // the borders answer for themselves; the strip above the page resizes from the top (wider at the
                    // corners, so easier to catch)
                    base.WndProc(ref m);
                    if (m.Result.ToInt64() == HTCLIENT && WindowState == FormWindowState.Normal)
                    {
                        var lp = m.LParam.ToInt64();
                        var p = PointToClient(new Point((short)(lp & 0xFFFF), (short)((lp >> 16) & 0xFFFF)));
                        int corner = TopGrip * 4;
                        if (p.Y < TopGrip)
                            m.Result = (IntPtr)(p.X < corner ? HTTOPLEFT : p.X >= ClientSize.Width - corner ? HTTOPRIGHT : HTTOP);
                    }
                    return;
                }
                case WM_NCACTIVATE:
                    // there is no title bar to paint as active or inactive: Windows is asked not to draw one
                    m.LParam = new IntPtr(-1);
                    base.WndProc(ref m);
                    return;
                case WM_EXITSIZEMOVE:
                    base.WndProc(ref m);
                    // the user has moved or resized the window: this is the size to keep
                    if (WindowState == FormWindowState.Normal) pageDip = PageDip;
                    return;
            }
            base.WndProc(ref m);
        }

        protected override void OnFormClosing(FormClosingEventArgs e)
        {
            if (!exiting && e.CloseReason != CloseReason.WindowsShutDown)
            {
                // the close button, Alt+F4, the taskbar: the window goes to the notification area, the program runs on
                e.Cancel = true;
                HideToTray();
                return;
            }
            SaveBounds();
            tray.Visible = false;
            base.OnFormClosing(e);
        }

        protected override void Dispose(bool disposing)
        {
            if (disposing)
            {
                SystemEvents.DisplaySettingsChanged -= OnDisplayChanged;
                fitTimer.Dispose();
                tray.Dispose();
                http.Dispose();
            }
            base.Dispose(disposing);
        }

        // ---- starting ----------------------------------------------------------------------------------------------
        protected override async void OnLoad(EventArgs e)
        {
            base.OnLoad(e);
            new Thread(ListenForSignals) { IsBackground = true, Name = "signals" }.Start();
            try
            {
                Directory.CreateDirectory(stateDir);
                // the browser profile lives with this window's files, not beside the exe
                var env = await CoreWebView2Environment.CreateAsync(null, Path.Combine(stateDir, "WebView2"));
                await web.EnsureCoreWebView2Async(env);
            }
            catch (Exception ex)
            {
                DepotDialog.Tell(this, Words.T("app_no_webview_title"), Words.T("app_no_webview", ex.Message), Words.T("ok"));
                QuitNow();
                return;
            }
            var core = web.CoreWebView2;
            core.Settings.IsNonClientRegionSupportEnabled = true;   // the page's top bar moves the window (CSS app-region)
            core.Settings.AreDevToolsEnabled = Environment.GetEnvironmentVariable("MODEL_DEPOT_DEVTOOLS") == "1";
            core.Settings.IsStatusBarEnabled = false;
            // a program's window, not a browser tab: no page zoom, no swiping back, none of the browser's own keys
            // (F5, Ctrl+F, Ctrl+P ...); copying and pasting are not among them. The page reloads itself on F5.
            core.Settings.IsZoomControlEnabled = false;
            core.Settings.IsPinchZoomEnabled = false;
            core.Settings.IsSwipeNavigationEnabled = false;
            core.Settings.AreBrowserAcceleratorKeysEnabled = false;
            core.ContextMenuRequested += OnContextMenu;
            core.DocumentTitleChanged += (s, a) => Text = string.IsNullOrEmpty(core.DocumentTitle) ? "Model Depot" : core.DocumentTitle;
            core.WebMessageReceived += OnWebMessage;
            core.NavigationStarting += OnNavigationStarting;
            core.NavigationCompleted += OnNavigationCompleted;
            core.NewWindowRequested += (s, a) => { a.Handled = true; OpenOutside(a.Uri); };
            await StartAsync();
        }

        async Task StartAsync()
        {
            if (starting) return;
            starting = true;
            pageReady = false;
            // a program already running answers at once: its page straight away; else the loading screen while it starts
            var asking = RunningUrl();
            var url = await Task.WhenAny(asking, Task.Delay(250)) == asking ? asking.Result : null;
            bool loader = false;
            if (url == null)
            {
                loader = ShowLoader();
                if (!loader) ShowNote(Words.T("app_starting"));
                url = await asking;
                if (url == null && await LaunchProgram()) url = await RunningUrl();
            }
            starting = false;
            if (url == null) { await ShowFailure(); return; }
            appUrl = url;
            if (loader) await HandOver();
            web.CoreWebView2.Navigate(appUrl);
        }

        // ---- the loading screen ------------------------------------------------------------------------------------
        // desktop\loader.html (made by desktop\loader-src\make.py): the library's index of base models with its red
        // marker walking down it, until the program answers; then it hands over to the page's first paint. It is told
        // the language, and the base models the library showed last time (the page posts them, OnWebMessage).
        bool ShowLoader()
        {
            string html;
            try { html = File.ReadAllText(Path.Combine(root, "desktop", "loader.html"), Encoding.UTF8); }
            catch { return false; }
            var given = "<script>window.LOADER_WORDS={\"lang\":\"" + Words.Lang + "\"};window.LOADER_FAMILIES=" + ReadFamilies() + ";</script>";
            int at = html.IndexOf("<head>", StringComparison.OrdinalIgnoreCase);
            html = at < 0 ? given + html : html.Insert(at + "<head>".Length, given);
            loaderShown = true;
            loaderLoaded = new TaskCompletionSource<bool>();
            web.CoreWebView2.NavigateToString(html);
            return true;
        }

        string FamiliesPath => Path.Combine(stateDir, "loader-families.json");

        // [[code, name, architecture], ...] as JSON a script can hold (the serializer writes < > & as \u escapes), or null
        string ReadFamilies()
        {
            try
            {
                var js = new JavaScriptSerializer();
                var families = CleanFamilies(js.DeserializeObject(File.ReadAllText(FamiliesPath, Encoding.UTF8)));
                return families.Count > 0 ? js.Serialize(families) : "null";
            }
            catch { return "null"; }
        }

        // Only a short list of short strings is kept: the page's answer, not anything else.
        static List<string[]> CleanFamilies(object value)
        {
            var kept = new List<string[]>();
            // (arrays arrive as object[] from DeserializeObject, as an ArrayList inside a typed Deserialize)
            if (!(value is System.Collections.IList rows)) return kept;
            foreach (var row in rows.Cast<object>().Take(30))
            {
                if (!(row is System.Collections.IList cells) || cells.Count < 2) continue;
                var texts = cells.Cast<object>().Take(3).Select(c => c as string ?? "").ToArray();
                if (texts[0].Length == 0 || texts.Any(x => x.Length > 40)) continue;
                kept.Add(texts);
            }
            return kept;
        }

        void SaveFamilies(object value)
        {
            var families = CleanFamilies(value);
            if (families.Count == 0) return;
            try
            {
                Directory.CreateDirectory(stateDir);
                var tmp = FamiliesPath + ".tmp";
                File.WriteAllText(tmp, new JavaScriptSerializer().Serialize(families), new UTF8Encoding(false));
                if (File.Exists(FamiliesPath)) File.Delete(FamiliesPath);
                File.Move(tmp, FamiliesPath);
            }
            catch { /* kept the next time */ }
        }

        // The loading screen has loaded (a call made before would reach the page it replaces), or it never will.
        async Task<bool> LoaderUp()
        {
            var wait = loaderLoaded;
            return loaderShown && wait != null && await Task.WhenAny(wait.Task, Task.Delay(4000)) == wait.Task;
        }

        // Play the hand-over and wait for its last frame (the page posts "handed"; never more than 1.5 s).
        async Task HandOver()
        {
            if (!await LoaderUp()) return;
            handed = new TaskCompletionSource<bool>();
            try { await web.CoreWebView2.ExecuteScriptAsync("window.LOADER && LOADER.ready()"); }
            catch { return; }
            await Task.WhenAny(handed.Task, Task.Delay(1500));
        }

        // The address of the running program, from the file it writes when it starts, if Model Depot answers there.
        async Task<string> RunningUrl()
        {
            var info = ReadJson(Path.Combine(dataDir, "server.json"));
            if (info == null || !info.TryGetValue("port", out var port)) return null;
            var host = info.TryGetValue("host", out var h) ? h as string : null;
            if (string.IsNullOrEmpty(host) || host == "0.0.0.0" || host == "::") host = "127.0.0.1";
            if (host.Contains(":")) host = "[" + host + "]";
            string url;
            try { url = $"http://{host}:{Convert.ToInt32(port)}/"; } catch { return null; }
            try
            {
                using (var r = await http.GetAsync(url + "api/v1/status"))
                {
                    if (!r.IsSuccessStatusCode) return null;
                    var status = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(await r.Content.ReadAsStringAsync());
                    return status != null && status.TryGetValue("app", out var app) && app as string == "Model Depot" ? url : null;
                }
            }
            catch { return null; }
        }

        // The same start as Start.cmd, minus the browser: scripts\start.ps1 finds a Python, starts the program hidden
        // and waits until it answers. On a computer where nothing is prepared yet it asks before downloading anything.
        async Task<bool> LaunchProgram()
        {
            var script = Path.Combine(root, "scripts", "start.ps1");
            if (!File.Exists(script)) return false;
            var psi = new ProcessStartInfo("powershell.exe", $"-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File \"{script}\" -Quiet")
            {
                UseShellExecute = false, CreateNoWindow = true, WorkingDirectory = root,
            };
            try
            {
                using (var p = Process.Start(psi))
                    await Task.Run(() => p.WaitForExit(180000));
            }
            catch { return false; }
            return true;
        }

        bool IsApp(string uri) => appUrl.Length > 0 && (uri ?? "").StartsWith(appUrl, StringComparison.OrdinalIgnoreCase);

        void OnNavigationStarting(object sender, CoreWebView2NavigationStartingEventArgs e)
        {
            // the window shows Model Depot's own page and nothing else; any other address opens in the browser
            var uri = e.Uri ?? "";
            if (IsApp(uri) || uri == "about:blank" || uri.StartsWith("data:", StringComparison.OrdinalIgnoreCase)) return;
            e.Cancel = true;
            OpenOutside(uri);
        }

        void OnNavigationCompleted(object sender, CoreWebView2NavigationCompletedEventArgs e)
        {
            if (!web.Visible) web.Visible = true;
            if (!IsApp(web.CoreWebView2.Source)) { if (loaderShown) loaderLoaded?.TrySetResult(true); return; }
            loaderShown = false;
            if (!e.IsSuccess) { _ = ShowFailure(); return; }
            pageReady = true;
            web.Focus();        // keys go to the page straight away
            PostWindowState();
        }

        static void OpenOutside(string uri)
        {
            if (uri != null && (uri.StartsWith("http://", StringComparison.OrdinalIgnoreCase) || uri.StartsWith("https://", StringComparison.OrdinalIgnoreCase)))
                try { Process.Start(new ProcessStartInfo(uri) { UseShellExecute = true }); } catch { /* no browser */ }
        }

        static void OpenFolder(string path)
        {
            try { Directory.CreateDirectory(path); Process.Start("explorer.exe", "\"" + path + "\""); } catch { /* nothing to show */ }
        }

        // The right-click menu keeps what a text box and a picture offer and drops the browser's own entries (back,
        // reload, save as, print, inspect ...).
        static readonly HashSet<string> MenuKept = new HashSet<string>
            { "cut", "copy", "paste", "pasteAndMatchStyle", "selectAll", "undo", "redo", "copyImage", "copyLinkLocation" };

        void OnContextMenu(object sender, CoreWebView2ContextMenuRequestedEventArgs e)
        {
            var items = e.MenuItems;
            for (int i = items.Count - 1; i >= 0; i--)
                if (items[i].Kind == CoreWebView2ContextMenuItemKind.Separator || !MenuKept.Contains(items[i].Name)) items.RemoveAt(i);
            if (items.Count == 0) e.Handled = true;
        }

        // ---- messages from the page --------------------------------------------------------------------------------
        void OnWebMessage(object sender, CoreWebView2WebMessageReceivedEventArgs e)
        {
            var src = e.Source ?? "";
            if (!IsApp(src) && src != "about:blank" && !src.StartsWith("data:", StringComparison.OrdinalIgnoreCase)) return;
            string cmd = null;
            Dictionary<string, object> message = null;
            try { message = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(e.WebMessageAsJson); cmd = message["cmd"] as string; }
            catch { /* not one of ours */ }
            switch (cmd)
            {
                case "handed": handed?.TrySetResult(true); break;     // the loading screen's last frame
                case "families": if (IsApp(src) && message.TryGetValue("families", out var f)) SaveFamilies(f); break;
                case "min": WindowState = FormWindowState.Minimized; break;
                case "max": WindowState = WindowState == FormWindowState.Maximized ? FormWindowState.Normal : FormWindowState.Maximized; break;
                case "close": HideToTray(); break;
                case "window": PostWindowState(); break;    // the page asks once it listens (a post sent before is lost)
                case "retry": _ = StartAsync(); break;
                case "logs": OpenFolder(Path.Combine(dataDir, "logs")); break;
            }
        }

        // The page draws the maximize button: tell it which of its two glyphs to show.
        void PostWindowState()
        {
            if (!pageReady && !loaderShown) return;
            try { web.CoreWebView2.PostWebMessageAsJson("{\"cmd\":\"window\",\"maximized\":" + (WindowState == FormWindowState.Maximized ? "true" : "false") + "}"); }
            catch { /* the page is going away */ }
        }

        // ---- the page shown before the program answers -------------------------------------------------------------
        // In the program's own look: paper, ink, the wordmark with its red O, square buttons.
        void ShowNote(string message, string extra = "")
        {
            loaderShown = false;
            const string glyphs =
                "<button onclick=\"post('min')\" aria-label='min'><svg viewBox='0 0 20 20'><path d='M4 10.5h12' stroke='currentColor' stroke-width='1.6'/></svg></button>" +
                "<button onclick=\"post('max')\" aria-label='max'><svg viewBox='0 0 20 20'><path d='M4.75 4.75h10.5v10.5H4.75z' fill='none' stroke='currentColor' stroke-width='1.6'/></svg></button>" +
                "<button onclick=\"post('close')\" aria-label='close'><svg viewBox='0 0 20 20'><path d='M5 5l10 10M15 5L5 15' stroke='currentColor' stroke-width='1.8' stroke-linecap='square'/></svg></button>";
            var css = FontFace() +
                "html,body{margin:0;height:100%;background:#f3f3f2;color:#585b5e;font:14px/1.5 'Segoe UI','Microsoft YaHei UI','Microsoft YaHei',sans-serif;" +
                "-webkit-app-region:drag;app-region:drag;user-select:none;overflow:hidden}" +
                ".box{height:100%;display:grid;place-content:center;justify-items:center;gap:20px;text-align:center}" +
                ".mark{font:900 46px/0.86 Chivo,'Segoe UI Black','Segoe UI',sans-serif;letter-spacing:.01em;color:#161718;white-space:nowrap}" +
                ".mark i{font-style:normal;color:#e60000}" +
                ".win{position:fixed;top:0;right:0;display:flex;-webkit-app-region:no-drag;app-region:no-drag}" +
                ".win button{display:grid;place-items:center;width:46px;height:40px;padding:0;border:0;background:none;color:#393b3d;cursor:pointer}" +
                ".win button:hover{background:#161718;color:#fff}.win svg{width:14px;height:14px}" +
                "pre{max-width:80vw;max-height:40vh;overflow:auto;margin:0;padding:10px 12px;white-space:pre-wrap;text-align:left;color:#393b3d;" +
                "background:#f9f9f8;border:1px solid #c5c7c8;font:12px/1.5 Consolas,monospace;-webkit-app-region:no-drag;app-region:no-drag;user-select:text}" +
                ".act{display:flex;gap:10px;-webkit-app-region:no-drag;app-region:no-drag}" +
                ".act button{min-height:40px;padding:0 18px;border:1.5px solid #161718;background:transparent;color:#161718;" +
                "font:600 14px 'Segoe UI','Microsoft YaHei UI',sans-serif;cursor:pointer}" +
                ".act button:hover,.act button.solid{background:#161718;color:#fff}.act button.solid:hover{background:#2b2e31}";
            var html = "<!doctype html><html><head><meta charset='utf-8'><style>" + css + "</style></head><body>" +
                "<div class='win'>" + glyphs + "</div>" +
                "<div class='box'><div class='mark'>MODEL DEP<i>O</i>T</div><div>" + Html(message) + "</div>" + extra + "</div>" +
                "<script>function post(c){chrome.webview.postMessage({cmd:c})}</script></body></html>";
            web.CoreWebView2.NavigateToString(html);
        }

        async Task ShowFailure()
        {
            pageReady = false;
            var tail = "";
            foreach (var name in new[] { "stderr.log", "start.log" })
            {
                var path = Path.Combine(dataDir, "logs", name);
                try
                {
                    if (File.Exists(path) && new FileInfo(path).Length > 0)
                        tail += string.Join("\n", File.ReadAllLines(path, Encoding.UTF8).Reverse().Take(12).Reverse()) + "\n";
                }
                catch { /* the log is locked or gone */ }
            }
            // on the loading screen: its motion stops on a calm frame above the same note and buttons
            if (await LoaderUp())
            {
                var extra = (tail.Length > 0 ? "<pre>" + Html(tail.Trim()) + "</pre>" : "") +
                    "<div class=\"act\"><button type=\"button\" class=\"solid\" data-cmd=\"retry\">" + Html(Words.T("app_retry")) + "</button>" +
                    "<button type=\"button\" data-cmd=\"logs\">" + Html(Words.T("app_logs")) + "</button></div>";
                var js = new JavaScriptSerializer();
                try
                {
                    await web.CoreWebView2.ExecuteScriptAsync("LOADER.fail(" + js.Serialize(Words.T("app_failed")) + "," + js.Serialize(extra) + ")");
                    return;
                }
                catch { /* the plain note below */ }
            }
            ShowNote(Words.T("app_failed"),
                (tail.Length > 0 ? "<pre>" + Html(tail.Trim()) + "</pre>" : "") +
                "<div class='act'><button class='solid' onclick=\"post('retry')\">" + Html(Words.T("app_retry")) + "</button>" +
                "<button onclick=\"post('logs')\">" + Html(Words.T("app_logs")) + "</button></div>");
        }

        // The wordmark's typeface, from the program's own font files, so the note shows the real name.
        string FontFace()
        {
            try
            {
                var font = Path.Combine(root, "web", "fonts", "Chivo-latin.woff2");
                return "@font-face{font-family:Chivo;src:url(data:font/woff2;base64," + Convert.ToBase64String(File.ReadAllBytes(font)) +
                       ") format('woff2');font-weight:100 900}";
            }
            catch { return ""; }
        }

        static string Html(string s) => WebUtility.HtmlEncode(s ?? "");

        // ---- where things are --------------------------------------------------------------------------------------
        static string FindRoot()
        {
            // the exe sits in <program folder>\desktop\app
            var dir = new DirectoryInfo(Path.GetDirectoryName(Application.ExecutablePath));
            for (var d = dir; d != null; d = d.Parent)
                if (File.Exists(Path.Combine(d.FullName, "run.py"))) return d.FullName;
            return dir.FullName;
        }

        static Dictionary<string, object> ReadJson(string path)
        {
            try { return File.Exists(path) ? new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(File.ReadAllText(path, Encoding.UTF8)) : null; }
            catch { return null; }
        }

        // ---- remembered size and place -----------------------------------------------------------------------------
        // window.txt holds "2 x y width height maximized": the place in screen pixels and the page's size in 96-dpi
        // units, so that the window keeps its size whatever the screen's scale. The first version kept pixels, and a
        // change of scale while the window was away brought it back at 57% of its size; such a file is not used.
        string BoundsFile => Path.Combine(stateDir, "window.txt");

        bool RestoreSavedBounds()
        {
            try
            {
                var v = File.ReadAllText(BoundsFile).Split(' ').Select(int.Parse).ToArray();
                if (v.Length < 6 || v[0] != 2) return false;
                var dip = new Size(Math.Max(v[3], LeastDip.Width), Math.Max(v[4], LeastDip.Height));
                var r = new Rectangle(new Point(v[1], v[2]), WindowSizeFor(dip, Dpi));
                var screen = Screen.AllScreens.FirstOrDefault(s => s.WorkingArea.IntersectsWith(r));
                if (screen == null) return false;
                pageDip = dip;
                Bounds = Within(r, screen.WorkingArea);
                if (v[5] == 1) WindowState = FormWindowState.Maximized;
                return true;
            }
            catch { /* the first start, or the file is unreadable */ }
            return false;
        }

        // The first start: the page's reference size, 1440 x 900, in the middle of the screen; maximized where the
        // screen has less room than that (and then a little smaller than the screen when it is restored).
        void PlaceFirstTime(Rectangle wa)
        {
            var room = WindowSizeFor(new Size(FirstDip.Width + 80, FirstDip.Height + 60), Dpi);
            bool fits = wa.Width >= room.Width && wa.Height >= room.Height;
            pageDip = fits ? FirstDip : new Size(Math.Max(LeastDip.Width, wa.Width * 96 / Dpi - 80), Math.Max(LeastDip.Height, wa.Height * 96 / Dpi - 60));
            var size = WindowSizeFor(pageDip, Dpi);
            Bounds = Within(new Rectangle(wa.Left + (wa.Width - size.Width) / 2, wa.Top + (wa.Height - size.Height) / 2, size.Width, size.Height), wa);
            if (!fits) WindowState = FormWindowState.Maximized;
        }

        void SaveBounds()
        {
            try
            {
                var place = WindowState == FormWindowState.Normal ? Bounds : RestoreBounds;
                var state = WindowState == FormWindowState.Minimized ? shownState : WindowState;
                Directory.CreateDirectory(stateDir);
                File.WriteAllText(BoundsFile, $"2 {place.X} {place.Y} {pageDip.Width} {pageDip.Height} {(state == FormWindowState.Maximized ? 1 : 0)}");
            }
            catch { /* not worth stopping for */ }
        }

        // ---- Win32 -------------------------------------------------------------------------------------------------
        [StructLayout(LayoutKind.Sequential)] struct RECT { public int Left, Top, Right, Bottom; }
        [StructLayout(LayoutKind.Sequential)] struct NCCALCSIZE_PARAMS { public RECT rgrc0, rgrc1, rgrc2; public IntPtr lppos; }
        [DllImport("user32.dll")] static extern uint GetDpiForWindow(IntPtr hwnd);
        [DllImport("user32.dll")] static extern int GetSystemMetricsForDpi(int nIndex, uint dpi);
        [DllImport("user32.dll")] static extern bool IsZoomed(IntPtr hwnd);
        [DllImport("user32.dll")] static extern bool GetWindowRect(IntPtr hwnd, out RECT rect);
        [DllImport("user32.dll")] static extern bool IsIconic(IntPtr hwnd);
    }
}
