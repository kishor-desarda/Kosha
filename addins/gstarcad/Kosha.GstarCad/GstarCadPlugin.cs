// SPDX-License-Identifier: Apache-2.0
using System;
using Kosha.CadCore.UI;

[assembly: ExtensionApplication(typeof(Kosha.GstarCad.GstarCadPlugin))]
[assembly: CommandClass(typeof(Kosha.GstarCad.GstarCadPlugin))]

namespace Kosha.GstarCad
{
    /// <summary>Loaded by GStarCAD (NETLOAD or the startup suite). The KOSHA command shows the Kosha palette.</summary>
    public sealed class GstarCadPlugin : IExtensionApplication
    {
        static PaletteSet palette;
        static KoshaTaskPane pane;

        public void Initialize()
        {
            try
            {
                CadApplication.DocumentManager.DocumentActivated += (s, e) => Refresh();
                CadApplication.DocumentManager.MdiActiveDocument?.Editor.WriteMessage("\nKosha add-in loaded. Type KOSHA to open the Kosha palette.\n");
            }
            catch (System.Exception)
            {
                // never let an add-in error reach GStarCAD
            }
        }

        public void Terminate()
        {
            try { pane?.Dispose(); } catch (System.Exception) { }
        }

        static void Refresh()
        {
            try { pane?.RefreshStatus(); } catch (System.Exception) { }
        }

        [CommandMethod("KOSHA")]
        public void ShowPalette()
        {
            try
            {
                if (palette == null)
                {
                    pane = new KoshaTaskPane(new GstarCadHost());
                    palette = new PaletteSet("Kosha", new Guid("3C1D2F4A-5B6E-4C7D-8E9F-0A1B2C3D4E5F"));
                    palette.Add("Kosha", pane);
                    palette.MinimumSize = new System.Drawing.Size(300, 500);
                }
                palette.Visible = true;
                Refresh();
            }
            catch (System.Exception ex)
            {
                System.Windows.Forms.MessageBox.Show("The Kosha palette could not open: " + ex.Message, "Kosha");
            }
        }
    }
}
