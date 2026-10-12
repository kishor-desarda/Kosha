// SPDX-License-Identifier: Apache-2.0
using System;
using System.Collections.Generic;
using System.Text.RegularExpressions;

namespace Kosha.CadCore
{
    /// <summary>Finds the part number and description of a document: properties first, then the file name.</summary>
    public static class PartNumbers
    {
        public static readonly string[] PartNumberKeys =
            { "PartNo", "Part Number", "PartNumber", "Part No", "PART_NO", "Number", "Drawing No", "DRAWING_NO", "DWG_NO" };
        public static readonly string[] DescriptionKeys = { "Description", "DESCRIPTION", "Title", "TITLE", "Name" };
        public static readonly string[] MaterialKeys = { "Material", "MATERIAL" };

        /// <summary>Same pattern as the server's default: "MX-100-020 Agitator Shaft" → MX-100-020 and "Agitator Shaft".</summary>
        static readonly Regex FileNamePattern =
            new Regex(@"^([A-Z0-9]{1,6}(?:-[A-Z0-9]{1,8}){1,4})(?:[\s_]+(.*))?$", RegexOptions.IgnoreCase | RegexOptions.CultureInvariant);

        public static string Pick(IDictionary<string, string> props, IEnumerable<string> keys)
        {
            if (props == null) return "";
            foreach (var k in keys)
            {
                foreach (var kv in props)
                {
                    if (string.Equals(kv.Key, k, StringComparison.OrdinalIgnoreCase) && !string.IsNullOrWhiteSpace(kv.Value))
                        return kv.Value.Trim();
                }
            }
            return "";
        }

        public static string PartNumberOf(IDictionary<string, string> props, string path)
        {
            string pn = Pick(props, PartNumberKeys);
            if (pn.Length > 0) return pn.ToUpperInvariant();
            var m = FileNamePattern.Match(System.IO.Path.GetFileNameWithoutExtension(path ?? "") ?? "");
            return m.Success ? m.Groups[1].Value.ToUpperInvariant() : "";
        }

        public static string DescriptionOf(IDictionary<string, string> props, string path)
        {
            string d = Pick(props, DescriptionKeys);
            if (d.Length > 0) return d;
            string stem = System.IO.Path.GetFileNameWithoutExtension(path ?? "") ?? "";
            var m = FileNamePattern.Match(stem);
            return m.Success && m.Groups[2].Success ? m.Groups[2].Value.Trim() : stem;
        }
    }
}
