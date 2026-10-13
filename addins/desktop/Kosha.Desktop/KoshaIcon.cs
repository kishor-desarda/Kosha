// SPDX-License-Identifier: Apache-2.0
using System;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.IO;
using System.Windows.Forms;

namespace Kosha.Desktop
{
    /// <summary>The Kosha mark (layers on a Kosha Blue tile) for the tray and the windows.</summary>
    static class KoshaIcon
    {
        static Icon small;
        static Icon large;

        /// <summary>Window icon.</summary>
        public static Icon Get() => large ?? (large = Load(SystemInformation.IconSize));

        /// <summary>Tray icon, at the size Windows uses there.</summary>
        public static Icon Tray() => small ?? (small = Load(SystemInformation.SmallIconSize));

        static Icon Load(Size size)
        {
            try
            {
                using (var stream = typeof(KoshaIcon).Assembly.GetManifestResourceStream("Kosha.Desktop.kosha.ico"))
                {
                    if (stream != null) return new Icon(stream, size);
                }
            }
            catch (Exception ex) when (ex is ArgumentException || ex is IOException)
            {
                // fall back to drawing it
            }
            return Draw(size.Width);
        }

        static Icon Draw(int px)
        {
            try
            {
                using (var bmp = new Bitmap(px, px))
                using (var g = Graphics.FromImage(bmp))
                {
                    float s = px / 30f;
                    g.SmoothingMode = SmoothingMode.AntiAlias;
                    g.Clear(Color.Transparent);
                    using (var tile = new GraphicsPath())
                    using (var blue = new SolidBrush(DesktopApp.Accent))
                    using (var white = new SolidBrush(Color.White))
                    using (var pen = new Pen(Color.White, 2.4f * s) { LineJoin = LineJoin.Round, StartCap = LineCap.Round, EndCap = LineCap.Round })
                    {
                        float r = 13f * s, x = 1f * s, y = 1f * s, w = 28f * s;
                        tile.AddArc(x, y, r, r, 180, 90);
                        tile.AddArc(x + w - r, y, r, r, 270, 90);
                        tile.AddArc(x + w - r, y + w - r, r, r, 0, 90);
                        tile.AddArc(x, y + w - r, r, r, 90, 90);
                        tile.CloseFigure();
                        g.FillPath(blue, tile);
                        g.FillPolygon(white, new[] { P(15, 6.5f, s), P(23.5f, 11, s), P(15, 15.5f, s), P(6.5f, 11, s) });
                        foreach (float dy in new[] { 4.5f, 9f })
                            g.DrawLines(pen, new[] { P(6.5f, 11 + dy, s), P(15, 15.5f + dy, s), P(23.5f, 11 + dy, s) });
                    }
                    return Icon.FromHandle(bmp.GetHicon());
                }
            }
            catch (Exception)
            {
                return SystemIcons.Application;
            }
        }

        static PointF P(float x, float y, float scale) => new PointF(x * scale, y * scale);
    }
}
