// SPDX-License-Identifier: Apache-2.0
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Threading;
using Newtonsoft.Json;

namespace Kosha.Desktop.Core
{
    /// <summary>One thing Kosha Desktop did, shown in the tray's sync status list.</summary>
    public sealed class ActivityEntry
    {
        [JsonProperty("at")] public DateTime AtUtc { get; set; } = DateTime.UtcNow;
        [JsonProperty("action")] public string Action { get; set; } = "";
        [JsonProperty("path")] public string Path { get; set; } = "";
        [JsonProperty("ok")] public bool Ok { get; set; }
        [JsonProperty("message")] public string Message { get; set; } = "";

        [JsonIgnore] public string FileName => System.IO.Path.GetFileName(Path ?? "");
    }

    /// <summary>
    /// The recent actions of every Kosha Desktop process (each Explorer verb runs in a process of its own), newest first,
    /// in %LOCALAPPDATA%\Kosha\desktop-activity.json. It is a convenience: reading and writing never throw, and a
    /// damaged file starts an empty list. Writers take turns through a lock file and replace the list in one step.
    /// </summary>
    public sealed class ActivityLog
    {
        readonly string file;
        readonly int max;

        public ActivityLog(string file, int max = 200)
        {
            this.file = file ?? throw new ArgumentNullException(nameof(file));
            this.max = Math.Max(1, max);
        }

        public string FilePath => file;

        public static string DefaultPath =>
            System.IO.Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Kosha", "desktop-activity.json");

        public List<ActivityEntry> Read()
        {
            try
            {
                if (!File.Exists(file)) return new List<ActivityEntry>();
                string json;
                using (var fs = new FileStream(file, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete))
                using (var reader = new StreamReader(fs))
                    json = reader.ReadToEnd();
                return (JsonConvert.DeserializeObject<List<ActivityEntry>>(json) ?? new List<ActivityEntry>())
                    .Where(e => e != null).OrderByDescending(e => e.AtUtc).ToList();
            }
            catch (Exception)
            {
                return new List<ActivityEntry>();   // damaged or unreadable: the next Add starts it again
            }
        }

        /// <summary>Adds an entry; returns false when the list could not be written (the action itself is not affected).</summary>
        public bool Add(ActivityEntry entry)
        {
            if (entry == null) return false;
            try
            {
                Directory.CreateDirectory(System.IO.Path.GetDirectoryName(System.IO.Path.GetFullPath(file)));
                using (TakeLock())
                {
                    var list = Read();
                    list.Insert(0, entry);
                    string tmp = file + ".tmp";
                    File.WriteAllText(tmp, JsonConvert.SerializeObject(list.Take(max).ToList(), Formatting.Indented));
                    if (File.Exists(file)) File.Replace(tmp, file, null);
                    else File.Move(tmp, file);
                }
                return true;
            }
            catch (Exception)
            {
                return false;
            }
        }

        /// <summary>A lock file that only one process can hold; waits up to five seconds for another writer.</summary>
        IDisposable TakeLock()
        {
            string lockFile = file + ".lock";
            for (int attempt = 0; ; attempt++)
            {
                try
                {
                    return new FileStream(lockFile, FileMode.OpenOrCreate, FileAccess.ReadWrite, FileShare.None, 1, FileOptions.DeleteOnClose);
                }
                catch (IOException) when (attempt < 50)
                {
                    Thread.Sleep(100);
                }
            }
        }
    }
}
