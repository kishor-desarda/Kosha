// SPDX-License-Identifier: Apache-2.0
using System;
using System.Collections.Generic;
using System.Linq;

namespace Kosha.Desktop.Core
{
    public enum DesktopVerb
    {
        /// <summary>No verb: run the tray icon.</summary>
        Tray,
        CheckIn,
        CheckOut,
        Show,
        GetLatest,
        UndoCheckOut,
        /// <summary>Add the Explorer right-click verbs and the start-up entry for this Windows user.</summary>
        Register,
        /// <summary>Remove them again.</summary>
        Unregister,
        /// <summary>The command line could not be understood; see <see cref="DesktopCommand.Error"/>.</summary>
        Invalid,
    }

    /// <summary>
    /// What Kosha.Desktop.exe was asked to do. Explorer starts it as  Kosha.Desktop.exe checkin "C:\Kosha\Products\Spec.docx".
    /// </summary>
    public sealed class DesktopCommand
    {
        static readonly Dictionary<string, DesktopVerb> Verbs = new Dictionary<string, DesktopVerb>(StringComparer.OrdinalIgnoreCase)
        {
            ["tray"] = DesktopVerb.Tray,
            ["checkin"] = DesktopVerb.CheckIn,
            ["checkout"] = DesktopVerb.CheckOut,
            ["show"] = DesktopVerb.Show,
            ["latest"] = DesktopVerb.GetLatest,
            ["undo"] = DesktopVerb.UndoCheckOut,
            ["register"] = DesktopVerb.Register,
            ["unregister"] = DesktopVerb.Unregister,
        };

        public DesktopVerb Verb { get; private set; }
        public string Path { get; private set; } = "";
        public string Error { get; private set; } = "";

        public bool NeedsFile => Verb == DesktopVerb.CheckIn || Verb == DesktopVerb.CheckOut || Verb == DesktopVerb.Show ||
                                 Verb == DesktopVerb.GetLatest || Verb == DesktopVerb.UndoCheckOut;

        /// <summary>The word to put on the command line for a verb ("checkin"), as Explorer uses it.</summary>
        public static string ArgumentFor(DesktopVerb verb) => Verbs.First(kv => kv.Value == verb).Key;

        /// <summary>Plain words for a verb, for windows and the activity list.</summary>
        public static string Describe(DesktopVerb verb)
        {
            switch (verb)
            {
                case DesktopVerb.CheckIn: return "Check in";
                case DesktopVerb.CheckOut: return "Check out";
                case DesktopVerb.Show: return "Show in Kosha";
                case DesktopVerb.GetLatest: return "Get latest version";
                case DesktopVerb.UndoCheckOut: return "Undo check-out";
                case DesktopVerb.Register: return "Add to Explorer";
                case DesktopVerb.Unregister: return "Remove from Explorer";
                default: return "Kosha Desktop";
            }
        }

        public static DesktopCommand Parse(IList<string> args)
        {
            var list = (args ?? new string[0]).Where(a => !string.IsNullOrWhiteSpace(a)).Select(a => a.Trim()).ToList();
            if (list.Count == 0) return new DesktopCommand { Verb = DesktopVerb.Tray };
            string word = list[0].TrimStart('-', '/');
            if (!Verbs.TryGetValue(word, out var verb))
                return Invalid("Kosha Desktop does not know the command \"" + list[0] + "\". Use one of: " + string.Join(", ", Verbs.Keys) + ".");
            var cmd = new DesktopCommand { Verb = verb };
            if (!cmd.NeedsFile)
                return list.Count == 1 ? cmd : Invalid("\"" + word + "\" does not take a file.");
            if (list.Count < 2)
                return Invalid("Say which file to " + Describe(verb).ToLowerInvariant() + ", for example: Kosha.Desktop.exe " + word + " \"C:\\Kosha\\Products\\Spec.docx\"");
            if (list.Count > 2)
                return Invalid("Give one file at a time. Put the path in quotes when it has spaces.");
            cmd.Path = list[1].Trim('"');
            return cmd;
        }

        static DesktopCommand Invalid(string error) => new DesktopCommand { Verb = DesktopVerb.Invalid, Error = error };
    }
}
