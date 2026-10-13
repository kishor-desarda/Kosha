// SPDX-License-Identifier: Apache-2.0
using System;
using System.IO;
using System.Security;
using System.Windows.Forms;
using Kosha.Desktop.Core;
using Microsoft.Win32;

namespace Kosha.Desktop
{
    /// <summary>
    /// Writes and removes the Explorer verbs and the start-up entry described by <see cref="ExplorerVerbs"/>, under
    /// HKEY_CURRENT_USER only, so no administrator rights are needed and other users of the PC are not affected.
    /// The registry API treats the "*" key name literally (it is not a wildcard there).
    /// </summary>
    static class Registration
    {
        public static bool Register(string exePath)
        {
            try
            {
                foreach (var entry in ExplorerVerbs.Entries(exePath)) Write(entry);
                Write(ExplorerVerbs.StartupEntry(exePath));
                return true;
            }
            catch (Exception ex) when (ex is UnauthorizedAccessException || ex is SecurityException || ex is IOException)
            {
                MessageBox.Show("Kosha Desktop could not add its commands to the Explorer menu: " + ex.Message, "Kosha Desktop",
                                MessageBoxButtons.OK, MessageBoxIcon.Warning);
                return false;
            }
        }

        static void Write(RegistryEntry entry)
        {
            using (var key = Registry.CurrentUser.CreateSubKey(entry.Key))
                key.SetValue(entry.Name, entry.Value, RegistryValueKind.String);
        }

        public static bool Unregister()
        {
            try
            {
                foreach (var key in ExplorerVerbs.VerbKeys) Registry.CurrentUser.DeleteSubKeyTree(key, false);
                using (var run = Registry.CurrentUser.OpenSubKey(ExplorerVerbs.RunKey, true))
                    run?.DeleteValue(ExplorerVerbs.RunValueName, false);
                return true;
            }
            catch (Exception ex) when (ex is UnauthorizedAccessException || ex is SecurityException || ex is IOException)
            {
                MessageBox.Show("Kosha Desktop could not remove its commands from the Explorer menu: " + ex.Message, "Kosha Desktop",
                                MessageBoxButtons.OK, MessageBoxIcon.Warning);
                return false;
            }
        }
    }
}
