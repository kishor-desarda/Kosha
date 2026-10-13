// SPDX-License-Identifier: Apache-2.0
using System.Collections.Generic;
using System.Linq;

namespace Kosha.Desktop.Core
{
    /// <summary>One registry value under HKEY_CURRENT_USER. An empty <see cref="Name"/> is the key's default value.</summary>
    public sealed class RegistryEntry
    {
        public RegistryEntry(string key, string name, string value)
        {
            Key = key;
            Name = name;
            Value = value;
        }

        public string Key { get; }
        public string Name { get; }
        public string Value { get; }

        public override string ToString() => Key + " : " + (Name.Length == 0 ? "(Default)" : Name) + " = " + Value;
    }

    /// <summary>
    /// The Explorer right-click verbs and the start-up entry, per Windows user (HKEY_CURRENT_USER, so no administrator
    /// rights are needed). Written by  Kosha.Desktop.exe register  and removed by  Kosha.Desktop.exe unregister.
    /// The "*" key is the registry's own name for "every file type", not a wildcard.
    /// </summary>
    public static class ExplorerVerbs
    {
        public const string ShellKey = @"Software\Classes\*\shell";
        public const string RunKey = @"Software\Microsoft\Windows\CurrentVersion\Run";
        public const string RunValueName = "Kosha Desktop";

        /// <summary>Registry key name, menu text and command-line verb of each right-click entry, in menu order.</summary>
        public static readonly IReadOnlyList<(string Key, string Label, DesktopVerb Verb)> Verbs = new[]
        {
            ("KoshaCheckIn", "Check in to Kosha", DesktopVerb.CheckIn),
            ("KoshaCheckOut", "Check out from Kosha", DesktopVerb.CheckOut),
            ("KoshaShow", "Show in Kosha", DesktopVerb.Show),
            ("KoshaGetLatest", "Get latest version from Kosha", DesktopVerb.GetLatest),
        };

        public static IEnumerable<string> VerbKeys => Verbs.Select(v => ShellKey + @"\" + v.Key);

        static string Quote(string path) => "\"" + path + "\"";

        /// <summary>The command Explorer runs for a verb: "C:\...\Kosha.Desktop.exe" checkin "%1".</summary>
        public static string CommandFor(string exePath, DesktopVerb verb) =>
            Quote(exePath) + " " + DesktopCommand.ArgumentFor(verb) + " \"%1\"";

        /// <summary>Every value to write for the right-click verbs.</summary>
        public static List<RegistryEntry> Entries(string exePath)
        {
            var list = new List<RegistryEntry>();
            foreach (var v in Verbs)
            {
                string key = ShellKey + @"\" + v.Key;
                list.Add(new RegistryEntry(key, "", v.Label));
                list.Add(new RegistryEntry(key, "Icon", exePath + ",0"));
                list.Add(new RegistryEntry(key + @"\command", "", CommandFor(exePath, v.Verb)));
            }
            return list;
        }

        /// <summary>The value that starts the tray icon when the user signs in to Windows.</summary>
        public static RegistryEntry StartupEntry(string exePath) =>
            new RegistryEntry(RunKey, RunValueName, Quote(exePath) + " " + DesktopCommand.ArgumentFor(DesktopVerb.Tray));
    }
}
