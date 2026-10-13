// SPDX-License-Identifier: Apache-2.0
using System;
using System.Collections.Generic;
using System.Drawing;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Threading.Tasks;
using System.Windows.Forms;
using Kosha.Client;
using Kosha.Desktop.Core;

namespace Kosha.Desktop
{
    /// <summary>
    /// The sync status window: who is signed in, the workspace folder, the files checked out to you with the state of
    /// the copy on this PC, and what Kosha Desktop did recently. Disabled buttons are explained in the line below them.
    /// </summary>
    sealed class StatusForm : Form
    {
        readonly DesktopApp app;
        readonly Label userLabel;
        readonly Label workspaceLabel;
        readonly ListView filesList;
        readonly Label hintLabel;
        readonly ListView activityList;
        readonly Button checkInButton;
        readonly Button undoButton;
        readonly Button showButton;
        readonly Button locateButton;
        readonly Button refreshButton;
        readonly Button signInButton;
        readonly Timer activityTimer = new Timer { Interval = 3000 };
        DateTime activityStamp = DateTime.MinValue;
        bool busy;

        public StatusForm(DesktopApp app)
        {
            this.app = app;
            Text = "Kosha Desktop";
            Icon = KoshaIcon.Get();
            Font = new Font("Segoe UI", 9f);
            AutoScaleDimensions = new SizeF(96f, 96f);
            AutoScaleMode = AutoScaleMode.Dpi;
            StartPosition = FormStartPosition.CenterScreen;
            ClientSize = new Size(760, 560);
            MinimumSize = new Size(560, 420);

            userLabel = new Label { AutoSize = true, Font = new Font("Segoe UI Semibold", 10f), Margin = new Padding(0, 0, 0, 2) };
            workspaceLabel = new Label { AutoSize = true, Margin = new Padding(0, 6, 8, 0) };
            signInButton = Ui.Button("Sign in…");
            signInButton.Click += (s, e) => SignInOrOut();
            var changeFolder = Ui.Button("Change folder…");
            changeFolder.Click += (s, e) => ChangeWorkspace();
            var openFolder = Ui.Button("Open folder");
            openFolder.Click += (s, e) => DesktopApp.ShowInExplorer(app.Settings.WorkspaceRoot);
            var openWeb = Ui.Button("Open Kosha in the browser");
            openWeb.Click += (s, e) => DesktopApp.OpenInBrowser(app.Session.HomeUrl);

            filesList = MakeList("Files checked out to you", ("File", 220), ("Kosha folder", 200), ("On this PC", 300));
            filesList.ShowItemToolTips = true;
            filesList.SelectedIndexChanged += (s, e) => UpdateButtons();
            filesList.DoubleClick += (s, e) => { if (checkInButton.Enabled) checkInButton.PerformClick(); };
            checkInButton = Ui.Button("Check in…", primary: true);
            checkInButton.Click += async (s, e) => await Guard(CheckInSelectedAsync);
            undoButton = Ui.Button("Undo check-out");
            undoButton.Click += async (s, e) => await Guard(UndoSelectedAsync);
            showButton = Ui.Button("Show in Kosha");
            showButton.Click += (s, e) => { var r = Selected(); if (r != null) DesktopApp.OpenInBrowser(app.Session.WebUrl(r.Status)); };
            locateButton = Ui.Button("Show on this PC");
            locateButton.Click += (s, e) => { var r = Selected(); if (r != null) DesktopApp.ShowInExplorer(r.LocalPath); };
            refreshButton = Ui.Button("Refresh");
            refreshButton.Click += async (s, e) => await Guard(RefreshFilesAsync);
            hintLabel = new Label { AutoSize = true, ForeColor = SystemColors.GrayText, Margin = new Padding(0, 4, 0, 8) };

            activityList = MakeList("Recent activity on this PC", ("Time", 130), ("Action", 130), ("File", 200), ("Result", 300));
            activityList.ShowItemToolTips = true;

            var layout = new TableLayoutPanel { Dock = DockStyle.Fill, ColumnCount = 1, RowCount = 8, Padding = new Padding(14) };
            layout.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100));
            layout.RowStyles.Add(new RowStyle(SizeType.AutoSize));
            layout.RowStyles.Add(new RowStyle(SizeType.AutoSize));
            layout.RowStyles.Add(new RowStyle(SizeType.AutoSize));
            layout.RowStyles.Add(new RowStyle(SizeType.Percent, 55));
            layout.RowStyles.Add(new RowStyle(SizeType.AutoSize));
            layout.RowStyles.Add(new RowStyle(SizeType.AutoSize));
            layout.RowStyles.Add(new RowStyle(SizeType.AutoSize));
            layout.RowStyles.Add(new RowStyle(SizeType.Percent, 45));

