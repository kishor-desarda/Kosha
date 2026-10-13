// SPDX-License-Identifier: Apache-2.0
using System;
using System.Drawing;
using System.IO;
using System.Threading.Tasks;
using System.Windows.Forms;
using Kosha.Client;
using Kosha.Desktop.Core;

namespace Kosha.Desktop
{
    /// <summary>
    /// The small window of one Explorer verb (or a check-in from the tray): what Kosha knows about the file, the work
    /// in progress, then the result in plain words with "Open in Kosha". Check-in first asks for a comment.
    /// Every handler catches its own errors; the window cannot be closed while Kosha is working.
    /// </summary>
    sealed class ActionForm : Form
    {
        readonly DesktopApp app;
        readonly string path;
        DesktopVerb verb;
        string url;
        bool busy;
        Func<Task> primaryAction;

        readonly Label fileLabel;
        readonly Label statusLabel;
        readonly ProgressBar progress;
        readonly FlowLayoutPanel checkInPanel;
        readonly TextBox commentBox;
        readonly CheckBox keepBox;
        readonly TextBox resultBox;
        readonly Button primaryButton;
        readonly Button openButton;
        readonly Button closeButton;

        public ActionForm(DesktopApp app, DesktopVerb verb, string path)
        {
            this.app = app;
            this.verb = verb;
            this.path = path ?? "";
            Ui.StandardDialog(this, "Kosha: " + DesktopCommand.Describe(verb));
            ShowInTaskbar = true;
            app.Prompts.Owner = this;

            fileLabel = Ui.Text(FileName, bold: true);
            statusLabel = Ui.Text("");
            progress = new ProgressBar { Style = ProgressBarStyle.Marquee, MarqueeAnimationSpeed = 30, Width = Ui.Width, Height = 8, Visible = false };

            commentBox = Ui.Input("Comment for this version");
            commentBox.Multiline = true;
            commentBox.Height = 54;
            keepBox = new CheckBox { Text = "Keep it checked out, to go on editing", AutoSize = true, Margin = new Padding(0, 6, 0, 0) };
            checkInPanel = Ui.Stack();
            checkInPanel.Padding = new Padding(0);
            checkInPanel.Dock = DockStyle.None;
            checkInPanel.Visible = false;
            checkInPanel.Controls.Add(Ui.Text("Kosha checks in the file as it is saved on disk: save it in its program first."));
            checkInPanel.Controls.Add(Ui.Caption("Comment for this version (what changed and why)"));
            checkInPanel.Controls.Add(commentBox);
            checkInPanel.Controls.Add(keepBox);

            resultBox = new TextBox
            {
                ReadOnly = true, Multiline = true, BorderStyle = BorderStyle.None, TabStop = true, Width = Ui.Width,
                BackColor = SystemColors.Control, Visible = false, Margin = new Padding(0, 8, 0, 0), AccessibleName = "Result",
            };

            primaryButton = Ui.Button("Check in", primary: true);
            primaryButton.Visible = false;
            primaryButton.Click += async (s, e) => { if (primaryAction != null) await Guard(primaryAction); };
            openButton = Ui.Button("Open in Kosha");
            openButton.Visible = false;
            openButton.Click += (s, e) => DesktopApp.OpenInBrowser(url);
            closeButton = Ui.Button("Cancel");
            closeButton.Click += (s, e) => Close();
            CancelButton = closeButton;

            var buttons = Ui.ButtonRow();
            buttons.Controls.Add(closeButton);
            buttons.Controls.Add(primaryButton);
            buttons.Controls.Add(openButton);

            var stack = Ui.Stack();
            stack.Controls.Add(fileLabel);
            stack.Controls.Add(statusLabel);
            stack.Controls.Add(progress);
            stack.Controls.Add(checkInPanel);
            stack.Controls.Add(resultBox);
            stack.Controls.Add(buttons);
            Controls.Add(stack);

            Shown += async (s, e) => await Guard(StartAsync);
            FormClosing += (s, e) => { if (busy && e.CloseReason == CloseReason.UserClosing) e.Cancel = true; };
            FormClosed += (s, e) => { if (app.Prompts.Owner == this) app.Prompts.Owner = null; };
        }

        string FileName => Path.GetFileName(path);

        /// <summary>Runs one step; any error becomes a calm message in the window.</summary>
        async Task Guard(Func<Task> step)
        {
            try
            {
                await step();
            }
            catch (Exception ex)
            {
                SetBusy(false, null);
                ShowResult(false, "Something went wrong in Kosha Desktop: " + ex.Message + " Your file on this PC was not changed.");
            }
        }

