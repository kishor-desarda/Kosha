// SPDX-License-Identifier: Apache-2.0
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text.RegularExpressions;
using SolidWorks.Interop.sldworks;
using SolidWorks.Interop.swconst;

namespace Kosha.SolidWorks
{
    /// <summary>Reads what Kosha needs from an open SolidWorks document.</summary>
    public static class ModelReader
    {
        static readonly string[] PartNumberKeys = { "PartNo", "Part Number", "PartNumber", "Part No", "Number", "Drawing No" };
        static readonly string[] DescriptionKeys = { "Description", "Title", "Name" };
        static readonly Regex FileNamePn = new Regex(@"^([A-Z0-9]{1,6}(?:-[A-Z0-9]{1,8}){1,4})(?:[\s_]+(.*))?$", RegexOptions.IgnoreCase);

        public static string ActiveConfigName(ModelDoc2 doc)
        {
            try
            {
                var cfg = doc.ConfigurationManager.ActiveConfiguration;
                return cfg != null ? cfg.Name : "";
            }
            catch (Exception) { return ""; }
        }

        /// <summary>Custom properties: configuration-specific values override file-level ones.</summary>
        public static Dictionary<string, string> Properties(ModelDoc2 doc, string config)
        {
            var result = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
            foreach (var cfg in new[] { "", config })
            {
                if (cfg == null) continue;
                CustomPropertyManager mgr;
                try { mgr = doc.Extension.get_CustomPropertyManager(cfg); }
                catch (Exception) { continue; }
                if (mgr == null) continue;
                var names = mgr.GetNames() as object[];
                if (names == null) continue;
                foreach (var n in names)
                {
                    string name = Convert.ToString(n), val, resolved;
                    bool wasResolved, linked;
                    mgr.Get6(name, false, out val, out resolved, out wasResolved, out linked);
                    string v = string.IsNullOrEmpty(resolved) ? val : resolved;
                    if (!string.IsNullOrEmpty(v)) result[name] = v;
                }
            }
            return result;
        }

        public static string Pick(Dictionary<string, string> props, string[] keys)
        {
            foreach (var k in keys)
            {
                string v;
                if (props.TryGetValue(k, out v) && !string.IsNullOrWhiteSpace(v)) return v.Trim();
            }
            return "";
        }

        public static string PartNumberOf(Dictionary<string, string> props, string path)
        {
            string pn = Pick(props, PartNumberKeys);
            if (!string.IsNullOrEmpty(pn)) return pn.ToUpperInvariant();
            var m = FileNamePn.Match(Path.GetFileNameWithoutExtension(path) ?? "");
            return m.Success ? m.Groups[1].Value.ToUpperInvariant() : "";
        }

        public static string DescriptionOf(Dictionary<string, string> props, string path)
        {
            string d = Pick(props, DescriptionKeys);
            if (!string.IsNullOrEmpty(d)) return d;
            var m = FileNamePn.Match(Path.GetFileNameWithoutExtension(path) ?? "");
            return m.Success && m.Groups[2].Success ? m.Groups[2].Value : Path.GetFileNameWithoutExtension(path);
        }

        public static void SetProperty(ModelDoc2 doc, string name, string value)
        {
            var mgr = doc.Extension.get_CustomPropertyManager("");
            mgr.Add3(name, (int)swCustomInfoType_e.swCustomInfoText, value, (int)swCustomPropertyAddOption_e.swCustomPropertyReplaceValue);
        }

        public static string Material(ModelDoc2 doc, string config)
        {
            var part = doc as PartDoc;
            if (part == null) return "";
            try
            {
                string db;
                return part.GetMaterialPropertyName2(config, out db) ?? "";
            }
            catch (Exception) { return ""; }
        }

        /// <summary>Mass (kg), volume (mm³), area (mm²) and sorted bounding box (mm).</summary>
        public static bool Physical(ModelDoc2 doc, out double massKg, out double volumeMm3, out double areaMm2, out double[] boxMm)
        {
            massKg = volumeMm3 = areaMm2 = 0;
            boxMm = new double[] { 0, 0, 0 };
            try
            {
                var mp = doc.Extension.CreateMassProperty();
                if (mp != null)
                {
                    massKg = mp.Mass;
                    volumeMm3 = mp.Volume * 1e9;
                    areaMm2 = mp.SurfaceArea * 1e6;
                }
                double[] b = null;
                var part = doc as PartDoc;
                var asm = doc as AssemblyDoc;
                if (part != null) b = part.GetPartBox(true) as double[];
                else if (asm != null) b = asm.GetBox(0) as double[];
                if (b != null && b.Length >= 6)
                {
                    boxMm = new[] { Math.Abs(b[3] - b[0]) * 1000, Math.Abs(b[4] - b[1]) * 1000, Math.Abs(b[5] - b[2]) * 1000 }
                        .OrderByDescending(x => x).ToArray();
                }
                return mp != null;
            }
            catch (Exception) { return false; }
        }

