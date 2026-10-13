// SPDX-License-Identifier: Apache-2.0
// Shared by the DWG add-ins and compiled into each of them, like DwgHostLogic.cs (read the notes there).

namespace Kosha.CadCore.Dwg
{
    using System;
    using Kosha.CadCore.UI;

    /// <summary>
    /// The Kosha palette of a DWG add-in: created on the first KOSHA command and kept for the CAD session.
    /// Every member catches its own errors, so an exception never reaches the CAD program.
    /// </summary>
    public sealed class DwgPalette
    {
        readonly string title;
        readonly Guid paletteId;
        readonly Func<ICadHost> createHost;
        PaletteSet palette;
        KoshaTaskPane pane;

        /// <param name="title">Palette title, "Kosha".</param>
        /// <param name="paletteId">A fixed id per CAD program, so the CAD program remembers where the palette was docked.</param>
        /// <param name="createHost">Makes the CAD program's host when the palette opens for the first time.</param>
        public DwgPalette(string title, Guid paletteId, Func<ICadHost> createHost)
        {
            this.title = title;
            this.paletteId = paletteId;
            this.createHost = createHost;
        }

        /// <summary>Call from IExtensionApplication.Initialize: follows drawing switches and says how to open the palette.</summary>
        public void Initialize()
        {
            try
            {
                CadApplication.DocumentManager.DocumentActivated += (s, e) => Refresh();
                CadApplication.DocumentManager.MdiActiveDocument?.Editor.WriteMessage("\nKosha add-in loaded. Type KOSHA to open the Kosha palette.\n");
            }
            catch (System.Exception)
            {
                // never let an add-in error reach the CAD program
            }
        }

        /// <summary>The KOSHA command.</summary>
        public void Show()
        {
            try
            {
                if (palette == null)
                {
                    pane = pane ?? new KoshaTaskPane(createHost());
                    palette = new PaletteSet(title, paletteId);
                    palette.Add(title, pane);
                    palette.MinimumSize = new System.Drawing.Size(300, 500);
                }
                palette.Visible = true;
                Refresh();
            }
            catch (System.Exception ex)
            {
                System.Windows.Forms.MessageBox.Show("The Kosha palette could not open: " + ex.Message + Environment.NewLine +
                                                     "Your drawing was not changed.", "Kosha");
            }
        }

        /// <summary>Shows the status of the active drawing again (after a drawing switch or a save).</summary>
        public void Refresh()
        {
            try { pane?.RefreshStatus(); }
            catch (System.Exception) { }
        }

        /// <summary>Call from IExtensionApplication.Terminate.</summary>
        public void Terminate()
        {
            try { pane?.Dispose(); }
            catch (System.Exception) { }
        }
    }
}
