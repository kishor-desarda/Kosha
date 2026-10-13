// SPDX-License-Identifier: Apache-2.0

namespace Kosha.AutoCad
{
    using Kosha.CadCore.Dwg;

    /// <summary>
    /// AutoCAD answers for the shared CAD core (an <see cref="Kosha.CadCore.ICadHost"/>). All the DWG work
    /// (title blocks, drawing properties, save, close, open) is in the shared <see cref="DwgHostLogic"/>
    /// (sdk/dotnet/Kosha.CadCore.Dwg), the same code as the GStarCAD add-in; it compiles against AutoCAD
    /// through the names in AutoCadApi.cs. Called on AutoCAD's main thread only.
    /// </summary>
    public sealed class AutoCadHost : DwgHostLogic
    {
        protected override string ProductName => "AutoCAD";
    }
}
