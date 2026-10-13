// SPDX-License-Identifier: Apache-2.0

namespace Kosha.GstarCad
{
    using Kosha.CadCore.Dwg;

    /// <summary>
    /// GStarCAD answers for the shared CAD core. All the DWG work (title blocks, drawing properties, save,
    /// close, open) is in the shared <see cref="DwgHostLogic"/> (sdk/dotnet/Kosha.CadCore.Dwg), the same code
    /// as the AutoCAD add-in; it compiles against GStarCAD through the names in GstarCadApi.cs.
    /// </summary>
    public sealed class GstarCadHost : DwgHostLogic
    {
        protected override string ProductName => "GStarCAD";
    }
}
