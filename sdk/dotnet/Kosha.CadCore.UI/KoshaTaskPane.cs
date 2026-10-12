// SPDX-License-Identifier: Apache-2.0
using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Threading.Tasks;
using System.Windows.Forms;
using Kosha.Client;

namespace Kosha.CadCore.UI
{
    /// <summary>
    /// The Kosha pane shown inside a CAD program. It knows nothing about the CAD program itself:
    /// everything goes through <see cref="CadSession"/> and the host's <see cref="ICadHost"/>.
    /// Every handler catches its own errors, so an exception never reaches the CAD program.
    /// </summary>
    public sealed class KoshaTaskPane : UserControl, IUserPrompts
    {
        static readonly Color Accent = Color.FromArgb(11, 92, 173);      // Kosha Blue
        static readonly Color WarnInk = Color.FromArgb(138, 58, 8);

        readonly ITokenProtector protector;
        readonly KoshaSettings settings;
        readonly KoshaApiClient api;
        readonly CadSession session;
        DocumentState state;
        bool busy;

        readonly Panel loginPanel = new Panel { Dock = DockStyle.Fill, AutoScroll = true };
        readonly TextBox serverBox = new TextBox();
        readonly TextBox workspaceBox = new TextBox();
        readonly TextBox userBox = new TextBox();
        readonly TextBox passBox = new TextBox { UseSystemPasswordChar = true };

        readonly Panel docPanel = new Panel { Dock = DockStyle.Fill, AutoScroll = true };
        readonly Label userLabel = new Label { AutoSize = false, Height = 20, ForeColor = Color.DimGray };
        readonly Label fileLabel = new Label { AutoSize = false, Height = 40 };
        readonly Label stateLabel = new Label { AutoSize = false, Height = 64 };
        readonly Label folderLabel = new Label { AutoSize = false, Height = 34, ForeColor = Color.DimGray };
        readonly TextBox pnBox = new TextBox();
        readonly TextBox descBox = new TextBox();
        readonly Label materialLabel = new Label { AutoSize = false, Height = 20 };
        readonly Label warnLabel = new Label { AutoSize = false, Height = 70, ForeColor = WarnInk };
        readonly TextBox commentBox = new TextBox { Multiline = true, Height = 54 };
        readonly CheckBox keepBox = new CheckBox { Text = "Keep checked out", AutoSize = true };
        readonly CheckBox withComponentsBox = new CheckBox { Text = "Also check in new and changed components", AutoSize = true, Checked = true };
        readonly Button checkoutBtn = new Button { Text = "Check out", Height = 34 };
        readonly Button checkinBtn = new Button { Text = "Check in", Height = 34 };
        readonly Button undoBtn = new Button { Text = "Undo check-out", Height = 30 };
        readonly Button latestBtn = new Button { Text = "Get latest version", Height = 30 };
        readonly Button nextPnBtn = new Button { Text = "Next number", Height = 26 };
        readonly Button openWebBtn = new Button { Text = "Open in Kosha", Height = 30 };

        public KoshaTaskPane(ICadHost host, string settingsFile = null, ITokenProtector protector = null)
        {
            this.protector = protector ?? TokenProtectors.ForThisMachine();
            settings = KoshaSettings.Load(settingsFile ?? Path.Combine(KoshaSettings.DefaultFolder, "settings.json"), this.protector);
            api = new KoshaApiClient(settings);
            session = new CadSession(host, api, settings, this);
            Font = new Font("Segoe UI", 9f);
            ApplyTheme(host.Theme);
            BuildLogin();
            BuildDoc();
            Controls.Add(docPanel);
            Controls.Add(loginPanel);
            ShowLogin(string.IsNullOrEmpty(settings.Token));
        }

        public CadSession Session => session;

        void ApplyTheme(CadTheme theme)
        {
            bool dark = theme == CadTheme.Dark;
            BackColor = dark ? Color.FromArgb(37, 41, 46) : Color.White;
            ForeColor = dark ? Color.FromArgb(232, 234, 237) : Color.FromArgb(22, 25, 29);
        }

