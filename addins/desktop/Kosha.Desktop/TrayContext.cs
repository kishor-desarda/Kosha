// SPDX-License-Identifier: Apache-2.0
using System;
using System.Drawing;
using System.Threading;
using System.Windows.Forms;

namespace Kosha.Desktop
{
    /// <summary>
    /// The Kosha icon in the notification area: sign in, workspace folder, Kosha in the browser and the sync status
    /// window. One per Windows user session; starting it again shows the status window of the running one.
    /// </summary>
    sealed class TrayContext : ApplicationContext
    {
        const string MutexName = @"Local\Kosha.Desktop.Tray";
        const string ShowEventName = @"Local\Kosha.Desktop.ShowStatus";

        readonly DesktopApp app;
        readonly NotifyIcon icon;
        readonly ContextMenuStrip menu;
        readonly ToolStripMenuItem accountItem;
        readonly ToolStripMenuItem signInItem;
        readonly Control invoker = new Control();
        readonly EventWaitHandle showEvent;
        readonly RegisteredWaitHandle showWait;
        StatusForm status;

        public static int Run()
        {
            using (var mutex = new Mutex(true, MutexName, out bool first))
            {
                if (!first)
                {
                    try
                    {
                        using (var show = EventWaitHandle.OpenExisting(ShowEventName)) show.Set();
                    }
                    catch (Exception)
                    {
                        // the running copy is just starting or closing: nothing to do
                    }
                    return 0;
                }
                using (var app = new DesktopApp())
                using (var context = new TrayContext(app))
                    Application.Run(context);
                GC.KeepAlive(mutex);
            }
            return 0;
        }

        TrayContext(DesktopApp app)
        {
            this.app = app;
            _ = invoker.Handle;    // a window handle on this thread, to come back to it from the show signal

            accountItem = new ToolStripMenuItem("Kosha Desktop") { Enabled = false };
            signInItem = new ToolStripMenuItem("Sign in…", null, (s, e) => SignInOrOut());
            menu = new ContextMenuStrip();
            menu.Items.Add(accountItem);
            menu.Items.Add(new ToolStripSeparator());
            menu.Items.Add(new ToolStripMenuItem("Show sync status", null, (s, e) => ShowStatus()) { Font = new Font(menu.Font, FontStyle.Bold) });
            menu.Items.Add(new ToolStripMenuItem("Open Kosha in the browser", null, (s, e) => DesktopApp.OpenInBrowser(app.Session.HomeUrl)));
            menu.Items.Add(new ToolStripMenuItem("Open workspace folder", null, (s, e) => DesktopApp.ShowInExplorer(app.Settings.WorkspaceRoot)));
            menu.Items.Add(new ToolStripMenuItem("Change workspace folder…", null, (s, e) => ChangeWorkspace()));
            menu.Items.Add(new ToolStripSeparator());
            menu.Items.Add(signInItem);
            menu.Items.Add(new ToolStripSeparator());
            menu.Items.Add(new ToolStripMenuItem("Exit Kosha Desktop", null, (s, e) => ExitThread()));
            menu.Opening += (s, e) => UpdateMenu();

            icon = new NotifyIcon { Icon = KoshaIcon.Tray(), Text = "Kosha Desktop", ContextMenuStrip = menu, Visible = true };
            icon.DoubleClick += (s, e) => ShowStatus();
            icon.BalloonTipClicked += (s, e) => { if (!app.Session.IsSignedIn) SignInOrOut(); };

            showEvent = new EventWaitHandle(false, EventResetMode.AutoReset, ShowEventName);
            showWait = ThreadPool.RegisterWaitForSingleObject(showEvent, (state, timedOut) => OnUiThread(ShowStatus), null, -1, false);

            UpdateMenu();
            if (!app.Session.IsSignedIn)
                icon.ShowBalloonTip(8000, "Kosha Desktop", "Sign in to Kosha to check files in and out from Explorer. Click here, or right-click the Kosha icon.", ToolTipIcon.Info);
        }

        void OnUiThread(Action action)
        {
            try
            {
                if (!invoker.IsDisposed) invoker.BeginInvoke(action);
            }
            catch (Exception)
            {
                // closing down
            }
        }

        void UpdateMenu()
        {
            try { app.ReloadSettings(); }
            catch (Exception) { /* keep what we have */ }
            bool signedIn = app.Session.IsSignedIn;
            accountItem.Text = signedIn ? "Signed in as " + app.Settings.Username : "Not signed in to Kosha";
            signInItem.Text = signedIn ? "Sign out" : "Sign in…";
            icon.Text = signedIn ? "Kosha Desktop: signed in" : "Kosha Desktop: not signed in";
        }

        void SignInOrOut()
        {
            try
            {
                if (app.Session.IsSignedIn)
                {
                    app.Session.SignOut();
                    app.SaveSettings();
                }
                else
                {
                    using (var f = new SignInForm(app)) f.ShowDialog();
                }
                UpdateMenu();
                if (status != null && status.Visible) _ = status.RefreshAllAsync();
            }
            catch (Exception ex)
            {
                MessageBox.Show("Kosha Desktop: " + ex.Message, "Kosha Desktop", MessageBoxButtons.OK, MessageBoxIcon.Warning);
            }
        }

        void ChangeWorkspace()
        {
            try
            {
                using (var dlg = new FolderBrowserDialog { Description = "Choose your local Kosha workspace folder", UseDescriptionForTitle = true, ShowNewFolderButton = true })
                {
                    if (System.IO.Directory.Exists(app.Settings.WorkspaceRoot)) dlg.SelectedPath = app.Settings.WorkspaceRoot;
                    if (dlg.ShowDialog() != DialogResult.OK) return;
                    app.Settings.WorkspaceRoot = dlg.SelectedPath;
                    app.SaveSettings();
                }
                if (status != null && status.Visible) _ = status.RefreshAllAsync();
            }
            catch (Exception ex)
            {
                MessageBox.Show("Kosha Desktop: " + ex.Message, "Kosha Desktop", MessageBoxButtons.OK, MessageBoxIcon.Warning);
            }
        }

        void ShowStatus()
        {
            try
            {
                if (status == null || status.IsDisposed)
                {
                    status = new StatusForm(app);
                    status.Show();            // its Shown event loads the lists
                }
                else
                {
                    if (!status.Visible) status.Show();
                    if (status.WindowState == FormWindowState.Minimized) status.WindowState = FormWindowState.Normal;
                    _ = status.RefreshAllAsync();
                }
                status.Activate();
            }
            catch (Exception ex)
            {
                MessageBox.Show("The Kosha status window could not open: " + ex.Message, "Kosha Desktop", MessageBoxButtons.OK, MessageBoxIcon.Warning);
            }
        }

        protected override void ExitThreadCore()
        {
            try
            {
                showWait.Unregister(null);
                showEvent.Dispose();
                icon.Visible = false;
                icon.Dispose();
                status?.Dispose();
                invoker.Dispose();
            }
            catch (Exception)
            {
                // closing down anyway
            }
            base.ExitThreadCore();
        }
    }
}
