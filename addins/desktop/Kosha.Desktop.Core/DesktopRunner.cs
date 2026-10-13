// SPDX-License-Identifier: Apache-2.0
using System;
using System.Globalization;
using System.Threading.Tasks;
using Kosha.Client;

namespace Kosha.Desktop.Core
{
    /// <summary>The result of one Explorer verb, ready to show: a message in plain words and, perhaps, a page to open.</summary>
    public sealed class VerbOutcome
    {
        public bool Ok { get; set; }

        /// <summary>The person said no to a question; nothing was done and nothing is shown.</summary>
        public bool Cancelled { get; set; }

        /// <summary>Kosha needs a sign-in first; show the sign-in window and run the verb again.</summary>
        public bool NeedsSignIn { get; set; }

        public string Message { get; set; } = "";

        /// <summary>The file's page in Kosha, offered as "Open in Kosha" (and opened at once for Show in Kosha).</summary>
        public string Url { get; set; }
    }

    /// <summary>
    /// Runs one verb from the Explorer menu or the tray and turns every outcome, including every error, into a
    /// <see cref="VerbOutcome"/>: an exception never reaches Explorer or a window. Each finished action is added
    /// to the activity log for the sync status list.
    /// </summary>
    public sealed class DesktopRunner
    {
        readonly DesktopSession session;
        readonly ActivityLog log;

        public DesktopRunner(DesktopSession session, ActivityLog log)
        {
            this.session = session ?? throw new ArgumentNullException(nameof(session));
            this.log = log;
        }

        public DesktopSession Session => session;

        public async Task<VerbOutcome> RunAsync(DesktopVerb verb, string path, string comment = "", bool keepCheckedOut = false)
        {
            VerbOutcome outcome;
            try
            {
                outcome = await RunCoreAsync(verb, path, comment, keepCheckedOut);
            }
            catch (KoshaException ex) when (ex.IsSignInNeeded)
            {
                return new VerbOutcome { NeedsSignIn = true, Message = "Sign in to Kosha first. Nothing was changed." };
            }
            catch (KoshaException ex)
            {
                outcome = new VerbOutcome { Message = ex.Message };
            }
            catch (Exception ex)
            {
                outcome = new VerbOutcome { Message = "Something went wrong in Kosha Desktop: " + ex.Message + " Your file on this PC was not changed." };
            }
            if (!outcome.Cancelled)
                log?.Add(new ActivityEntry { Action = DesktopCommand.Describe(verb), Path = path ?? "", Ok = outcome.Ok, Message = outcome.Message });
            return outcome;
        }

        async Task<VerbOutcome> RunCoreAsync(DesktopVerb verb, string path, string comment, bool keepCheckedOut)
        {
            switch (verb)
            {
                case DesktopVerb.CheckIn:
                {
                    var r = await session.CheckInAsync(path, comment, keepCheckedOut);
                    return Done(r.Summary(), session.WebUrl(r.Status));
                }
                case DesktopVerb.CheckOut:
                {
                    var r = await session.CheckOutAsync(path);
                    if (r.Cancelled) return new VerbOutcome { Cancelled = true };
                    string name = System.IO.Path.GetFileName(path);
                    string msg = name + " is checked out to you. Edit and save it as usual, then use Check in to Kosha.";
                    if (r.GotLatest) msg = "Your copy was replaced with the latest version (the old one is kept in " + r.KeptCopy + "). " + msg;
                    return Done(msg, session.WebUrl(r.Status));
                }
                case DesktopVerb.GetLatest:
                {
                    var r = await session.GetLatestAsync(path);
                    if (r.Cancelled) return new VerbOutcome { Cancelled = true };
                    string name = System.IO.Path.GetFileName(path);
                    string which = string.Format(CultureInfo.InvariantCulture, "revision {0}, version {1}", r.Status?.Revision, r.Status?.Version);
                    return Done(r.AlreadyLatest
                                    ? "You already have the latest version of " + name + " (" + which + ")."
                                    : name + " is now the latest version (" + which + "). Your previous copy is kept in " + r.KeptCopy + ".",
                                session.WebUrl(r.Status));
                }
                case DesktopVerb.UndoCheckOut:
                {
                    if (!await session.UndoCheckOutAsync(path)) return new VerbOutcome { Cancelled = true };
                    return Done(System.IO.Path.GetFileName(path) + " is no longer checked out. Your changes are still in the file on this PC.", null);
                }
                case DesktopVerb.Show:
                {
                    string url = await session.WebUrlForAsync(path);
                    return Done("Opening " + System.IO.Path.GetFileName(path) + " in Kosha.", url);
                }
                default:
                    return new VerbOutcome { Message = "\"" + DesktopCommand.Describe(verb) + "\" is not something Kosha Desktop does for a file." };
            }
        }

        static VerbOutcome Done(string message, string url) => new VerbOutcome { Ok = true, Message = message, Url = url };
    }
}
