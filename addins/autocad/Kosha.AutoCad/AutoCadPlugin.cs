// SPDX-License-Identifier: Apache-2.0

[assembly: ExtensionApplication(typeof(Kosha.AutoCad.AutoCadPlugin))]
[assembly: CommandClass(typeof(Kosha.AutoCad.AutoCadPlugin))]

namespace Kosha.AutoCad
{
    using System;
    using Kosha.CadCore.Dwg;

    /// <summary>
    /// Loaded by AutoCAD: with NETLOAD, or at start-up from the Kosha.bundle autoloader folder.
    /// The KOSHA command shows the Kosha palette (a PaletteSet with the shared Kosha task pane);
    /// the palette and its events are shared with the GStarCAD add-in (<see cref="DwgPalette"/>).
    /// </summary>
    public sealed class AutoCadPlugin : IExtensionApplication
    {
        static readonly DwgPalette Palette =
            new DwgPalette("Kosha", new Guid("B62FEC8F-54A7-4BD5-A687-CC6B505245EF"), () => new AutoCadHost());

        public void Initialize() => Palette.Initialize();

        public void Terminate() => Palette.Terminate();

        [CommandMethod("KOSHA")]
        public void ShowPalette() => Palette.Show();
    }
}
