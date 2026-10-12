// SPDX-License-Identifier: Apache-2.0
using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using Kosha.CadCore;

namespace Kosha.GstarCad
{
    /// <summary>
    /// GStarCAD answers for the shared CAD core. Drawings only: part number, description and material come
    /// from the drawing's custom properties and from title-block attributes (PART_NO, DESCRIPTION, MATERIAL ...).
    /// Previews are made on the Kosha server from the DWG itself, so none is exported here.
    /// Called on GStarCAD's main thread only.
    /// </summary>
    public sealed class GstarCadHost : ICadHost
    {
        static readonly HashSet<string> TitleBlockTags = new HashSet<string>(StringComparer.OrdinalIgnoreCase)
        {
            "PART_NO", "PARTNO", "DRAWING_NO", "DWG_NO", "DESCRIPTION", "TITLE", "MATERIAL", "REV", "FINISH",
        };

        /// <summary>Kosha property name → title-block tag, so values written by Kosha also show on the drawing.</summary>
        static readonly Dictionary<string, string> TagFor = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase)
        {
            ["PartNo"] = "PART_NO", ["Description"] = "DESCRIPTION", ["Material"] = "MATERIAL",
        };

        public string ProgramName
        {
            get
            {
                try { return "GStarCAD " + CadApplication.Version; }
                catch (System.Exception) { return "GStarCAD"; }
            }
        }

        public CadTheme Theme
        {
            get
            {
                try { return Convert.ToInt32(CadApplication.GetSystemVariable("COLORTHEME")) == 0 ? CadTheme.Dark : CadTheme.Light; }
                catch (System.Exception) { return CadTheme.Light; }
            }
        }

        static CadDocument Wrap(CadDocumentNative d) =>
            d == null ? null : new CadDocument(File.Exists(d.Name) ? d.Name : "", Path.GetFileName(d.Name), CadDocumentKind.Drawing, d);

        static CadDocumentNative N(CadDocument d) => (CadDocumentNative)d.Native;

        public CadDocument ActiveDocument => Wrap(CadApplication.DocumentManager.MdiActiveDocument);

        public string ActiveConfiguration(CadDocument doc) => "";

        public IDictionary<string, string> ReadProperties(CadDocument doc)
        {
            var result = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
            var db = N(doc).Database;
            try
            {
                var custom = db.SummaryInfo.CustomProperties;
                while (custom.MoveNext())
                {
                    var e = (DictionaryEntry)custom.Current;
                    string v = Convert.ToString(e.Value);
                    if (!string.IsNullOrWhiteSpace(v)) result[Convert.ToString(e.Key)] = v;
                }
                if (!string.IsNullOrWhiteSpace(db.SummaryInfo.Title) && !result.ContainsKey("Title")) result["Title"] = db.SummaryInfo.Title;
            }
            catch (System.Exception) { /* drawings without summary info are normal */ }
            foreach (var kv in TitleBlockValues(db))
                if (!result.ContainsKey(kv.Key)) result[kv.Key] = kv.Value;
            return result;
        }

        /// <summary>Attribute values of title-block tags found on block references in every layout.</summary>
        static Dictionary<string, string> TitleBlockValues(Database db)
        {
            var values = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
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
                                TitleBlockTags.Contains(att.Tag) && !string.IsNullOrWhiteSpace(att.TextString) && !values.ContainsKey(att.Tag))
                                values[att.Tag] = att.TextString.Trim();
                        }
                    }
                }
                tr.Commit();
            }
            return values;
        }

        public void WriteProperty(CadDocument doc, string name, string value)
        {
            var d = N(doc);
            using (d.LockDocument())
            {
                var db = d.Database;
                var builder = new DatabaseSummaryInfoBuilder(db.SummaryInfo);
                builder.CustomPropertyTable[name] = value;
                db.SummaryInfo = builder.ToDatabaseSummaryInfo();
                if (!TagFor.TryGetValue(name, out string tag)) return;
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
                                if (tr.GetObject(attId, OpenMode.ForRead) is AttributeReference att && string.Equals(att.Tag, tag, StringComparison.OrdinalIgnoreCase))
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

        public string ReadMaterial(CadDocument doc) => "";       // taken from MATERIAL in the title block by the core
        public CadPhysical ReadPhysical(CadDocument doc) => null;
        public IList<CadComponent> ReadStructure(CadDocument doc) => new List<CadComponent>();

        /// <summary>External references could be checked in first; the starter add-in leaves them to the user.</summary>
        public IList<CadDocument> ReferencedDocumentsDeepestFirst(CadDocument doc) => new List<CadDocument>();

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
            catch (System.Exception) { return false; }
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
