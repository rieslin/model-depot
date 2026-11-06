using System;
using System.Runtime.InteropServices;

namespace ModelDepotSetup
{
    static class Native
    {
        [DllImport("user32.dll")]
        public static extern uint GetDpiForWindow(IntPtr hwnd);

        [StructLayout(LayoutKind.Sequential)]
        public struct RECT { public int Left, Top, Right, Bottom; }

        [StructLayout(LayoutKind.Sequential)]
        public struct NCCALCSIZE_PARAMS
        {
            public RECT rgrc0, rgrc1, rgrc2;
            public IntPtr lppos;
        }

        // The user's Downloads folder where Windows keeps it (it can be moved elsewhere), or null.
        public static string DownloadsFolder()
        {
            var path = IntPtr.Zero;
            try { return SHGetKnownFolderPath(FolderDownloads, 0, IntPtr.Zero, out path) == 0 ? Marshal.PtrToStringUni(path) : null; }
            catch { return null; }
            finally { if (path != IntPtr.Zero) Marshal.FreeCoTaskMem(path); }
        }

        static readonly Guid FolderDownloads = new Guid("374DE290-123F-4565-9164-39C4925E467B");     // FOLDERID_Downloads

        [DllImport("shell32.dll")]
        static extern int SHGetKnownFolderPath([MarshalAs(UnmanagedType.LPStruct)] Guid rfid, uint dwFlags, IntPtr hToken, out IntPtr ppszPath);
    }

    // A problem the page names to the user: a code from the page's own word list, and what it concerns.
    sealed class SetupProblem : Exception
    {
        public readonly string Code;
        public readonly string Detail;

        public SetupProblem(string code, string detail = "") : base(code + " " + detail)
        {
            Code = code;
            Detail = detail ?? "";
        }
    }
}
