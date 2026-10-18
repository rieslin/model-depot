// The Model Depot setup: a window showing the setup's own page (setup\ui, in the program's own design) over WebView2,
// and the work behind it (Engine.cs). One exe for both directions:
//
//   ModelDepotSetup.exe                         installs, from the package folder it sits in (setup\ beside it)
//   ModelDepotSetup.exe /uninstall              run from <install>\uninstall: copies itself out and starts again
//   ModelDepotSetup.exe /uninstall "<install>"  removes the program files of that installation
//
// The two WebView2 assemblies and its native loader are inside the exe (resources), so the package folder holds
// nothing but the setup, its data and the models folder.
using System;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Windows.Forms;

namespace ModelDepotSetup
{
    static class Program
    {
        public static readonly string Temp = Path.Combine(Path.GetTempPath(), "ModelDepotSetup");

        [STAThread]
        static int Main(string[] args)
        {
            AppDomain.CurrentDomain.AssemblyResolve += (s, e) =>
            {
                var name = new AssemblyName(e.Name).Name + ".dll";
                using (var res = Assembly.GetExecutingAssembly().GetManifestResourceStream(name))
                {
                    if (res == null) return null;
                    var bytes = new byte[res.Length];
                    res.Read(bytes, 0, bytes.Length);
                    return Assembly.Load(bytes);
                }
            };
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);

            var exe = Application.ExecutablePath;
            var exeDir = Path.GetDirectoryName(exe);
            bool uninstall = args.Length > 0 && string.Equals(args[0], "/uninstall", StringComparison.OrdinalIgnoreCase);
            string target = uninstall && args.Length > 1 ? args[1] : null;

            if (uninstall && target == null)
            {
                // run from <install>\uninstall, which is about to be removed: a copy elsewhere does the work
                var install = Path.GetDirectoryName(exeDir);
                var away = Path.Combine(Path.GetTempPath(), "ModelDepotUninstall");
                try
                {
                    if (Directory.Exists(away)) Directory.Delete(away, true);
                    Engine.CopyTree(exeDir, away);
                    Process.Start(new ProcessStartInfo(Path.Combine(away, Path.GetFileName(exe)), "/uninstall \"" + install + "\"")
                    { UseShellExecute = false, WorkingDirectory = away });
                    return 0;
                }
                catch (Exception ex)
                {
                    MessageBox.Show(ex.Message, "Model Depot");
                    return 1;
                }
            }

            try
            {
                Directory.CreateDirectory(Temp);
                using (var res = Assembly.GetExecutingAssembly().GetManifestResourceStream("WebView2Loader.dll"))
                using (var file = File.Create(Path.Combine(Temp, "WebView2Loader.dll")))
                    res.CopyTo(file);
            }
            catch (IOException) { }     // another setup window has it open: the copy there is the same

            Application.Run(new SetupForm(exeDir, target));
            return 0;
        }
    }
}
