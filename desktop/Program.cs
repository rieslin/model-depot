using System;
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Threading;
using System.Windows.Forms;

namespace ModelDepot
{
    static class Program
    {
        // A test window started with MODEL_DEPOT_PROFILE set is a program of its own: its own "only one" rule, signals,
        // remembered size and browser profile. Normally the variable is not set.
        internal static string Profile
        {
            get
            {
                var p = Environment.GetEnvironmentVariable("MODEL_DEPOT_PROFILE");
                return string.IsNullOrWhiteSpace(p) ? "" : "." + p.Trim();
            }
        }

        // The two signals a second start can send to the running app. scripts\stop.ps1 sends "quit" too: stopping
        // Model Depot also takes its icon out of the notification area.
        internal static string ShowSignalName => @"Local\ModelDepot.Desktop.Show" + Profile;
        internal static string QuitSignalName => @"Local\ModelDepot.Desktop.Quit" + Profile;

        // ModelDepot.exe            open the window (a second start brings the running one forward, from the tray too)
        // ModelDepot.exe --quit     close the running app without asking and wait for it to end; the background
        //                           program is left alone (used before replacing the exe, and by stop.ps1)
        [STAThread]
        static int Main(string[] args)
        {
            bool quit = Array.Exists(args, a => string.Equals(a, "--quit", StringComparison.OrdinalIgnoreCase));
            using (var mutex = new Mutex(true, @"Local\ModelDepot.Desktop" + Profile, out bool first))
            {
                if (!first)
                {
                    Signal(quit ? QuitSignalName : ShowSignalName);
                    if (quit) WaitForTheOther();
                    return 0;
                }
                if (quit) return 0;     // nothing is running

                Application.EnableVisualStyles();
                Application.SetCompatibleTextRenderingDefault(false);
                using (var show = new EventWaitHandle(false, EventResetMode.AutoReset, ShowSignalName))
                using (var quitSignal = new EventWaitHandle(false, EventResetMode.AutoReset, QuitSignalName))
                    Application.Run(new MainForm(show, quitSignal));
                GC.KeepAlive(mutex);
            }
            return 0;
        }

        static void Signal(string name)
        {
            // this start came from the user, so it may hand the right to come to the front to the running app
            AllowSetForegroundWindow(-1 /* ASFW_ANY */);
            try
            {
                using (var signal = EventWaitHandle.OpenExisting(name))
                    signal.Set();
            }
            catch { /* the other one is just closing */ }
        }

        [DllImport("user32.dll")] static extern bool AllowSetForegroundWindow(int processId);

        static void WaitForTheOther()
        {
            var me = Process.GetCurrentProcess();
            foreach (var p in Process.GetProcessesByName(me.ProcessName))
            {
                if (p.Id == me.Id) continue;
                try { p.WaitForExit(15000); } catch { /* gone already */ }
            }
        }
    }
}
