using System;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Runtime.InteropServices;
using System.Windows.Forms;

namespace ModelDepot
{
    // The window's own pieces drawn the way the page draws its own (DESIGN.md): paper ground, ink, square corners, the
    // body face, and buttons as the page's buttons are (a solid ink one for the action, a line one for cancelling). The
    // notification area's menu and the window's few questions use them, instead of Windows's grey ones.
    static class Look
    {
        public static readonly Color Paper = Color.FromArgb(0xf3, 0xf3, 0xf2);
        public static readonly Color Ink = Color.FromArgb(0x16, 0x17, 0x18);
        public static readonly Color InkHover = Color.FromArgb(0x2b, 0x2e, 0x31);
        public static readonly Color Ink3 = Color.FromArgb(0x58, 0x5b, 0x5e);
        public static readonly Color Rule = Color.FromArgb(0xc5, 0xc7, 0xc8);

        // A length of the page (96-dpi units) in pixels at a scale.
        public static int Px(double dip, int dpi) => (int)Math.Round(dip * dpi / 96.0);

        static bool HasCjk(string text)
        {
            foreach (var c in text ?? "") if (c >= 0x2e80 && c <= 0xffef) return true;
            return false;
        }

        // The page's body face: Segoe UI for Latin text, Microsoft YaHei UI where there is Chinese; sized in pixels as
        // the page's are.
        public static Font Body(string text, double px, int dpi, bool strong = false)
        {
            var cjk = HasCjk(text);
            var family = cjk ? "Microsoft YaHei UI" : strong ? "Segoe UI Semibold" : "Segoe UI";
            var style = strong && cjk ? FontStyle.Bold : FontStyle.Regular;
            return new Font(family, (float)(px * dpi / 96.0), style, GraphicsUnit.Pixel);
        }

        [DllImport("dwmapi.dll")]
        static extern int DwmSetWindowAttribute(IntPtr hwnd, int attribute, ref int value, int size);

        // Windows 11 rounds the corners of some windows by itself; the archive's corners are square.
        public static void SquareCorners(IntPtr hwnd)
        {
            int never = 1;  // DWMWCP_DONOTROUND
            try { DwmSetWindowAttribute(hwnd, 33 /* DWMWA_WINDOW_CORNER_PREFERENCE */, ref never, sizeof(int)); } catch { /* before Windows 11 */ }
        }

        [DllImport("user32.dll")] static extern IntPtr MonitorFromPoint(Point pt, uint flags);
        [DllImport("shcore.dll")] static extern int GetDpiForMonitor(IntPtr monitor, int type, out uint dpiX, out uint dpiY);

        // The scale of the screen under the pointer (where a menu or a question from the notification area appears).
        public static int DpiAtCursor()
        {
            try
            {
                if (GetDpiForMonitor(MonitorFromPoint(Cursor.Position, 2 /* nearest */), 0, out var x, out _) == 0) return (int)x;
            }
            catch { /* before Windows 8.1 */ }
            return 96;
        }

        public static void Frame(Graphics g, Size size, int dpi)
        {
            int w = Math.Max(1, Px(1.5, dpi));
            using (var b = new SolidBrush(Ink))
            {
                g.FillRectangle(b, 0, 0, size.Width, w);
                g.FillRectangle(b, 0, size.Height - w, size.Width, w);
                g.FillRectangle(b, 0, 0, w, size.Height);
                g.FillRectangle(b, size.Width - w, 0, w, size.Height);
            }
        }
    }

    // The notification area's menu: a paper panel in an ink frame, its lines 36 high, the line under the pointer (or
    // chosen with the arrow keys) inked as the page's line buttons are under the pointer. It closes when anything else
    // is clicked, and on Esc.
    sealed class DepotMenu : Form
    {
        readonly string[] labels;
        readonly Action[] actions;
        readonly int dpi;
        readonly Rectangle[] rows;
        int lit = -1;

        public DepotMenu(string[] labels, Action[] actions, int dpi)
        {
            this.labels = labels;
            this.actions = actions;
            this.dpi = dpi;
            FormBorderStyle = FormBorderStyle.None;
            AutoScaleMode = AutoScaleMode.None;
            StartPosition = FormStartPosition.Manual;
            ShowInTaskbar = false;
            TopMost = true;
            KeyPreview = true;
            BackColor = Look.Paper;
            DoubleBuffered = true;
            int pad = Px(5), line = Px(36), width = Px(200), gap = Px(9);
            rows = new Rectangle[labels.Length];
            int y = pad;
            for (int i = 0; i < labels.Length; i++)
            {
                using (var f = Look.Body(labels[i], 13.5, dpi)) width = Math.Max(width, TextRenderer.MeasureText(labels[i], f).Width + 2 * Px(16) + 2 * pad);
                if (i > 0) y += gap;        // the rule between lines
                rows[i] = new Rectangle(pad, y, 0, line);
                y += line;
            }
            for (int i = 0; i < rows.Length; i++) rows[i].Width = width - 2 * pad;
            ClientSize = new Size(width, y + pad);
        }

