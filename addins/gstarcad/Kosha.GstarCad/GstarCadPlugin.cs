// SPDX-License-Identifier: Apache-2.0

[assembly: ExtensionApplication(typeof(Kosha.GstarCad.GstarCadPlugin))]
[assembly: CommandClass(typeof(Kosha.GstarCad.GstarCadPlugin))]

namespace Kosha.GstarCad
{
    using System;
    using Kosha.CadCore.Dwg;

    /// <summary>
    /// Loaded by GStarCAD (NETLOAD or the startup suite). The KOSHA command shows the Kosha palette.
    /// The palette and its events are shared with the AutoCAD add-in (<see cref="DwgPalette"/>).
    /// </summary>
    public sealed class GstarCadPlugin : IExtensionApplication
    {
        static readonly DwgPalette Palette =
            new DwgPalette("Kosha", new Guid("3C1D2F4A-5B6E-4C7D-8E9F-0A1B2C3D4E5F"), () => new GstarCadHost());

        public void Initialize() => Palette.Initialize();

        public void Terminate() => Palette.Terminate();

        [CommandMethod("KOSHA")]
        public void ShowPalette() => Palette.Show();
    }
}