        // --------------------------------------------------------------- layout
        static int Stack(Control parent, int y, params Control[] items)
        {
            foreach (var c in items)
            {
                c.Left = 10;
                c.Top = y;
                c.Width = Math.Max(160, parent.ClientSize.Width - 20);
                c.Anchor = AnchorStyles.Left | AnchorStyles.Right | AnchorStyles.Top;
                parent.Controls.Add(c);
                y += c.Height + 6;
            }
            return y;
        }

        static Label Caption(string text) =>
            new Label { Text = text, AutoSize = false, Height = 18, Font = new Font("Segoe UI Semibold", 8.5f) };

        static void Primary(Button b)
        {
            b.BackColor = Accent;
            b.ForeColor = Color.White;
            b.FlatStyle = FlatStyle.Flat;
        }

        void BuildLogin()
        {
            loginPanel.Width = 300;
            var title = new Label { Text = "Kosha", Height = 30, Font = new Font("Segoe UI Semibold", 13f) };
            var sub = new Label { Text = "Sign in with your Kosha account.", Height = 22, ForeColor = Color.DimGray };
            serverBox.Text = settings.ServerUrl;
            workspaceBox.Text = settings.WorkspaceRoot;
            userBox.Text = settings.Username;
            var signIn = new Button { Text = "Sign in", Height = 34 };
            Primary(signIn);
            signIn.Click += async (s, e) => await Run(SignInAsync);
            passBox.KeyDown += async (s, e) => { if (e.KeyCode == Keys.Enter) await Run(SignInAsync); };
            Stack(loginPanel, 12, title, sub, Caption("Kosha server address"), serverBox, Caption("Local workspace folder"), workspaceBox,
                Caption("User name"), userBox, Caption("Password"), passBox, signIn);
        }

        void BuildDoc()
        {
            docPanel.Width = 300;
            fileLabel.Font = new Font("Segoe UI Semibold", 10f);
            Primary(checkinBtn);
            checkoutBtn.Click += async (s, e) => await Run(async () => { await session.CheckOutAsync(state); await RefreshAsync(); });
            checkinBtn.Click += async (s, e) => await Run(CheckInAsync);
            undoBtn.Click += async (s, e) => await Run(async () => { await session.UndoCheckOutAsync(state); await RefreshAsync(); });
            latestBtn.Click += async (s, e) => await Run(async () => { await session.GetLatestAsync(state); await RefreshAsync(); });
            nextPnBtn.Click += async (s, e) => await Run(NextNumberAsync);
            openWebBtn.Click += (s, e) => SafeOpen(session.WebUrl(state));
            var signOut = new LinkLabel { Text = "Sign out", Height = 20 };
            signOut.LinkClicked += (s, e) => { session.SignOut(); SaveSettings(); ShowLogin(true); };
            Stack(docPanel, 10, userLabel, fileLabel, stateLabel, folderLabel,
                Caption("Part number"), pnBox, nextPnBtn, Caption("Description"), descBox, Caption("Material"), materialLabel,
                warnLabel, checkoutBtn, Caption("Comment for this version"), commentBox, keepBox, withComponentsBox, checkinBtn,
                undoBtn, latestBtn, openWebBtn, signOut);
        }

        void ShowLogin(bool login)
        {
            loginPanel.Visible = login;
            docPanel.Visible = !login;
            if (!login) _ = Run(RefreshAsync);
        }

        void SaveSettings()
        {
            try { settings.Save(protector); }
            catch (Exception ex) when (ex is IOException || ex is UnauthorizedAccessException) { /* settings are a convenience */ }
        }

        static void SafeOpen(string url)
        {
            try { Process.Start(new ProcessStartInfo(url) { UseShellExecute = true }); }
            catch (Exception) { /* no browser: nothing to do */ }
        }

