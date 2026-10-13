// SPDX-License-Identifier: Apache-2.0
//
// Shared DWG host, written once for every AutoCAD-like CAD program (AutoCAD, GStarCAD ...).
//
// This folder is not a project of its own: each DWG add-in compiles these files into its own assembly with
//     <Compile Include="..\..\..\sdk\dotnet\Kosha.CadCore.Dwg\*.cs" Link="Shared\%(Filename)%(Extension)" />
// The files name no CAD namespace. CadApplication, CadDocumentNative and the database types (Database,
// BlockTable, AttributeReference, DwgVersion ...) come from the add-in's own *Api.cs file of global usings,
// so the same source compiles against Autodesk.AutoCAD.* (AutoCadApi.cs) and GrxCAD.* (GstarCadApi.cs).
// Catch System.Exception by its full name: the CAD Runtime namespaces have an Exception class of their own.
// The rules that need no CAD API (which tags, which property goes where) are in Kosha.CadCore.TitleBlocks,
// which is tested in CI; keep the code here a thin walk over the drawing.
// The using directives sit inside the namespace on purpose: there they win over the CAD namespaces of the
// global usings, so File, Path or Exception always mean the System types.

namespace Kosha.CadCore.Dwg
{
    using System;
    using System.Collections;
    using System.Collections.Generic;
    using System.IO;

    /// <summary>
    /// DWG answers for the shared CAD core. Drawings only: part number, description and material come from the
    /// drawing's custom properties (DWGPROPS) and from title-block attributes (PART_NO, DESCRIPTION, MATERIAL ...).
    /// Previews are made on the Kosha server from the DWG itself, so none is exported here.
    /// Called on the CAD program's main thread only.
    /// </summary>
    public abstract class DwgHostLogic : ICadHost
    {
        /// <summary>The CAD program's name without a version, for example "AutoCAD".</summary>
        protected abstract string ProductName { get; }

        public string ProgramName
        {
            get
            {
                try { return ProductName + " " + CadApplication.Version; }
                catch (System.Exception) { return ProductName; }
            }
        }

        /// <summary>COLORTHEME is 0 for the dark theme and 1 for the light one.</summary>
        public CadTheme Theme
        {
            get
            {
                try { return Convert.ToInt32(CadApplication.GetSystemVariable("COLORTHEME")) == 0 ? CadTheme.Dark : CadTheme.Light; }
                catch (System.Exception) { return CadTheme.Light; }
            }
        }

        /// <summary>An unsaved drawing is called "Drawing1.dwg" but has no file yet: it gets an empty path.</summary>
        static CadDocument Wrap(CadDocumentNative d) =>
            d == null ? null : new CadDocument(File.Exists(d.Name) ? d.Name : "", Path.GetFileName(d.Name), CadDocumentKind.Drawing, d);

        static CadDocumentNative N(CadDocument d) => (CadDocumentNative)d.Native;

        public CadDocument ActiveDocument
        {
            get
            {
                try { return Wrap(CadApplication.DocumentManager.MdiActiveDocument); }
                catch (System.Exception) { return null; }
            }
        }

        public string ActiveConfiguration(CadDocument doc) => "";

        public IDictionary<string, string> ReadProperties(CadDocument doc)
        {
            var db = N(doc).Database;
            var custom = new List<KeyValuePair<string, string>>();
            string title = "";
            try
            {
                var info = db.SummaryInfo;
                var e = info.CustomProperties;
                while (e.MoveNext())
                {
                    var entry = (DictionaryEntry)e.Current;
                    custom.Add(new KeyValuePair<string, string>(Convert.ToString(entry.Key), Convert.ToString(entry.Value)));
                }
                title = info.Title;
            }
            catch (System.Exception)
            {
                // drawings without summary information are normal
            }
            return TitleBlocks.Merge(custom, title, TitleBlockValues(db));
        }

