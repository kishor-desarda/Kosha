// SPDX-License-Identifier: Apache-2.0
using System.Collections.Generic;

namespace Kosha.CadCore
{
    public enum CadDocumentKind { Part, Assembly, Drawing, Other }

    public enum CadTheme { Light, Dark }

    /// <summary>An open CAD document. <see cref="Native"/> is the CAD program's own object, used only by its host.</summary>
    public sealed class CadDocument
    {
        public CadDocument(string path, string title, CadDocumentKind kind, object native)
        {
            Path = path ?? "";
            Title = string.IsNullOrEmpty(title) ? System.IO.Path.GetFileName(Path) : title;
            Kind = kind;
            Native = native;
        }

        public string Path { get; }
        public string Title { get; }
        public CadDocumentKind Kind { get; }
        public object Native { get; }
        public bool IsSaved => Path.Length > 0;
        public string FileName => System.IO.Path.GetFileName(Path);
    }

    /// <summary>Mass and size of a model, in kg and mm. Box is sorted largest first.</summary>
    public sealed class CadPhysical
    {
        public double MassKg { get; set; }
        public double VolumeMm3 { get; set; }
        public double AreaMm2 { get; set; }
        public double[] BoxMm { get; set; } = new double[3];
    }

    /// <summary>A direct child of an assembly with its quantity.</summary>
    public sealed class CadComponent
    {
        public string Path { get; set; }
        public string Configuration { get; set; } = "";
        public IDictionary<string, string> Properties { get; set; } = new Dictionary<string, string>();
        public string Material { get; set; } = "";
        public int Quantity { get; set; } = 1;
        public bool IsVirtual { get; set; }
    }

    /// <summary>
    /// What a CAD program must provide to the shared CAD core. One thin class per CAD program
    /// (SolidWorksHost, GstarCadHost, AutoCadHost ...). Every member is called on the CAD program's
    /// main thread. Members that a program cannot support return null or an empty list.
    /// </summary>
    public interface ICadHost
    {
        /// <summary>Shown in logs and stored with each check-in, e.g. "GStarCAD 2025".</summary>
        string ProgramName { get; }
        CadTheme Theme { get; }
        CadDocument ActiveDocument { get; }

        /// <summary>Document properties, resolved, case-insensitive (custom properties, title-block attributes ...).</summary>
        IDictionary<string, string> ReadProperties(CadDocument doc);
        void WriteProperty(CadDocument doc, string name, string value);
        string ActiveConfiguration(CadDocument doc);
        string ReadMaterial(CadDocument doc);
        CadPhysical ReadPhysical(CadDocument doc);

        /// <summary>Direct children with quantities (assemblies); empty for other documents.</summary>
        IList<CadComponent> ReadStructure(CadDocument doc);

        /// <summary>Open documents this one depends on (parts of an assembly, xrefs of a drawing), deepest first.</summary>
        IList<CadDocument> ReferencedDocumentsDeepestFirst(CadDocument doc);

        bool HasUnsavedChanges(CadDocument doc);
        bool Save(CadDocument doc);
        byte[] ExportPreviewPng(CadDocument doc);
        byte[] ExportMeshStl(CadDocument doc);

        /// <summary>The file's bytes, even while the CAD program has it open.</summary>
        byte[] ReadFileBytes(CadDocument doc);

        void Close(CadDocument doc);
        CadDocument Open(string path, CadDocumentKind kind);
    }
}
