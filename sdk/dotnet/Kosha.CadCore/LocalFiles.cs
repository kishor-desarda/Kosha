// SPDX-License-Identifier: Apache-2.0
using System;
using System.IO;

namespace Kosha.CadCore
{
    /// <summary>File operations on the designer's PC, written so that nothing is ever lost.</summary>
    public static class LocalFiles
    {
        public static void SetReadOnly(string path, bool readOnly)
        {
            try
            {
                if (string.IsNullOrEmpty(path) || !File.Exists(path)) return;
                var attrs = File.GetAttributes(path);
                File.SetAttributes(path, readOnly ? attrs | FileAttributes.ReadOnly : attrs & ~FileAttributes.ReadOnly);
            }
            catch (IOException) { /* best effort: read-only is a reminder, not a lock */ }
            catch (UnauthorizedAccessException) { }
        }

        public static bool IsReadOnly(string path) =>
            File.Exists(path) && (File.GetAttributes(path) & FileAttributes.ReadOnly) != 0;

        /// <summary>
        /// Replaces a local file with new content. The old file is first copied to <paramref name="keepFolder"/>,
        /// the new content is written to a temporary file next to the target and then swapped in, so a crash
        /// or a full disk leaves either the old or the new file, never half of one. Returns the kept copy's path.
        /// </summary>
        public static string ReplaceKeepingOld(string path, byte[] content, string keepFolder)
        {
            string kept = null;
            if (File.Exists(path))
            {
                Directory.CreateDirectory(keepFolder);
                kept = Path.Combine(keepFolder, DateTime.Now.ToString("yyyyMMdd-HHmmss") + "_" + Path.GetFileName(path));
                File.Copy(path, kept, overwrite: true);
                SetReadOnly(path, false);
            }
            string tmp = path + ".kosha-new";
            File.WriteAllBytes(tmp, content);
            if (File.Exists(path)) File.Replace(tmp, path, null);
            else File.Move(tmp, path);
            return kept;
        }

        public static string DefaultKeepFolder =>
            Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Kosha", "replaced-files");
    }
}
