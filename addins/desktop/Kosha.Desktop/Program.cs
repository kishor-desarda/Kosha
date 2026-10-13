// SPDX-License-Identifier: Apache-2.0
using System;
using System.Windows.Forms;
using Kosha.Desktop.Core;

namespace Kosha.Desktop
{
    /// <summary>
    /// Kosha.Desktop.exe                      the tray icon (also started at sign-in to Windows)
    /// Kosha.Desktop.exe checkin "file"       an Explorer verb: checkin, checkout, show, latest, undo
    /// Kosha.Desktop.exe register | unregister   add or remove the Explorer verbs for this Windows user (install.ps1 does it)
    /// Every error is caught and shown in plain words; nothing reaches Explorer.
    /// </summary>
    static class Program
    {
        [STAThread]
        static int Main(string[] args)
        {
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            Application.SetHighDpiMode(HighDpiMode.PerMonitorV2);
            Application.SetUnhandledExceptionMode(UnhandledExceptionMode.CatchException);
            Application.ThreadException += (s, e) => ShowError(e.Exception);
            try
            {
                var cmd = DesktopCommand.Parse(args);
                switch (cmd.Verb)
                {
                    case DesktopVerb.Invalid:
                        MessageBox.Show(cmd.Error, "Kosha Desktop", MessageBoxButtons.OK, MessageBoxIcon.Warning);
                        return 2;
                    case DesktopVerb.Register:
                        return Registration.Register(Environment.ProcessPath ?? Application.ExecutablePath) ? 0 : 1;
                    case DesktopVerb.Unregister:
                        return Registration.Unregister() ? 0 : 1;
                    case DesktopVerb.Tray:
                        return TrayContext.Run();
                    default:
                        using (var app = new DesktopApp())
                        using (var form = new ActionForm(app, cmd.Verb, cmd.Path))
                            Application.Run(form);
                        return 0;
                }
            }
            catch (Exception ex)
            {
                ShowError(ex);
                return 1;
            }
        }

        static void ShowError(Exception ex)
        {
            try
            {
                MessageBox.Show("Something went wrong in Kosha Desktop: " + ex.Message + Environment.NewLine +
                                "Your files on this PC were not changed.", "Kosha Desktop", MessageBoxButtons.OK, MessageBoxIcon.Error);
            }
            catch (Exception)
            {
                // no way left to tell the user
            }
        }
    }
}