        async Task StartAsync()
        {
            if (!app.Session.IsSignedIn && !AskSignIn())
            {
                Close();
                return;
            }
            if (verb == DesktopVerb.CheckIn) await PrepareCheckInAsync();
            else await RunVerbAsync(verb);
        }

        bool AskSignIn()
        {
            using (var f = new SignInForm(app))
                return f.ShowDialog(this) == DialogResult.OK;
        }

        async Task PrepareCheckInAsync()
        {
            SetBusy(true, "Asking Kosha about this file…");
            FileState st = null;
            try
            {
                for (int attempt = 0; st == null; attempt++)
                {
                    try
                    {
                        st = await app.Session.DescribeAsync(path);
                    }
                    catch (KoshaException ex) when (ex.IsSignInNeeded && attempt == 0)
                    {
                        if (!AskSignIn()) break;
                    }
                }
            }
            catch (KoshaException ex)
            {
                SetBusy(false, null);
                ShowResult(false, ex.Message);
                return;
            }
            SetBusy(false, null);
            if (st == null)
            {
                Close();
                return;
            }
            if (!st.CanCheckIn)
            {
                statusLabel.Text = st.Message;
                ShowResult(false, st.CheckInBlocked);
                if (st.CanCheckOut) Offer("Check out", () => RunVerbAsync(DesktopVerb.CheckOut));
                return;
            }
            statusLabel.Text = st.IsInKosha
                ? st.Message + " Checking in makes a new version."
                : "Not in Kosha yet: it will be added to " + st.Folder + " as a new file.";
            checkInPanel.Visible = true;
            Offer("Check in", () => RunVerbAsync(DesktopVerb.CheckIn));
            commentBox.Focus();
        }

        void Offer(string text, Func<Task> action)
        {
            primaryAction = action;
            primaryButton.Text = text;
            primaryButton.Visible = true;
            primaryButton.Enabled = true;
            AcceptButton = primaryButton;
        }

        static string Doing(DesktopVerb v)
        {
            switch (v)
            {
                case DesktopVerb.CheckIn: return "Checking in";
                case DesktopVerb.CheckOut: return "Checking out";
                case DesktopVerb.GetLatest: return "Getting the latest version of";
                case DesktopVerb.UndoCheckOut: return "Undoing the check-out of";
                default: return "Opening";
            }
        }

        async Task RunVerbAsync(DesktopVerb v)
        {
            verb = v;
            Text = "Kosha: " + DesktopCommand.Describe(v);
            checkInPanel.Visible = false;
            primaryButton.Visible = false;
            resultBox.Visible = false;
            SetBusy(true, Doing(v) + " " + FileName + "…");
            VerbOutcome outcome;
            try
            {
                outcome = await app.Runner.RunAsync(v, path, commentBox.Text, keepBox.Checked);
                if (outcome.NeedsSignIn && AskSignIn())
                    outcome = await app.Runner.RunAsync(v, path, commentBox.Text, keepBox.Checked);
            }
            finally
            {
                SetBusy(false, null);
            }
            if (outcome.Cancelled || outcome.NeedsSignIn)
            {
                Close();
                return;
            }
            if (outcome.Ok && v == DesktopVerb.Show)
            {
                DesktopApp.OpenInBrowser(outcome.Url);
                Close();
                return;
            }
            url = outcome.Url;
            statusLabel.Text = outcome.Ok ? "Done." : "Not done.";
            ShowResult(outcome.Ok, outcome.Message);
        }

        void SetBusy(bool on, string text)
        {
            busy = on;
            UseWaitCursor = on;
            progress.Visible = on;
            if (text != null) statusLabel.Text = text;
            primaryButton.Enabled = !on;
            closeButton.Enabled = !on;
            commentBox.Enabled = keepBox.Enabled = !on;
        }

        void ShowResult(bool ok, string message)
        {
            resultBox.Text = message ?? "";
            resultBox.ForeColor = ok ? SystemColors.ControlText : DesktopApp.ErrorInk;
            resultBox.Height = TextRenderer.MeasureText(resultBox.Text, resultBox.Font, new Size(Ui.Width, int.MaxValue),
                                                        TextFormatFlags.WordBreak | TextFormatFlags.TextBoxControl).Height + 6;
            resultBox.Visible = true;
            openButton.Visible = ok && !string.IsNullOrEmpty(url);
            closeButton.Text = "Close";
            if (!primaryButton.Visible) AcceptButton = closeButton;
            resultBox.Focus();
            resultBox.Select(0, 0);
        }
    }
}
