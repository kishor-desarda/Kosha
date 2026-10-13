// SPDX-License-Identifier: Apache-2.0
using System.Drawing;
using System.Windows.Forms;

namespace Kosha.Desktop
{
    /// <summary>Small layout helpers so every window looks the same without a designer file.</summary>
    static class Ui
    {
        public const int Width = 420;

        /// <summary>A top-to-bottom stack of controls that sizes itself.</summary>
        public static FlowLayoutPanel Stack() => new FlowLayoutPanel
        {
            FlowDirection = FlowDirection.TopDown,
            WrapContents = false,
            AutoSize = true,
            AutoSizeMode = AutoSizeMode.GrowAndShrink,
            Padding = new Padding(16, 14, 16, 14),
        };

        /// <summary>A row of buttons, right-aligned.</summary>
        public static FlowLayoutPanel ButtonRow() => new FlowLayoutPanel
        {
            FlowDirection = FlowDirection.RightToLeft,
            WrapContents = false,
            AutoSize = true,
            AutoSizeMode = AutoSizeMode.GrowAndShrink,
            MinimumSize = new Size(Width, 0),        // full width, so the buttons line up on the right
            Margin = new Padding(0, 10, 0, 0),
        };

        /// <summary>A label that wraps at the window width.</summary>
        public static Label Text(string text, bool bold = false) => new Label
        {
            Text = text,
            AutoSize = true,
            MaximumSize = new Size(Width, 0),
            Margin = new Padding(0, 4, 0, 2),
            Font = bold ? new Font("Segoe UI Semibold", 10f) : null,
        };

        public static Label Caption(string text) => new Label
        {
            Text = text,
            AutoSize = true,
            Margin = new Padding(0, 8, 0, 2),
            Font = new Font("Segoe UI Semibold", 9f),
        };

        public static TextBox Input(string accessibleName, string value = "") => new TextBox
        {
            Text = value ?? "",
            Width = Width,
            AccessibleName = accessibleName,
        };

        public static Button Button(string text, bool primary = false)
        {
            var b = new Button { Text = text, AutoSize = true, MinimumSize = new Size(96, 30), Margin = new Padding(8, 0, 0, 0) };
            if (primary)
            {
                b.BackColor = DesktopApp.Accent;
                b.ForeColor = Color.White;
                b.FlatStyle = FlatStyle.Flat;
            }
            return b;
        }

        public static void StandardDialog(Form f, string title)
        {
            f.Text = title;
            f.Icon = KoshaIcon.Get();
            f.Font = new Font("Segoe UI", 9f);
            f.AutoScaleDimensions = new SizeF(96f, 96f);      // sizes here are for 96 DPI; Windows scales them up
            f.AutoScaleMode = AutoScaleMode.Dpi;
            f.FormBorderStyle = FormBorderStyle.FixedDialog;
            f.MaximizeBox = false;
            f.MinimizeBox = false;
            f.StartPosition = FormStartPosition.CenterScreen;
            f.AutoSize = true;
            f.AutoSizeMode = AutoSizeMode.GrowAndShrink;
        }
    }
}