        /// <summary>Runs one action: busy cursor, no double clicks, every error turned into a calm message.</summary>
        async Task Run(Func<Task> action)
        {
            if (busy) return;
            busy = true;
            Cursor = Cursors.WaitCursor;
            try
            {
                await action();
            }
            catch (KoshaException ex)
            {
                if (ex.IsSignInNeeded) { session.SignOut(); SaveSettings(); ShowLogin(true); }
                MessageBox.Show(this, ex.Message, "Kosha", MessageBoxButtons.OK, ex.Status >= 500 ? MessageBoxIcon.Error : MessageBoxIcon.Warning);
            }
            catch (Exception ex)
            {
                MessageBox.Show(this, "Something went wrong in the Kosha add-in: " + ex.Message + Environment.NewLine +
                                      "Your files on this PC were not changed.", "Kosha", MessageBoxButtons.OK, MessageBoxIcon.Error);
            }
            finally
            {
                Cursor = Cursors.Default;
                busy = false;
            }
        }

        public bool Confirm(string title, string message) =>
            MessageBox.Show(this, message, title, MessageBoxButtons.OKCancel, MessageBoxIcon.Question) == DialogResult.OK;

        // --------------------------------------------------------------- actions
        async Task SignInAsync()
        {
            settings.ServerUrl = serverBox.Text.Trim();
            settings.WorkspaceRoot = workspaceBox.Text.Trim();
            var user = await session.SignInAsync(userBox.Text.Trim(), passBox.Text);
            passBox.Text = "";
            SaveSettings();
            userLabel.Text = "Signed in as " + (user?.FullName ?? settings.Username);
            ShowLogin(false);
        }

        /// <summary>Call when the active document changes or is saved.</summary>
        public void RefreshStatus()
        {
            if (docPanel.Visible) _ = Run(RefreshAsync);
        }

        async Task RefreshAsync()
        {
            state = await session.DescribeAsync(session.Host.ActiveDocument);
            fileLabel.Text = state.Title;
            stateLabel.Text = state.Message;
            folderLabel.Text = state.Folder;
            pnBox.Text = state.PartNumber;
            descBox.Text = state.Description;
            materialLabel.Text = state.Material;
            warnLabel.Text = state.Warning;
            withComponentsBox.Visible = state.OffersComponents;
            checkoutBtn.Enabled = state.CanCheckOut;
            checkinBtn.Enabled = state.CanCheckIn;
            undoBtn.Enabled = state.CanUndo;
            latestBtn.Enabled = state.CanGetLatest;
            openWebBtn.Enabled = state.CanOpenInBrowser;
            pnBox.ReadOnly = descBox.ReadOnly = !state.CanCheckIn;
            nextPnBtn.Enabled = state.CanCheckIn;
        }

        async Task CheckInAsync()
        {
            var outcome = await session.CheckInAsync(state, pnBox.Text, descBox.Text, commentBox.Text, keepBox.Checked, withComponentsBox.Checked);
            commentBox.Text = "";
            MessageBox.Show(this, outcome.Summary(), "Kosha", MessageBoxButtons.OK, MessageBoxIcon.Information);
            await RefreshAsync();
        }

        async Task NextNumberAsync()
        {
            string prefix = settings.PartNumberPrefix;
            using (var f = new Form { Text = "Part number prefix", Width = 320, Height = 150, FormBorderStyle = FormBorderStyle.FixedDialog,
                                      StartPosition = FormStartPosition.CenterParent, MinimizeBox = false, MaximizeBox = false })
            {
                var tb = new TextBox { Text = prefix, Left = 12, Top = 14, Width = 280 };
                var ok = new Button { Text = "Get number", Left = 192, Top = 50, Width = 100, DialogResult = DialogResult.OK };
                f.Controls.Add(tb);
                f.Controls.Add(ok);
                f.AcceptButton = ok;
                if (f.ShowDialog(this) != DialogResult.OK) return;
                prefix = tb.Text.Trim();
            }
            pnBox.Text = await session.NextNumberAsync(state?.Document, prefix);
            SaveSettings();
        }

        protected override void Dispose(bool disposing)
        {
            if (disposing) api.Dispose();
            base.Dispose(disposing);
        }
    }
}
