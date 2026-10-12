// SPDX-License-Identifier: Apache-2.0
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using Kosha.CadCore;
using SolidWorks.Interop.sldworks;
using SolidWorks.Interop.swconst;

namespace Kosha.SolidWorks
{
    /// <summary>SolidWorks answers for the shared CAD core. Called on SolidWorks' main thread only.</summary>
    public sealed class SolidWorksHost : ICadHost
    {
        readonly ISldWorks app;

        public SolidWorksHost(ISldWorks app) { this.app = app; }

        public string ProgramName => "SOLIDWORKS " + SafeRevision();
        public CadTheme Theme => CadTheme.Light;

        string SafeRevision()
        {
            try { return app.RevisionNumber(); } catch (Exception) { return ""; }
        }

        static CadDocumentKind KindOf(ModelDoc2 doc)
        {
            switch (doc.GetType())
            {
                case (int)swDocumentTypes_e.swDocPART: return CadDocumentKind.Part;
                case (int)swDocumentTypes_e.swDocASSEMBLY: return CadDocumentKind.Assembly;
                case (int)swDocumentTypes_e.swDocDRAWING: return CadDocumentKind.Drawing;
                default: return CadDocumentKind.Other;
            }
        }

        static CadDocument Wrap(ModelDoc2 doc) => doc == null ? null : new CadDocument(doc.GetPathName(), doc.GetTitle(), KindOf(doc), doc);
        static ModelDoc2 M(CadDocument d) => (ModelDoc2)d.Native;

        public CadDocument ActiveDocument => Wrap(app.ActiveDoc as ModelDoc2);

        public string ActiveConfiguration(CadDocument doc)
        {
            try { return M(doc).ConfigurationManager.ActiveConfiguration?.Name ?? ""; }
            catch (Exception) { return ""; }
        }

        /// <summary>Custom properties: configuration-specific values override file-level ones.</summary>
        public IDictionary<string, string> ReadProperties(CadDocument doc) => Properties(M(doc), ActiveConfiguration(doc));

        static Dictionary<string, string> Properties(ModelDoc2 doc, string config)
        {
            var result = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
            foreach (var cfg in new[] { "", config })
            {
                if (cfg == null) continue;
                CustomPropertyManager mgr;
                try { mgr = doc.Extension.get_CustomPropertyManager(cfg); }
                catch (Exception) { continue; }
                if (!(mgr?.GetNames() is object[] names)) continue;
                foreach (var n in names)
                {
                    string name = Convert.ToString(n);
                    mgr.Get6(name, false, out string val, out string resolved, out bool _, out bool _);
                    string v = string.IsNullOrEmpty(resolved) ? val : resolved;
                    if (!string.IsNullOrEmpty(v)) result[name] = v;
                }
            }
            return result;
        }

        public void WriteProperty(CadDocument doc, string name, string value)
        {
            var mgr = M(doc).Extension.get_CustomPropertyManager("");
            mgr.Add3(name, (int)swCustomInfoType_e.swCustomInfoText, value, (int)swCustomPropertyAddOption_e.swCustomPropertyReplaceValue);
        }

        public string ReadMaterial(CadDocument doc) => Material(M(doc), ActiveConfiguration(doc));

        static string Material(ModelDoc2 doc, string config)
        {
            if (!(doc is PartDoc part)) return "";
            try { return part.GetMaterialPropertyName2(config, out string _) ?? ""; }
            catch (Exception) { return ""; }
        }

        public CadPhysical ReadPhysical(CadDocument doc)
        {
            try
            {
                var m = M(doc);
                var mp = m.Extension.CreateMassProperty();
                if (mp == null) return null;
                double[] b = m is PartDoc part ? part.GetPartBox(true) as double[] : m is AssemblyDoc asm ? asm.GetBox(0) as double[] : null;
                var box = b != null && b.Length >= 6
                    ? new[] { Math.Abs(b[3] - b[0]) * 1000, Math.Abs(b[4] - b[1]) * 1000, Math.Abs(b[5] - b[2]) * 1000 }.OrderByDescending(x => x).ToArray()
                    : new double[3];
                return new CadPhysical { MassKg = mp.Mass, VolumeMm3 = mp.Volume * 1e9, AreaMm2 = mp.SurfaceArea * 1e6, BoxMm = box };
            }
            catch (Exception) { return null; }
        }

        /// <summary>Direct children merged by file and configuration. Suppressed, envelope and excluded-from-BOM parts are skipped.</summary>
        public IList<CadComponent> ReadStructure(CadDocument doc)
        {
            var rows = new Dictionary<string, CadComponent>(StringComparer.OrdinalIgnoreCase);
            if (!(M(doc) is AssemblyDoc asm)) return new List<CadComponent>();
            try { asm.ResolveAllLightWeightComponents(false); } catch (Exception) { }
            if (!(asm.GetComponents(true) is object[] comps)) return new List<CadComponent>();
            foreach (var o in comps)
            {
                if (!(o is Component2 c) || c.IsSuppressed() || c.IsEnvelope() || c.ExcludeFromBOM) continue;
                string path = c.GetPathName();
                string cfg = c.ReferencedConfiguration ?? "";
                string key = path + "|" + cfg;
                if (rows.TryGetValue(key, out var row)) { row.Quantity++; continue; }
                var model = c.GetModelDoc2() as ModelDoc2;
                rows[key] = new CadComponent
                {
                    Path = path, Configuration = cfg, Quantity = 1, IsVirtual = c.IsVirtual,
                    Properties = model != null ? Properties(model, cfg) : new Dictionary<string, string>(),
                    Material = model != null ? Material(model, cfg) : "",
                };
            }
            return rows.Values.ToList();
        }

