// SPDX-License-Identifier: Apache-2.0
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using Kosha.Client;

namespace Kosha.Desktop.Core
{
    /// <summary>
    /// The other direction of <see cref="KoshaSettings.KoshaFolderFor"/>: where a Kosha file lives on this PC.
    /// The Kosha folder "Kosha/Products/Mixers" is the local folder C:\Kosha\Products\Mixers when the workspace is C:\Kosha.
    /// </summary>
    public static class WorkspaceMap
    {
        /// <summary>Full paths ("Kosha/Products/Mixers") of every folder, by id. Loops and missing parents end the path.</summary>
        public static Dictionary<int, string> FolderPaths(IEnumerable<FolderInfo> folders)
        {
            var byId = new Dictionary<int, FolderInfo>();
            foreach (var f in folders ?? Enumerable.Empty<FolderInfo>())
                if (f != null) byId[f.Id] = f;
            var result = new Dictionary<int, string>();
            foreach (var f in byId.Values)
            {
                var names = new List<string>();
                var seen = new HashSet<int>();
                var cur = f;
                while (cur != null && seen.Add(cur.Id))
                {
                    names.Insert(0, cur.Name ?? "");
                    cur = cur.ParentId.HasValue && byId.TryGetValue(cur.ParentId.Value, out var parent) ? parent : null;
                }
                result[f.Id] = string.Join("/", names);
            }
            return result;
        }

        /// <summary>
        /// The local path of a file in a Kosha folder, or null when the folder is the root (files there have no
        /// place in the workspace) or a folder name could not be a Windows folder name.
        /// </summary>
        public static string LocalPathFor(KoshaSettings settings, string koshaFolder, string fileName)
        {
            if (settings == null || string.IsNullOrWhiteSpace(settings.WorkspaceRoot) || string.IsNullOrWhiteSpace(fileName)) return null;
            var parts = (koshaFolder ?? "").Split(new[] { '/', '\\' }, StringSplitOptions.RemoveEmptyEntries).ToList();
            if (parts.Count > 0) parts.RemoveAt(0);            // the root folder, "Kosha", is the workspace itself
            if (parts.Count == 0) return null;
            var bad = Path.GetInvalidFileNameChars();
            if (parts.Any(p => p == "." || p == ".." || p.IndexOfAny(bad) >= 0) || fileName.IndexOfAny(bad) >= 0) return null;
            return Path.Combine(new[] { settings.WorkspaceRoot }.Concat(parts).Concat(new[] { fileName }).ToArray());
        }
    }
}