        /// <summary>Saves a copy of the document as PNG (current view) or STL. Returns bytes or null.</summary>
        public static byte[] ExportCopy(ISldWorks app, ModelDoc2 doc, string extension)
        {
            string tmp = Path.Combine(Path.GetTempPath(), "Kosha_" + Guid.NewGuid().ToString("N") + extension);
            try
            {
                if (extension == ".stl")
                {
                    app.SetUserPreferenceToggle((int)swUserPreferenceToggle_e.swSTLBinaryFormat, true);
                    app.SetUserPreferenceToggle((int)swUserPreferenceToggle_e.swSTLComponentsIntoOneFile, true);
                    app.SetUserPreferenceIntegerValue((int)swUserPreferenceIntegerValue_e.swSTLQuality, (int)swSTLQuality_e.swSTLQuality_Coarse);
                    app.SetUserPreferenceIntegerValue((int)swUserPreferenceIntegerValue_e.swExportStlUnits, (int)swLengthUnit_e.swMM);
                }
                int errors = 0, warnings = 0;
                bool ok = doc.Extension.SaveAs(tmp, (int)swSaveAsVersion_e.swSaveAsCurrentVersion,
                    (int)(swSaveAsOptions_e.swSaveAsOptions_Silent | swSaveAsOptions_e.swSaveAsOptions_Copy), null, ref errors, ref warnings);
                if (!ok || !File.Exists(tmp)) return null;
                return File.ReadAllBytes(tmp);
            }
            catch (Exception) { return null; }
            finally
            {
                try { if (File.Exists(tmp)) File.Delete(tmp); } catch (Exception) { }
            }
        }

        /// <summary>Read the file even while SolidWorks has it open.</summary>
        public static byte[] ReadFileShared(ModelDoc2 doc, string path)
        {
            try
            {
                using (var fs = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete))
                using (var ms = new MemoryStream())
                {
                    fs.CopyTo(ms);
                    return ms.ToArray();
                }
            }
            catch (IOException)
            {
                // locked: save a copy and read that instead
                string tmp = Path.Combine(Path.GetTempPath(), "Kosha_" + Guid.NewGuid().ToString("N") + Path.GetExtension(path));
                int e = 0, w = 0;
                doc.Extension.SaveAs(tmp, (int)swSaveAsVersion_e.swSaveAsCurrentVersion,
                    (int)(swSaveAsOptions_e.swSaveAsOptions_Silent | swSaveAsOptions_e.swSaveAsOptions_Copy), null, ref e, ref w);
                var bytes = File.ReadAllBytes(tmp);
                File.Delete(tmp);
                return bytes;
            }
        }

        /// <summary>
        /// Direct children of an assembly, merged by file + configuration, with quantities.
        /// Suppressed, envelope and "exclude from BOM" components are skipped.
        /// </summary>
        public static List<Dictionary<string, object>> Structure(AssemblyDoc asm)
        {
            var rows = new Dictionary<string, Dictionary<string, object>>(StringComparer.OrdinalIgnoreCase);
            try { asm.ResolveAllLightWeightComponents(false); } catch (Exception) { }
            var comps = asm.GetComponents(true) as object[];
            if (comps == null) return new List<Dictionary<string, object>>();
            foreach (var o in comps)
            {
                var c = o as Component2;
                if (c == null) continue;
                if (c.IsSuppressed() || c.IsEnvelope() || c.ExcludeFromBOM) continue;
                string path = c.GetPathName();
                string cfg = c.ReferencedConfiguration ?? "";
                string key = path + "|" + cfg;
                Dictionary<string, object> row;
                if (rows.TryGetValue(key, out row))
                {
                    row["qty"] = (int)row["qty"] + 1;
                    continue;
                }
                var model = c.GetModelDoc2() as ModelDoc2;
                var props = model != null ? Properties(model, cfg) : new Dictionary<string, string>();
                rows[key] = new Dictionary<string, object>
                {
                    { "file", Path.GetFileName(path) },
                    { "configuration", cfg },
                    { "part_number", PartNumberOf(props, path) },
                    { "description", DescriptionOf(props, path) },
                    { "material", model != null ? Material(model, cfg) : "" },
                    { "qty", 1 },
                    { "virtual", c.IsVirtual },
                };
            }
            return rows.Values.ToList();
        }

        /// <summary>All referenced files below an assembly, deepest first (check-in order).</summary>
        public static List<Tuple<string, ModelDoc2>> ComponentsDeepestFirst(AssemblyDoc asm)
        {
            var depth = new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase);
            var docs = new Dictionary<string, ModelDoc2>(StringComparer.OrdinalIgnoreCase);
            Walk(asm, 1, depth, docs, new HashSet<string>(StringComparer.OrdinalIgnoreCase));
            return depth.OrderByDescending(kv => kv.Value).Select(kv => Tuple.Create(kv.Key, docs[kv.Key])).ToList();
        }

        static void Walk(AssemblyDoc asm, int level, Dictionary<string, int> depth, Dictionary<string, ModelDoc2> docs, HashSet<string> stack)
        {
            var comps = asm.GetComponents(true) as object[];
            if (comps == null) return;
            foreach (var o in comps)
            {
                var c = o as Component2;
                if (c == null || c.IsSuppressed() || c.IsVirtual) continue;
                string path = c.GetPathName();
                var model = c.GetModelDoc2() as ModelDoc2;
                if (model == null || string.IsNullOrEmpty(path)) continue;
                int d;
                if (!depth.TryGetValue(path, out d) || d < level) depth[path] = level;
                docs[path] = model;
                var sub = model as AssemblyDoc;
                if (sub != null && stack.Add(path))
                {
                    Walk(sub, level + 1, depth, docs, stack);
                    stack.Remove(path);
                }
            }
        }
    }
}