        public IList<CadDocument> ReferencedDocumentsDeepestFirst(CadDocument doc)
        {
            var depth = new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase);
            var docs = new Dictionary<string, ModelDoc2>(StringComparer.OrdinalIgnoreCase);
            if (M(doc) is AssemblyDoc asm) Walk(asm, 1, depth, docs, new HashSet<string>(StringComparer.OrdinalIgnoreCase));
            return depth.OrderByDescending(kv => kv.Value).Select(kv => Wrap(docs[kv.Key])).ToList();
        }

        static void Walk(AssemblyDoc asm, int level, Dictionary<string, int> depth, Dictionary<string, ModelDoc2> docs, HashSet<string> stack)
        {
            if (!(asm.GetComponents(true) is object[] comps)) return;
            foreach (var o in comps)
            {
                if (!(o is Component2 c) || c.IsSuppressed() || c.IsVirtual) continue;
                string path = c.GetPathName();
                if (!(c.GetModelDoc2() is ModelDoc2 model) || string.IsNullOrEmpty(path)) continue;
                if (!depth.TryGetValue(path, out int d) || d < level) depth[path] = level;
                docs[path] = model;
                if (model is AssemblyDoc sub && stack.Add(path))
                {
                    Walk(sub, level + 1, depth, docs, stack);
                    stack.Remove(path);
                }
            }
        }

        public bool HasUnsavedChanges(CadDocument doc) => M(doc).GetSaveFlag();

        public bool Save(CadDocument doc)
        {
            int e = 0, w = 0;
            return M(doc).Save3((int)swSaveAsOptions_e.swSaveAsOptions_Silent, ref e, ref w);
        }

        public byte[] ExportPreviewPng(CadDocument doc) => ExportCopy(doc, ".png");

        public byte[] ExportMeshStl(CadDocument doc)
        {
            app.SetUserPreferenceToggle((int)swUserPreferenceToggle_e.swSTLBinaryFormat, true);
            app.SetUserPreferenceToggle((int)swUserPreferenceToggle_e.swSTLComponentsIntoOneFile, true);
            app.SetUserPreferenceIntegerValue((int)swUserPreferenceIntegerValue_e.swSTLQuality, (int)swSTLQuality_e.swSTLQuality_Coarse);
            app.SetUserPreferenceIntegerValue((int)swUserPreferenceIntegerValue_e.swExportStlUnits, (int)swLengthUnit_e.swMM);
            return ExportCopy(doc, ".stl");
        }

        static byte[] ExportCopy(CadDocument doc, string extension)
        {
            string tmp = Path.Combine(Path.GetTempPath(), "Kosha_" + Guid.NewGuid().ToString("N") + extension);
            try
            {
                int errors = 0, warnings = 0;
                bool ok = M(doc).Extension.SaveAs(tmp, (int)swSaveAsVersion_e.swSaveAsCurrentVersion,
                    (int)(swSaveAsOptions_e.swSaveAsOptions_Silent | swSaveAsOptions_e.swSaveAsOptions_Copy), null, ref errors, ref warnings);
                return ok && File.Exists(tmp) ? File.ReadAllBytes(tmp) : null;
            }
            catch (Exception) { return null; }
            finally
            {
                try { if (File.Exists(tmp)) File.Delete(tmp); } catch (Exception) { }
            }
        }

        public byte[] ReadFileBytes(CadDocument doc)
        {
            try
            {
                using (var fs = new FileStream(doc.Path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete))
                using (var ms = new MemoryStream())
                {
                    fs.CopyTo(ms);
                    return ms.ToArray();
                }
            }
            catch (IOException)
            {
                return ExportCopy(doc, Path.GetExtension(doc.Path));   // locked: save a copy and read that instead
            }
        }

        public void Close(CadDocument doc) => app.CloseDoc(M(doc).GetTitle());

        public CadDocument Open(string path, CadDocumentKind kind)
        {
            int type = kind == CadDocumentKind.Assembly ? (int)swDocumentTypes_e.swDocASSEMBLY
                     : kind == CadDocumentKind.Drawing ? (int)swDocumentTypes_e.swDocDRAWING : (int)swDocumentTypes_e.swDocPART;
            int errs = 0, warns = 0;
            return Wrap(app.OpenDoc6(path, type, (int)swOpenDocOptions_e.swOpenDocOptions_Silent, "", ref errs, ref warns));
        }
    }
}