        int Px(double dip) => Look.Px(dip, dpi);

        // Opened at the pointer, kept within the screen (above and to the left of it when the taskbar is below).
        public void Open()
        {
            var at = Cursor.Position;
            var area = Screen.FromPoint(at).WorkingArea;
            int x = Math.Min(at.X, area.Right - Width), y = at.Y - Height;
            if (y < area.Top) y = Math.Min(at.Y, area.Bottom - Height);
            Location = new Point(Math.Max(area.Left, x), y);
            Show();
            Activate();
        }

        protected override void OnHandleCreated(EventArgs e)
        {
            base.OnHandleCreated(e);
            Look.SquareCorners(Handle);
        }

        protected override void OnDeactivate(EventArgs e)
        {
            base.OnDeactivate(e);
            Close();
        }

        protected override void OnPaint(PaintEventArgs e)
        {
            var g = e.Graphics;
            for (int i = 0; i < rows.Length; i++)
            {
                if (i > 0)
                {
                    int y = rows[i].Top - Px(9) / 2 - 1, inset = Px(12);
                    using (var b = new SolidBrush(Look.Rule)) g.FillRectangle(b, inset, y, Width - 2 * inset, Math.Max(1, Px(1)));
                }
                if (i == lit) using (var b = new SolidBrush(Look.Ink)) g.FillRectangle(b, rows[i]);
                var text = new Rectangle(rows[i].X + Px(16), rows[i].Y, rows[i].Width - Px(32), rows[i].Height);
                using (var f = Look.Body(labels[i], 13.5, dpi))
                    TextRenderer.DrawText(g, labels[i], f, text, i == lit ? Color.White : Look.Ink,
                        TextFormatFlags.VerticalCenter | TextFormatFlags.Left | TextFormatFlags.NoPrefix | TextFormatFlags.EndEllipsis);
            }
            Look.Frame(g, ClientSize, dpi);
        }

        int RowAt(Point p)
        {
            for (int i = 0; i < rows.Length; i++) if (rows[i].Contains(p)) return i;
            return -1;
        }

        protected override void OnMouseMove(MouseEventArgs e)
        {
            base.OnMouseMove(e);
            int now = RowAt(e.Location);
            if (now != lit) { lit = now; Invalidate(); }
        }

        protected override void OnMouseUp(MouseEventArgs e)
        {
            base.OnMouseUp(e);
            int i = RowAt(e.Location);
            if (i >= 0) Run(i);
        }

        protected override void OnKeyDown(KeyEventArgs e)
        {
            base.OnKeyDown(e);
            if (e.KeyCode == Keys.Escape) Close();
            else if (e.KeyCode == Keys.Down || e.KeyCode == Keys.Up)
            {
                int step = e.KeyCode == Keys.Down ? 1 : -1;
                lit = lit < 0 ? (step > 0 ? 0 : rows.Length - 1) : (lit + step + rows.Length) % rows.Length;
                Invalidate();
            }
            else if ((e.KeyCode == Keys.Enter || e.KeyCode == Keys.Space) && lit >= 0) Run(lit);
        }

        void Run(int i)
        {
            var action = actions[i];
            Close();
            BeginInvokeLater(action);
        }

        // after the menu has gone, so that a question it opens is not closed with it
        static void BeginInvokeLater(Action action)
        {
            var t = new Timer { Interval = 1 };
            t.Tick += (s, e) => { t.Stop(); t.Dispose(); action(); };
            t.Start();
        }

        // For checking the look without the app running.
        public static Bitmap Picture(string[] labels, int dpi, int litRow)
        {
            using (var m = new DepotMenu(labels, new Action[labels.Length], dpi) { lit = litRow })
            {
                var bmp = new Bitmap(m.Width, m.Height);
                m.CreateControl();
                m.DrawToBitmap(bmp, new Rectangle(Point.Empty, m.Size));
                return bmp;
            }
        }
    }

    // A question or a notice in the page's confirmation style (the delete dialog): a title, a sentence or two, and the
    // buttons at the bottom right; Esc cancels, Enter takes the button in focus (Cancel first, so that a stray key does
    // nothing irreversible).
    sealed class DepotDialog : Form
    {
        readonly string title, body;
        readonly string[] labels;           // the buttons, left to right; the last one is the action
        readonly int dpi;
        Rectangle[] buttons = new Rectangle[0];
        int focus, hover = -1;
        Rectangle titleBox, bodyBox;
        Point dragFrom;

