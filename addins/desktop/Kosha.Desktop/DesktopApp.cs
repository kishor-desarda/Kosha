// SPDX-License-Identifier: Apache-2.0
using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Windows.Forms;
using Kosha.CadCore;
using Kosha.Client;
using Kosha.Desktop.Core;

namespace Kosha.Desktop
{
    /// <summary>
    /// What every window of one Kosha Desktop process shares: the settings (the same file as the CAD add-ins, so one
    /// sign-in serves all), the API client, the session and the activity log.
    /// </summary>
    sealed class DesktopApp : IDisposable
    {
        public static readonly Color Accent = Color.FromArgb(11, 92, 173);     // Kosha Blue
        public static readonly Color ErrorInk = Color.FromArgb(160, 30, 30);

        public DesktopApp()
        {
            Protector = TokenProtectors.ForThisMachine();
            Settings = KoshaSettings.Load(SettingsFile, Protector);
            Api = new KoshaApiClient(Settings);
            Session = new DesktopSession(Api, Settings, Prompts);
            Runner = new DesktopRunner(Session, Log);
        }

        public static string SettingsFile => Path.Combine(KoshaSettings.DefaultFolder, "settings.json");

        public ITokenProtector Protector { get; }
        public KoshaSettings Settings { get; }
        public KoshaApiClient Api { get; }
        public UiPrompts Prompts { get; } = new UiPrompts();
        public DesktopSession Session { get; }
        public ActivityLog Log { get; } = new ActivityLog(ActivityLog.DefaultPath);
        public DesktopRunner Runner { get; }

        /// <summary>Settings are a convenience: a failed save is not an error worth stopping for.</summary>
        public void SaveSettings()
        {
            try { Settings.Save(Protector); }
            catch (Exception ex) when (ex is IOException || ex is UnauthorizedAccessException) { }
        }

        /// <summary>Takes over a sign-in or folder change made by another Kosha program (a CAD add-in, an Explorer verb).</summary>
        public void ReloadSettings()
        {
            var fresh = KoshaSettings.Load(SettingsFile, Protector);
            if (fresh.Token != Settings.Token || fresh.ServerUrl != Settings.ServerUrl) Session.SignOut();
            Settings.ServerUrl = fresh.ServerUrl;
            Settings.WorkspaceRoot = fresh.WorkspaceRoot;
            Settings.Username = fresh.Username;
            Settings.Token = fresh.Token;
        }

        public static void OpenInBrowser(string url)
        {
            if (string.IsNullOrEmpty(url)) return;
            try { Process.Start(new ProcessStartInfo(url) { UseShellExecute = true }); }
            catch (Exception) { /* no browser: nothing more to do */ }
        }

        /// <summary>Opens Explorer at a file (selected) or a folder.</summary>
        public static void ShowInExplorer(string path)
        {
            try
            {
                if (File.Exists(path)) Process.Start("explorer.exe", "/select,\"" + path + "\"");
                else if (Directory.Exists(path)) Process.Start(new ProcessStartInfo(path) { UseShellExecute = true });
                else
                {
                    string parent = Path.GetDirectoryName(path ?? "");
                    if (!string.IsNullOrEmpty(parent) && Directory.Exists(parent)) Process.Start(new ProcessStartInfo(parent) { UseShellExecute = true });
                }
            }
            catch (Exception) { /* Explorer could not start: nothing more to do */ }
        }

        public void Dispose() => Api.Dispose();
    }

    /// <summary>Questions from the session, as message boxes in front of the window that asked.</summary>
    sealed class UiPrompts : IUserPrompts
    {
        public IWin32Window Owner { get; set; }

        public bool Confirm(string title, string message) =>
            MessageBox.Show(Owner, message, title, MessageBoxButtons.OKCancel, MessageBoxIcon.Question) == DialogResult.OK;
    }
}
