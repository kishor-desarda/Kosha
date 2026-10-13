// SPDX-License-Identifier: Apache-2.0
using System;
using System.IO;
using System.Windows.Forms;
using Kosha.Client;

namespace Kosha.Desktop
{
    /// <summary>Sign in to Kosha and choose the workspace folder. The token is saved protected with Windows DPAPI.</summary>
    sealed class SignInForm : Form
    {
        readonly DesktopApp app;
        readonly TextBox serverBox;
        readonly TextBox workspaceBox;
        readonly TextBox userBox;
        readonly TextBox passBox;
        readonly Label errorLabel;
        readonly Button signInButton;
        readonly Button cancelButton;

        public SignInForm(DesktopApp app)
        {
            this.app = app;
            Ui.StandardDialog(this, "Sign in to Kosha");
            ShowInTaskbar = true;

            serverBox = Ui.Input("Kosha server address", app.Settings.ServerUrl);
            workspaceBox = Ui.Input("Local workspace folder", app.Settings.WorkspaceRoot);
            userBox = Ui.Input("User name", app.Settings.Username);
            passBox = Ui.Input("Password");
            passBox.UseSystemPasswordChar = true;
            errorLabel = Ui.Text("");
            errorLabel.ForeColor = DesktopApp.ErrorInk;
            errorLabel.Visible = false;

            var browse = Ui.Button("Choose folder…");
            browse.Margin = new Padding(0, 4, 0, 0);
            browse.Click += (s, e) => ChooseFolder();

            signInButton = Ui.Button("Sign in", primary: true);
            signInButton.Click += async (s, e) => await SignInAsync();
            cancelButton = Ui.Button("Cancel");
            cancelButton.DialogResult = DialogResult.Cancel;
            AcceptButton = signInButton;
            CancelButton = cancelButton;

            var buttons = Ui.ButtonRow();
            buttons.Controls.Add(signInButton);
            buttons.Controls.Add(cancelButton);

            var stack = Ui.Stack();
            stack.Controls.Add(Ui.Text("Sign in with your Kosha account. Kosha Desktop and the Kosha CAD add-ins share this sign-in and workspace folder on this PC."));
            stack.Controls.Add(Ui.Caption("Kosha server address"));
            stack.Controls.Add(serverBox);
            stack.Controls.Add(Ui.Caption("Local workspace folder (its sub-folders are your Kosha folders)"));
            stack.Controls.Add(workspaceBox);
            stack.Controls.Add(browse);
            stack.Controls.Add(Ui.Caption("User name"));
            stack.Controls.Add(userBox);
            stack.Controls.Add(Ui.Caption("Password"));
            stack.Controls.Add(passBox);
            stack.Controls.Add(errorLabel);
            stack.Controls.Add(buttons);
            Controls.Add(stack);

            Shown += (s, e) => (string.IsNullOrEmpty(userBox.Text) ? userBox : passBox).Focus();
        }

        void ChooseFolder()
        {
            try
            {
                using (var dlg = new FolderBrowserDialog { Description = "Choose your local Kosha workspace folder", UseDescriptionForTitle = true, ShowNewFolderButton = true })
                {
                    if (Directory.Exists(workspaceBox.Text)) dlg.SelectedPath = workspaceBox.Text;
                    if (dlg.ShowDialog(this) == DialogResult.OK) workspaceBox.Text = dlg.SelectedPath;
                }
            }
            catch (Exception ex)
            {
                ShowError("The folder picker could not open: " + ex.Message);
            }
        }

        void ShowError(string message)
        {
            errorLabel.Text = message;
            errorLabel.Visible = message.Length > 0;
        }

        async System.Threading.Tasks.Task SignInAsync()
        {
            string server = serverBox.Text.Trim();
            string workspace = workspaceBox.Text.Trim();
            if (!server.StartsWith("https://", StringComparison.OrdinalIgnoreCase) && !server.StartsWith("http://", StringComparison.OrdinalIgnoreCase))
            {
                ShowError("Enter the Kosha server address, starting with https:// (or http://), for example https://kosha.example.com.");
                serverBox.Focus();
                return;
            }
            if (workspace.Length == 0)
            {
                ShowError("Choose a local workspace folder, for example C:\\Kosha.");
                workspaceBox.Focus();
                return;
            }
            if (userBox.Text.Trim().Length == 0 || passBox.Text.Length == 0)
            {
                ShowError("Enter your Kosha user name and password.");
                (userBox.Text.Trim().Length == 0 ? userBox : passBox).Focus();
                return;
            }
            try
            {
                Directory.CreateDirectory(workspace);
            }
            catch (Exception ex) when (ex is IOException || ex is UnauthorizedAccessException || ex is ArgumentException || ex is NotSupportedException)
            {
                ShowError("The workspace folder " + workspace + " cannot be used: " + ex.Message);
                return;
            }

            ShowError("");
            UseWaitCursor = true;
            signInButton.Enabled = false;
            try
            {
                app.Settings.ServerUrl = server;
                app.Settings.WorkspaceRoot = workspace;
                await app.Session.SignInAsync(userBox.Text.Trim(), passBox.Text);
                passBox.Text = "";
                app.SaveSettings();
                DialogResult = DialogResult.OK;
                Close();
            }
            catch (KoshaException ex)
            {
                ShowError(ex.Message);
                passBox.SelectAll();
                passBox.Focus();
            }
            catch (Exception ex)
            {
                ShowError("Signing in did not work: " + ex.Message);
            }
            finally
            {
                UseWaitCursor = false;
                signInButton.Enabled = true;
            }
        }
    }
}