        DepotDialog(string title, string body, string[] labels, int dpi)
        {
            this.title = title;
            this.body = body;
            this.labels = labels;
            this.dpi = dpi;
            focus = labels.Length > 1 ? 0 : labels.Length - 1;
            FormBorderStyle = FormBorderStyle.None;
            AutoScaleMode = AutoScaleMode.None;
            StartPosition = FormStartPosition.Manual;
            ShowInTaskbar = false;
            KeyPreview = true;
            BackColor = Look.Paper;
            Text = title;
            DoubleBuffered = true;
            Lay();
        }

        int Px(double dip) => Look.Px(dip, dpi);

        // The page's measures: 22 / 24 / 20 around, the title 19 px, the text 13.5 px, 18 above the buttons, buttons 48
        // high with 16 at either side of their words, 10 between them.
        void Lay()
        {
            int width = Px(440), inner = width - 2 * Px(24);
            var flags = TextFormatFlags.WordBreak | TextFormatFlags.NoPrefix;
            int y = Px(22);
            using (var f = Look.Body(title, 19, dpi, true))
            {
                var s = TextRenderer.MeasureText(title, f, new Size(inner, int.MaxValue), flags);
                titleBox = new Rectangle(Px(24), y, inner, s.Height);
                y += s.Height;
            }
            if (!string.IsNullOrEmpty(body))
            {
                y += Px(10);
                using (var f = Look.Body(body, 13.5, dpi))
                {
                    var s = TextRenderer.MeasureText(body, f, new Size(inner, int.MaxValue), flags);
                    bodyBox = new Rectangle(Px(24), y, inner, s.Height);
                    y += s.Height;
                }
            }
            y += Px(18);
            int height = Px(48), right = width - Px(24);
            buttons = new Rectangle[labels.Length];
            for (int i = labels.Length - 1; i >= 0; i--)
            {
                using (var f = Look.Body(labels[i], 14, dpi, true))
                {
                    int w = Math.Max(Px(96), TextRenderer.MeasureText(labels[i], f).Width + 2 * Px(16));
                    buttons[i] = new Rectangle(right - w, y, w, height);
                    right -= w + Px(10);
                }
            }
            ClientSize = new Size(width, y + height + Px(20));
        }

        protected override void OnHandleCreated(EventArgs e)
        {
            base.OnHandleCreated(e);
            Look.SquareCorners(Handle);
        }

        protected override void OnPaint(PaintEventArgs e)
        {
            var g = e.Graphics;
            g.SmoothingMode = SmoothingMode.None;
            int line = Math.Max(1, Px(1.5));
            using (var ink = new SolidBrush(Look.Ink))
            {
                g.FillRectangle(ink, 0, 0, Width, line);
                g.FillRectangle(ink, 0, Height - line, Width, line);
                g.FillRectangle(ink, 0, 0, line, Height);
                g.FillRectangle(ink, Width - line, 0, line, Height);
            }
            var flags = TextFormatFlags.WordBreak | TextFormatFlags.NoPrefix;
            using (var f = Look.Body(title, 19, dpi, true)) TextRenderer.DrawText(g, title, f, titleBox, Look.Ink, flags);
            if (!string.IsNullOrEmpty(body))
                using (var f = Look.Body(body, 13.5, dpi)) TextRenderer.DrawText(g, body, f, bodyBox, Look.Ink3, flags);
            for (int i = 0; i < buttons.Length; i++) PaintButton(g, i);
        }

        void PaintButton(Graphics g, int i)
        {
            var r = buttons[i];
            bool action = i == buttons.Length - 1, over = i == hover;
            int line = Math.Max(1, Px(1.5));
            Color fill = action ? (over ? Look.InkHover : Look.Ink) : (over ? Look.Ink : Look.Paper);
            Color edge = action && over ? Look.InkHover : Look.Ink;
            Color text = action || over ? Color.White : Look.Ink;
            using (var b = new SolidBrush(edge)) g.FillRectangle(b, r);
            using (var b = new SolidBrush(fill)) g.FillRectangle(b, Rectangle.Inflate(r, -line, -line));
            using (var f = Look.Body(labels[i], 14, dpi, true))
                TextRenderer.DrawText(g, labels[i], f, r, text, TextFormatFlags.HorizontalCenter | TextFormatFlags.VerticalCenter | TextFormatFlags.NoPrefix);
            if (i == focus && ContainsFocus)
            {
                // the page's :focus-visible: a 2 px ink ring, 2 px out
                int ring = Math.Max(1, Px(2)), gap = Px(2);
                var o = Rectangle.Inflate(r, gap + ring, gap + ring);
                using (var p = new Pen(Look.Ink, ring) { Alignment = PenAlignment.Inset }) g.DrawRectangle(p, o.X, o.Y, o.Width - 1, o.Height - 1);
            }
        }