            var accountRow = Row(userLabel, signInButton, openWeb);
            var folderRow = Row(workspaceLabel, changeFolder, openFolder);
            var buttonRow = Row(checkInButton, undoButton, showButton, locateButton, refreshButton);
            layout.Controls.Add(accountRow, 0, 0);
            layout.Controls.Add(folderRow, 0, 1);
            layout.Controls.Add(Ui.Caption("Files checked out to you"), 0, 2);
            layout.Controls.Add(filesList, 0, 3);
            layout.Controls.Add(buttonRow, 0, 4);
            layout.Controls.Add(hintLabel, 0, 5);
            layout.Controls.Add(Ui.Caption("Recent activity on this PC"), 0, 6);
            layout.Controls.Add(activityList, 0, 7);
            Controls.Add(layout);

            activityTimer.Tick += (s, e) => RefreshActivity(force: false);
            Shown += async (s, e) => { activityTimer.Start(); await RefreshAllAsync(); };
            Activated += (s, e) => app.Prompts.Owner = this;
            FormClosed += (s, e) => { activityTimer.Stop(); activityTimer.Dispose(); };
        }

        static ListView MakeList(string name, params (string Title, int Width)[] columns)
        {
            var list = new ListView
            {
                View = View.Details, FullRowSelect = true, MultiSelect = false, HideSelection = false, Dock = DockStyle.Fill,
                AccessibleName = name, Margin = new Padding(0, 2, 0, 4),
            };
            foreach (var c in columns) list.Columns.Add(c.Title, c.Width);
            return list;
        }

        static FlowLayoutPanel Row(params Control[] controls)
        {
            var row = new FlowLayoutPanel { AutoSize = true, WrapContents = true, Dock = DockStyle.Fill, Margin = new Padding(0, 0, 0, 4) };
            foreach (var c in controls)
            {
                if (c is Button) c.Margin = new Padding(0, 0, 8, 0);
                row.Controls.Add(c);
            }
            return row;
        }

        /// <summary>Call when the window is shown again from the tray.</summary>
        public async Task RefreshAllAsync()
        {
            await Guard(async () =>
            {
                app.ReloadSettings();
                ShowAccount();
                RefreshActivity(force: true);
                await RefreshFilesAsync();
            });
        }

        async Task Guard(Func<Task> action)
        {
            if (busy) return;
            busy = true;
            UseWaitCursor = true;
            app.Prompts.Owner = this;
            try
            {
                await action();
            }
            catch (KoshaException ex)
            {
                if (ex.IsSignInNeeded)
                {
                    app.Session.SignOut();
                    app.SaveSettings();
                    ShowAccount();
                    hintLabel.Text = "Your sign-in has expired. Press Sign in… to see your files.";
                }
                else hintLabel.Text = ex.Message;
            }
            catch (Exception ex)
            {
                hintLabel.Text = "Something went wrong in Kosha Desktop: " + ex.Message + " Your files on this PC were not changed.";
            }
            finally
            {
                UseWaitCursor = false;
                busy = false;
            }
        }

        void ShowAccount()
        {
            bool signedIn = app.Session.IsSignedIn;
            string who = app.Session.User?.FullName;
            if (string.IsNullOrEmpty(who)) who = app.Settings.Username;
            userLabel.Text = signedIn ? "Signed in as " + who + " at " + app.Settings.ServerUrl : "Not signed in to Kosha";
            signInButton.Text = signedIn ? "Sign out" : "Sign in…";
            workspaceLabel.Text = "Workspace folder: " + app.Settings.WorkspaceRoot;
        }

        void SignInOrOut()
        {
            if (app.Session.IsSignedIn)
            {
                app.Session.SignOut();
                app.SaveSettings();
                filesList.Items.Clear();
                ShowAccount();
                UpdateButtons();
                return;
            }
            using (var f = new SignInForm(app))
            {
                if (f.ShowDialog(this) == DialogResult.OK) _ = RefreshAllAsync();
            }
        }

        void ChangeWorkspace()
        {
            using (var dlg = new FolderBrowserDialog { Description = "Choose your local Kosha workspace folder", UseDescriptionForTitle = true, ShowNewFolderButton = true })
            {
                if (Directory.Exists(app.Settings.WorkspaceRoot)) dlg.SelectedPath = app.Settings.WorkspaceRoot;
                if (dlg.ShowDialog(this) != DialogResult.OK) return;
                app.Settings.WorkspaceRoot = dlg.SelectedPath;
                app.SaveSettings();
            }
            ShowAccount();
            _ = Guard(RefreshFilesAsync);
        }

        async Task RefreshFilesAsync()
        {
            filesList.Items.Clear();
            if (!app.Session.IsSignedIn)
            {
                hintLabel.Text = "Sign in to see the files checked out to you.";
                UpdateButtons();
                return;
            }
            hintLabel.Text = "Asking Kosha…";
            List<SyncRow> rows = await app.Session.CheckedOutToMeAsync();
            ShowAccount();
            foreach (var r in rows)
            {
                var item = new ListViewItem(new[] { r.FileName, r.KoshaFolder, r.Describe() }) { Tag = r, ToolTipText = r.LocalPath ?? "" };
                if (r.Local == LocalCopy.Changed) item.Font = new Font(filesList.Font, FontStyle.Bold);
                filesList.Items.Add(item);
            }
            UpdateButtons();
        }

        /// <summary>Reloads the activity list when another Kosha Desktop process (an Explorer verb) has added to it.</summary>
        void RefreshActivity(bool force)
        {
            List<ActivityEntry> entries;
            try
            {
                var stamp = File.Exists(app.Log.FilePath) ? File.GetLastWriteTimeUtc(app.Log.FilePath) : DateTime.MinValue;
                if (!force && stamp == activityStamp) return;
                activityStamp = stamp;
                entries = app.Log.Read().Take(100).ToList();
            }
            catch (Exception)
            {
                return;   // the activity list is a convenience
            }
            activityList.BeginUpdate();
            try
            {
                activityList.Items.Clear();
                foreach (var a in entries)
                {
                    var item = new ListViewItem(new[]
                    {
                        a.AtUtc.ToLocalTime().ToString("g", CultureInfo.CurrentCulture), a.Action, a.FileName, (a.Ok ? "Done: " : "Not done: ") + a.Message,
                    }) { ToolTipText = a.Message };
                    if (!a.Ok) item.ForeColor = DesktopApp.ErrorInk;
                    activityList.Items.Add(item);
                }
            }
            finally
            {
                activityList.EndUpdate();
            }
        }

        SyncRow Selected() => filesList.SelectedItems.Count == 1 ? filesList.SelectedItems[0].Tag as SyncRow : null;

        /// <summary>Enables what makes sense for the selected file and says why the rest is not available.</summary>
        void UpdateButtons()
        {
            var r = Selected();
            bool local = r != null && r.LocalPath != null && File.Exists(r.LocalPath);
            checkInButton.Enabled = local;
            undoButton.Enabled = r != null;
            showButton.Enabled = r != null;
            locateButton.Enabled = r != null && r.LocalPath != null;
            refreshButton.Enabled = app.Session.IsSignedIn;
            if (!app.Session.IsSignedIn) hintLabel.Text = "Sign in to see the files checked out to you.";
            else if (filesList.Items.Count == 0) hintLabel.Text = "No files are checked out to you. Right-click a file in your workspace and choose Check out from Kosha.";
            else if (r == null) hintLabel.Text = "Select a file to check it in, undo its check-out or open it in Kosha.";
            else if (r.Local == LocalCopy.NoPlace)
                hintLabel.Text = r.FileName + " is in the top Kosha folder, which has no place in your workspace, so it cannot be checked in from here. Open it in Kosha.";
            else if (!local) hintLabel.Text = r.FileName + " is not on this PC, so it cannot be checked in from here. Undo the check-out, or open it in Kosha.";
            else hintLabel.Text = r.Describe() + ".";
        }

        async Task CheckInSelectedAsync()
        {
            var r = Selected();
            if (r?.LocalPath == null) return;
            using (var f = new ActionForm(app, DesktopVerb.CheckIn, r.LocalPath))
                f.ShowDialog(this);
            app.Prompts.Owner = this;
            RefreshActivity(force: true);
            await RefreshFilesAsync();
        }

        async Task UndoSelectedAsync()
        {
            var r = Selected();
            if (r == null) return;
            if (!await app.Session.UndoCheckOutAsync(r)) return;
            app.Log.Add(new ActivityEntry
            {
                Action = DesktopCommand.Describe(DesktopVerb.UndoCheckOut), Path = r.LocalPath ?? r.FileName, Ok = true,
                Message = r.FileName + " is no longer checked out.",
            });
            RefreshActivity(force: true);
            await RefreshFilesAsync();
        }

        /// <summary>Closing the window only hides it; the tray icon stays.</summary>
        protected override void OnFormClosing(FormClosingEventArgs e)
        {
            if (e.CloseReason == CloseReason.UserClosing)
            {
                e.Cancel = true;
                Hide();
                return;
            }
            base.OnFormClosing(e);
        }
    }
}