        /// <summary>Attribute values of known title-block tags, from block references in every layout (model and paper space).</summary>
        static IDictionary<string, string> TitleBlockValues(Database db)
        {
            var reader = new TitleBlockReader();
            try
            {
                using (var tr = db.TransactionManager.StartTransaction())
                {
                    var bt = (BlockTable)tr.GetObject(db.BlockTableId, OpenMode.ForRead);
                    foreach (ObjectId btrId in bt)
                    {
                        var btr = (BlockTableRecord)tr.GetObject(btrId, OpenMode.ForRead);
                        if (!btr.IsLayout) continue;
                        foreach (ObjectId id in btr)
                        {
                            if (!(tr.GetObject(id, OpenMode.ForRead) is BlockReference br)) continue;
                            foreach (ObjectId attId in br.AttributeCollection)
                            {
                                if (tr.GetObject(attId, OpenMode.ForRead) is AttributeReference att)
                                    reader.Add(att.Tag, att.TextString);
                            }
                        }
                    }
                    tr.Commit();
                }
            }
            catch (System.Exception)
            {
                // a damaged block must not stop the pane: properties and the file name still work
            }
            return reader.Values;
        }

        /// <summary>Writes a drawing property and, for part number, description and material, the matching title-block attribute.</summary>
        public void WriteProperty(CadDocument doc, string name, string value)
        {
            var d = N(doc);
            using (d.LockDocument())
            {
                var db = d.Database;
                var builder = new DatabaseSummaryInfoBuilder(db.SummaryInfo);
                builder.CustomPropertyTable[name] = value;
                db.SummaryInfo = builder.ToDatabaseSummaryInfo();
                string tag = TitleBlocks.TagFor(name);
                if (tag == null) return;
                using (var tr = db.TransactionManager.StartTransaction())
                {
                    var bt = (BlockTable)tr.GetObject(db.BlockTableId, OpenMode.ForRead);
                    foreach (ObjectId btrId in bt)
                    {
                        var btr = (BlockTableRecord)tr.GetObject(btrId, OpenMode.ForRead);
                        if (!btr.IsLayout) continue;
                        foreach (ObjectId id in btr)
                        {
                            if (!(tr.GetObject(id, OpenMode.ForRead) is BlockReference br)) continue;
                            foreach (ObjectId attId in br.AttributeCollection)
                            {
                                if (tr.GetObject(attId, OpenMode.ForRead) is AttributeReference att &&
                                    string.Equals(att.Tag, tag, StringComparison.OrdinalIgnoreCase))
                                {
                                    att.UpgradeOpen();
                                    att.TextString = value;
                                }
                            }
                        }
                    }
                    tr.Commit();
                }
            }
        }

        public string ReadMaterial(CadDocument doc) => "";       // the core takes MATERIAL from the title block
        public CadPhysical ReadPhysical(CadDocument doc) => null;
        public IList<CadComponent> ReadStructure(CadDocument doc) => new List<CadComponent>();

        /// <summary>External references could be checked in first; for now the designer checks them in on their own.</summary>
        public IList<CadDocument> ReferencedDocumentsDeepestFirst(CadDocument doc) => new List<CadDocument>();

        /// <summary>DBMOD is not 0 when the active drawing has changes that are not saved.</summary>
        public bool HasUnsavedChanges(CadDocument doc)
        {
            try { return Convert.ToInt32(CadApplication.GetSystemVariable("DBMOD")) != 0; }
            catch (System.Exception) { return true; }   // unsure: save, which is always safe
        }

        public bool Save(CadDocument doc)
        {
            var d = N(doc);
            try
            {
                using (d.LockDocument())
                    d.Database.SaveAs(d.Name, true, DwgVersion.Current, d.Database.SecurityParameters);
                return true;
            }
            catch (System.Exception)
            {
                return false;
            }
        }

        public byte[] ExportPreviewPng(CadDocument doc) => null;
        public byte[] ExportMeshStl(CadDocument doc) => null;

        public byte[] ReadFileBytes(CadDocument doc)
        {
            using (var fs = new FileStream(doc.Path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete))
            using (var ms = new MemoryStream())
            {
                fs.CopyTo(ms);
                return ms.ToArray();
            }
        }

        public void Close(CadDocument doc) => N(doc).CloseAndDiscard();

        public CadDocument Open(string path, CadDocumentKind kind) => Wrap(CadApplication.DocumentManager.Open(path, false));
    }
}