        int ButtonAt(Point p)
        {
            for (int i = 0; i < buttons.Length; i++) if (buttons[i].Contains(p)) return i;
            return -1;
        }

        protected override void OnMouseMove(MouseEventArgs e)
        {
            base.OnMouseMove(e);
            if (e.Button == MouseButtons.Left && ButtonAt(dragFrom) < 0)
            {
                Location = new Point(Location.X + e.X - dragFrom.X, Location.Y + e.Y - dragFrom.Y);     // moved by any empty part
                return;
            }
            int now = ButtonAt(e.Location);
            if (now != hover) { hover = now; Cursor = now >= 0 ? Cursors.Hand : Cursors.Default; Invalidate(); }
        }

        protected override void OnMouseLeave(EventArgs e)
        {
            base.OnMouseLeave(e);
            if (hover >= 0) { hover = -1; Invalidate(); }
        }

        protected override void OnMouseDown(MouseEventArgs e)
        {
            base.OnMouseDown(e);
            dragFrom = e.Location;
        }

        protected override void OnMouseUp(MouseEventArgs e)
        {
            base.OnMouseUp(e);
            int i = ButtonAt(e.Location);
            if (e.Button == MouseButtons.Left && i >= 0 && i == ButtonAt(dragFrom)) Choose(i);
        }

        protected override void OnKeyDown(KeyEventArgs e)
        {
            base.OnKeyDown(e);
            if (e.KeyCode == Keys.Escape) { DialogResult = DialogResult.Cancel; Close(); }
            else if (e.KeyCode == Keys.Enter || e.KeyCode == Keys.Space) Choose(focus);
            else if (e.KeyCode == Keys.Tab || e.KeyCode == Keys.Left || e.KeyCode == Keys.Right)
            {
                int step = e.KeyCode == Keys.Left || (e.KeyCode == Keys.Tab && e.Shift) ? -1 : 1;
                focus = (focus + step + buttons.Length) % buttons.Length;
                Invalidate();
            }
        }

        protected override bool IsInputKey(Keys key) => (key & Keys.KeyCode) == Keys.Tab || base.IsInputKey(key);

        protected override void OnActivated(EventArgs e) { base.OnActivated(e); Invalidate(); }
        protected override void OnDeactivate(EventArgs e) { base.OnDeactivate(e); Invalidate(); }

        void Choose(int i)
        {
            DialogResult = i == buttons.Length - 1 ? DialogResult.OK : DialogResult.Cancel;
            Close();
        }

        // Centred on the window when it is in view, else on the screen under the pointer; in front of everything,
        // as it may be asked from the notification area while the window is hidden.
        static DialogResult Show(Form owner, string title, string body, string[] labels)
        {
            bool shown = owner != null && owner.Visible && owner.WindowState != FormWindowState.Minimized;
            int dpi = shown ? owner.DeviceDpi : Look.DpiAtCursor();
            using (var d = new DepotDialog(title, body, labels, dpi))
            {
                var area = shown ? owner.Bounds : Screen.FromPoint(Cursor.Position).WorkingArea;
                d.Location = new Point(area.Left + (area.Width - d.Width) / 2, area.Top + (area.Height - d.Height) / 2);
                d.TopMost = !shown;
                return shown ? d.ShowDialog(owner) : d.ShowDialog();
            }
        }

        public static bool Ask(Form owner, string title, string body, string action, string cancel) =>
            Show(owner, title, body, new[] { cancel, action }) == DialogResult.OK;

        public static void Tell(Form owner, string title, string body, string ok) => Show(owner, title, body, new[] { ok });

        // For checking the look without the app running: the dialog drawn into a picture at a scale.
        public static Bitmap Picture(string title, string body, string action, string cancel, int dpi, int hoverButton = -1)
        {
            using (var d = new DepotDialog(title, body, cancel == null ? new[] { action } : new[] { cancel, action }, dpi) { hover = hoverButton })
            {
                var bmp = new Bitmap(d.Width, d.Height);
                d.CreateControl();
                d.DrawToBitmap(bmp, new Rectangle(Point.Empty, d.Size));
                return bmp;
            }
        }
    }
}
