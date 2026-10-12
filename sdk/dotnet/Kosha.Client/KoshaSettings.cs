// SPDX-License-Identifier: Apache-2.0
using System;
using System.IO;
using Newtonsoft.Json;

namespace Kosha.Client
{
    /// <summary>
    /// Per-user settings, stored in %APPDATA%\Kosha\settings.json (the token is protected, never plain text).
    /// WorkspaceRoot is the local folder that mirrors Kosha: C:\Kosha\Products\Mixers\X.SLDPRT
    /// belongs to the Kosha folder "Kosha/Products/Mixers".
    /// </summary>
    public sealed class KoshaSettings
    {
        public string ServerUrl { get; set; } = "http://kosha-server:8080";
        public string WorkspaceRoot { get; set; } = @"C:\Kosha";
        public string Username { get; set; } = "";
        public string PartNumberPrefix { get; set; } = "";
        public bool ExportMesh { get; set; } = true;
        public string ProtectedToken { get; set; } = "";

        [JsonIgnore] public string Token { get; set; } = "";
        [JsonIgnore] public string FilePath { get; private set; }

        public static string DefaultFolder =>
            Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData), "Kosha");

        /// <summary>Loads settings; a missing or damaged file gives defaults instead of an error.</summary>
        public static KoshaSettings Load(string filePath, ITokenProtector protector)
        {
            KoshaSettings s = null;
            try
            {
                if (File.Exists(filePath)) s = JsonConvert.DeserializeObject<KoshaSettings>(File.ReadAllText(filePath));
            }
            catch (Exception)
            {
                s = null;   // damaged file: start from defaults; the next Save repairs it
            }
            s = s ?? new KoshaSettings();
            s.FilePath = filePath;
            s.Token = protector.Unprotect(s.ProtectedToken);
            return s;
        }

        /// <summary>Writes to a temporary file first and then replaces the old one, so a crash never leaves half a file.</summary>
        public void Save(ITokenProtector protector)
        {
            if (string.IsNullOrEmpty(FilePath)) return;
            ProtectedToken = protector.Protect(Token);
            Directory.CreateDirectory(Path.GetDirectoryName(FilePath));
            string tmp = FilePath + ".tmp";
            File.WriteAllText(tmp, JsonConvert.SerializeObject(this, Formatting.Indented));
            if (File.Exists(FilePath)) File.Replace(tmp, FilePath, null);
            else File.Move(tmp, FilePath);
        }

        /// <summary>Kosha folder for a local file, or null when the file is not inside a sub-folder of the workspace.</summary>
        public string KoshaFolderFor(string localFile)
        {
            if (string.IsNullOrEmpty(localFile) || string.IsNullOrEmpty(WorkspaceRoot)) return null;
            char sep = Path.DirectorySeparatorChar;
            string root = Path.GetFullPath(WorkspaceRoot).TrimEnd('\\', '/') + sep;
            string dir = Path.GetDirectoryName(Path.GetFullPath(localFile)) + sep;
            var cmp = sep == '\\' ? StringComparison.OrdinalIgnoreCase : StringComparison.Ordinal;
            if (!dir.StartsWith(root, cmp)) return null;
            string rel = dir.Substring(root.Length).Trim('\\', '/');
            if (rel.Length == 0) return null;
            return "Kosha/" + rel.Replace('\\', '/');
        }
    }
}
