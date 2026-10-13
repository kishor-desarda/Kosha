// SPDX-License-Identifier: Apache-2.0
using System;
using System.Collections.Generic;

namespace Kosha.CadCore
{
    /// <summary>
    /// Rules for DWG drawings (AutoCAD, GStarCAD ...), kept free of any CAD API so they are tested in CI:
    /// which title-block attribute tags Kosha reads, which tag a Kosha property is also written to, and
    /// how drawing properties and title-block values are merged. The CAD-specific walk over the drawing
    /// is in Kosha.CadCore.Dwg/DwgHostLogic.cs.
    /// </summary>
    public static class TitleBlocks
    {
        static readonly HashSet<string> Tags = new HashSet<string>(StringComparer.OrdinalIgnoreCase)
        {
            "PART_NO", "PARTNO", "DRAWING_NO", "DWG_NO", "DESCRIPTION", "TITLE", "MATERIAL", "REV", "FINISH",
        };

        /// <summary>Kosha property name → title-block tag, so values written by Kosha also show on the drawing.</summary>
        static readonly Dictionary<string, string> TagForProperty = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase)
        {
            ["PartNo"] = "PART_NO",
            ["Description"] = "DESCRIPTION",
            ["Material"] = "MATERIAL",
        };

        public static IEnumerable<string> KnownTags => Tags;

        public static bool IsKnownTag(string tag) => !string.IsNullOrWhiteSpace(tag) && Tags.Contains(tag.Trim());

        /// <summary>The title-block tag a Kosha property is also written to, or null when it has none.</summary>
        public static string TagFor(string propertyName) =>
            propertyName != null && TagForProperty.TryGetValue(propertyName.Trim(), out var tag) ? tag : null;

        /// <summary>
        /// The properties of a drawing, case-insensitive: drawing (DWGPROPS) custom properties first, then the
        /// drawing title from the summary, then title-block values for names not set by either. Empty values are skipped.
        /// </summary>
        public static Dictionary<string, string> Merge(IEnumerable<KeyValuePair<string, string>> customProperties, string summaryTitle,
                                                       IEnumerable<KeyValuePair<string, string>> titleBlock)
        {
            var result = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
            foreach (var kv in customProperties ?? new KeyValuePair<string, string>[0])
            {
                if (!string.IsNullOrWhiteSpace(kv.Key) && !string.IsNullOrWhiteSpace(kv.Value)) result[kv.Key.Trim()] = kv.Value.Trim();
            }
            if (!string.IsNullOrWhiteSpace(summaryTitle) && !result.ContainsKey("Title")) result["Title"] = summaryTitle.Trim();
            foreach (var kv in titleBlock ?? new KeyValuePair<string, string>[0])
            {
                if (!string.IsNullOrWhiteSpace(kv.Key) && !string.IsNullOrWhiteSpace(kv.Value) && !result.ContainsKey(kv.Key.Trim()))
                    result[kv.Key.Trim()] = kv.Value.Trim();
            }
            return result;
        }
    }

    /// <summary>
    /// Collects title-block values while a CAD host walks the block references of a drawing:
    /// only known tags are kept, and the first non-empty value of each tag wins.
    /// </summary>
    public sealed class TitleBlockReader
    {
        readonly Dictionary<string, string> values = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);

        public void Add(string tag, string text)
        {
            if (!TitleBlocks.IsKnownTag(tag) || string.IsNullOrWhiteSpace(text)) return;
            string key = tag.Trim();
            if (!values.ContainsKey(key)) values[key] = text.Trim();
        }

        public IDictionary<string, string> Values => values;
    }
}
