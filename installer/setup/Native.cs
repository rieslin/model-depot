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
