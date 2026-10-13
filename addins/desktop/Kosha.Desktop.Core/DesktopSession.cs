// SPDX-License-Identifier: Apache-2.0
using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Threading.Tasks;
using Kosha.CadCore;
using Kosha.Client;

namespace Kosha.Desktop.Core
{
    /// <summary>What Kosha knows about one file on this PC, in plain words, and what can be done with it now.</summary>
    public sealed class FileState
    {
        public string Path { get; set; } = "";
        public string FileName => System.IO.Path.GetFileName(Path ?? "");

        /// <summary>The Kosha folder ("Kosha/Products/Mixers"), or null when the file is not in a sub-folder of the workspace.</summary>
        public string Folder { get; set; }

        public DocStatus Status { get; set; }
        public bool IsInKosha => Status != null && Status.Exists;
        public bool CheckedOutToMe { get; set; }

        /// <summary>The file on this PC is not the same as the latest version in Kosha.</summary>
        public bool LocalDiffers { get; set; }

        public bool CanCheckIn { get; set; }
        public bool CanCheckOut { get; set; }
        public bool CanGetLatest { get; set; }
        public bool CanUndo { get; set; }
        public string Message { get; set; } = "";

        /// <summary>Why the file cannot be checked in now; empty when it can.</summary>
        public string CheckInBlocked { get; set; } = "";
    }

    public sealed class CheckInResult
    {
        public DocStatus Status { get; set; }
        public string Folder { get; set; } = "";
        public bool IsNew { get; set; }

        /// <summary>Kosha already had exactly this content, so no new version was made.</summary>
        public bool Unchanged { get; set; }

        public bool KeptCheckedOut { get; set; }

        public string Summary()
        {
            var s = Status ?? new DocStatus();
            string msg = IsNew
                ? string.Format(CultureInfo.InvariantCulture, "Added {0} to {1} as revision {2}, version {3}.", s.FileName, Folder, s.Revision, s.Version)
                : Unchanged
                    ? string.Format(CultureInfo.InvariantCulture, "Kosha already had this exact content of {0}, so no new version was made.", s.FileName)
                    : string.Format(CultureInfo.InvariantCulture, "Checked in {0} as version {1} of revision {2} in {3}.", s.FileName, s.Version, s.Revision, Folder);
            return msg + (KeptCheckedOut ? " It stays checked out to you."
                                         : " The file on this PC is read-only until you check it out again.");
        }
    }

    public sealed class GetLatestResult
    {
        public DocStatus Status { get; set; }
        public bool AlreadyLatest { get; set; }
        public bool Cancelled { get; set; }

        /// <summary>Where the previous copy on this PC was kept, or null.</summary>
        public string KeptCopy { get; set; }
    }

    public sealed class CheckOutResult
    {
        public DocStatus Status { get; set; }
        public bool Cancelled { get; set; }

        /// <summary>The copy on this PC was replaced with the latest version first; the old one is in <see cref="KeptCopy"/>.</summary>
        public bool GotLatest { get; set; }

        public string KeptCopy { get; set; }
    }

    public enum LocalCopy { Same, Changed, Missing, NoPlace }

    /// <summary>A file checked out to the user, for the tray's sync status list.</summary>
    public sealed class SyncRow
    {
        public DocStatus Status { get; set; }
        public string KoshaFolder { get; set; } = "";

        /// <summary>Where the file belongs in the workspace, or null when it has no place there.</summary>
        public string LocalPath { get; set; }

        public LocalCopy Local { get; set; }
        public string FileName => Status?.FileName ?? "";

        public string Describe()
        {
            switch (Local)
            {
                case LocalCopy.Changed: return "Changed on this PC: check it in when you are done";
                case LocalCopy.Same: return "Same as in Kosha: no changes yet";
                case LocalCopy.Missing: return "Not on this PC (expected in " + LocalPath + ")";
                default: return "Not in a workspace folder: open it in Kosha";
            }
        }
    }

    /// <summary>
    /// Kosha for plain files (Office documents, PDFs, images ...): check in, check out, get latest, undo, show.
    /// The workspace folder decides the Kosha folder, exactly as in the CAD add-ins. Nothing on this PC is lost:
    /// a replaced file is kept in <see cref="KeepFolder"/>, and every failure leaves the file as it was.
    /// Call it from the UI thread: awaits resume there, so the prompts can show windows.
    /// </summary>
    public sealed class DesktopSession
    {
        /// <summary>Stored with every version checked in from here.</summary>
        public const string SourceName = "Kosha Desktop";

        readonly KoshaApiClient api;
        readonly KoshaSettings settings;
        readonly IUserPrompts prompts;
        UserInfo me;

        public DesktopSession(KoshaApiClient api, KoshaSettings settings, IUserPrompts prompts)
        {
            this.api = api ?? throw new ArgumentNullException(nameof(api));
            this.settings = settings ?? throw new ArgumentNullException(nameof(settings));
            this.prompts = prompts ?? throw new ArgumentNullException(nameof(prompts));
        }

        public KoshaSettings Settings => settings;
        public bool IsSignedIn => !string.IsNullOrEmpty(settings.Token);
        public UserInfo User => me;
        public string KeepFolder { get; set; } = LocalFiles.DefaultKeepFolder;
        public string HomeUrl => settings.ServerUrl.TrimEnd('/') + "/";

        public string WebUrl(DocStatus s) =>
            settings.ServerUrl.TrimEnd('/') + (s != null && s.Exists && s.Id > 0 ? "/#/doc/" + s.Id.ToString(CultureInfo.InvariantCulture) : "/");

        public async Task<UserInfo> SignInAsync(string username, string password)
        {
            me = await api.SignInAsync(username, password);
            return me;
        }

        public async Task<UserInfo> WhoAmIAsync()
        {
            if (me == null) me = await api.MeAsync();
            return me;
        }

        public void SignOut()
        {
            settings.Token = "";
            me = null;
        }

        // ------------------------------------------------------------------ status
        public async Task<FileState> DescribeAsync(string path)
        {
            RequireFile(path);
            var st = new FileState { Path = path, Folder = settings.KoshaFolderFor(path) };
            if (st.Folder == null)
            {
                st.Message = st.CheckInBlocked = OutsideWorkspace(path);
                return st;
            }
            st.Status = await api.StatusAsync(st.Folder, st.FileName);
            if (!st.Status.Exists)
            {
                st.Message = "Not in Kosha yet. Check it in to add it to " + st.Folder + ".";
                st.CanCheckIn = true;
                return st;
            }
            var user = await WhoAmIAsync();
            var s = st.Status;
            st.CheckedOutToMe = s.CheckedOutBy.HasValue && user != null && s.CheckedOutBy.Value == user.Id;
            st.LocalDiffers = LocalDiffers(path, s.LatestSha);
            string who = s.CheckedOutBy == null ? "not checked out"
                       : st.CheckedOutToMe ? "checked out to you"
                       : "checked out by " + (string.IsNullOrEmpty(s.CheckedOutName) ? "someone else" : s.CheckedOutName);
            st.Message = string.Format(CultureInfo.InvariantCulture, "In Kosha: revision {0}, {1}, version {2}, {3}.",
                                       s.Revision, s.StateLabel, s.Version, who) +
                         (st.LocalDiffers ? " Your copy is different from the latest version." : "");
            var checkout = s.Action("checkout");
            st.CanCheckOut = !st.CheckedOutToMe && checkout.Ok;
            st.CanCheckIn = st.CheckedOutToMe;
            st.CanUndo = st.CheckedOutToMe;
            st.CanGetLatest = st.LocalDiffers;
            if (!st.CanCheckIn)
            {
                st.CheckInBlocked = s.CheckedOutBy != null
                    ? st.FileName + " is " + who + ", so you cannot check it in. Nothing was changed."
                    : checkout.Ok
                        ? st.FileName + " is already in Kosha and is not checked out to you. Check it out first " +
                          "(right-click › Check out from Kosha), make your changes, then check it in. Nothing was changed."
                        : st.FileName + " is already in Kosha and cannot be changed now: " + (checkout.Why ?? "") + " Nothing was changed.";
            }
            return st;
        }

        string OutsideWorkspace(string path)
        {
            string root = settings.WorkspaceRoot ?? "";
            return System.IO.Path.GetFileName(path) + " is not in a sub-folder of your Kosha workspace (" + root + "). " +
                   "The sub-folder a file is in decides its Kosha folder: move or copy the file into one, for example " +
                   System.IO.Path.Combine(root, "Products") + ", and try again. Nothing was changed.";
        }

        static void RequireFile(string path)
        {
            if (string.IsNullOrWhiteSpace(path) || !File.Exists(path))
                throw new KoshaException("The file " + path + " was not found. It may have been moved or renamed. Nothing was changed.", 0, "file_missing");
        }

        static bool LocalDiffers(string path, string latestSha)
        {
            if (string.IsNullOrEmpty(latestSha) || !File.Exists(path)) return false;
            try { return !string.Equals(KoshaApiClient.Sha256File(path), latestSha, StringComparison.OrdinalIgnoreCase); }
            catch (Exception ex) when (ex is IOException || ex is UnauthorizedAccessException) { return false; }
        }

        /// <summary>The file's bytes, also while a program such as Word has it open.</summary>
        static byte[] ReadShared(string path)
        {
            try
            {
                using (var fs = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete))
                using (var ms = new MemoryStream())
                {
                    fs.CopyTo(ms);
                    return ms.ToArray();
                }
            }
            catch (Exception ex) when (ex is IOException || ex is UnauthorizedAccessException)
            {
                throw new KoshaException(System.IO.Path.GetFileName(path) + " could not be read (" + ex.Message + "). If another program " +
                                         "has it open, save and close it there, then try again. Nothing was checked in.", 0, "file_in_use");
            }
        }

        // ----------------------------------------------------------------- actions
        /// <summary>Adds a new file to Kosha, or checks in a new version of a file checked out to you. No CAD data is sent.</summary>
        public async Task<CheckInResult> CheckInAsync(string path, string comment, bool keepCheckedOut)
        {
            var st = await DescribeAsync(path);
            if (!st.CanCheckIn)
                throw new KoshaException(st.CheckInBlocked, 0, st.Folder == null ? "outside_workspace" : "not_checked_out");
            byte[] bytes = ReadShared(path);
            int before = st.IsInKosha ? st.Status.Version : 0;
            var result = await api.CheckInAsync(new CheckinRequest
            {
                Folder = st.Folder,
                FileName = st.FileName,
                File = bytes,
                Properties = new Dictionary<string, string>(),
                Comment = comment ?? "",
                KeepCheckedOut = keepCheckedOut,
                Source = SourceName,
            });
            LocalFiles.SetReadOnly(path, !keepCheckedOut);
            return new CheckInResult
            {
                Status = result,
                Folder = st.Folder,
                IsNew = !st.IsInKosha,
                Unchanged = st.IsInKosha && result != null && result.Version == before,
                KeptCheckedOut = keepCheckedOut,
            };
        }

        /// <summary>
        /// Locks the file in Kosha for you and makes it writable. When your copy is not the latest version, it is first
        /// replaced with the latest one (after asking; your copy is kept), so you never edit an old version by mistake.
        /// </summary>
        public async Task<CheckOutResult> CheckOutAsync(string path)
        {
            var st = await DescribeAsync(path);
            RequireInKosha(st);
            if (st.CheckedOutToMe)
            {
                LocalFiles.SetReadOnly(path, false);
                return new CheckOutResult { Status = st.Status };
            }
            var checkout = st.Status.Action("checkout");
            if (!checkout.Ok)
                throw new KoshaException(st.FileName + " cannot be checked out: " + (checkout.Why ?? "") + " Nothing was changed.", 409, "cannot_check_out");
            var outcome = new CheckOutResult();
            if (st.LocalDiffers)
            {
                if (!prompts.Confirm("Check out", "Your copy of " + st.FileName + " is not the latest version in Kosha (revision " + st.Status.Revision +
                                                  ", version " + st.Status.Version.ToString(CultureInfo.InvariantCulture) + "). Kosha Desktop will replace it " +
                                                  "with the latest version and then check it out. Your copy is kept in " + KeepFolder + "."))
                    return new CheckOutResult { Status = st.Status, Cancelled = true };
                outcome.KeptCopy = (await ReplaceWithLatestAsync(st, writable: false)).KeptCopy;
                outcome.GotLatest = true;
            }
            outcome.Status = await api.CheckOutAsync(st.Folder, st.FileName);
            LocalFiles.SetReadOnly(path, false);
            return outcome;
        }

        /// <summary>Replaces the copy on this PC with the latest version in Kosha, after asking. The old copy is kept.</summary>
        public async Task<GetLatestResult> GetLatestAsync(string path)
        {
            var st = await DescribeAsync(path);
            RequireInKosha(st);
            if (!st.LocalDiffers) return new GetLatestResult { Status = st.Status, AlreadyLatest = true };
            string question = st.CheckedOutToMe
                ? st.FileName + " is checked out to you. Replacing it puts aside the changes you made on this PC since your last check-in " +
                  "(they are kept in " + KeepFolder + ") and keeps the file checked out. Replace it with the latest version?"
                : "Replace your copy of " + st.FileName + " with the latest version in Kosha (revision " + st.Status.Revision + ", version " +
                  st.Status.Version.ToString(CultureInfo.InvariantCulture) + ")? Your copy is kept in " + KeepFolder + ".";
            if (!prompts.Confirm("Get latest version", question)) return new GetLatestResult { Status = st.Status, Cancelled = true };
            return await ReplaceWithLatestAsync(st, writable: st.CheckedOutToMe);
        }

        async Task<GetLatestResult> ReplaceWithLatestAsync(FileState st, bool writable)
        {
            byte[] bytes = await api.DownloadAsync(st.Status.Id, st.Status.LatestSha);   // checked against its fingerprint first
            string kept;
            try
            {
                kept = LocalFiles.ReplaceKeepingOld(st.Path, bytes, KeepFolder);
            }
            catch (Exception ex) when (ex is IOException || ex is UnauthorizedAccessException)
            {
                throw new KoshaException(st.FileName + " could not be replaced (" + ex.Message + "). If it is open in another program, close it " +
                                         "there and try again. Your file was not changed.", 0, "file_in_use");
            }
            LocalFiles.SetReadOnly(st.Path, !writable);
            return new GetLatestResult { Status = st.Status, KeptCopy = kept };
        }

        /// <summary>Unlocks a file checked out to you without checking it in. Your changes stay in the file on this PC.</summary>
        public async Task<bool> UndoCheckOutAsync(string path)
        {
            var st = await DescribeAsync(path);
            RequireInKosha(st);
            if (!st.CheckedOutToMe)
                throw new KoshaException(st.FileName + " is not checked out to you. Nothing was changed.", 409, "not_checked_out");
            return await UndoAsync(st.Status, path);
        }

        /// <summary>Undo check-out from the sync status list, also for a file that is not on this PC.</summary>
        public Task<bool> UndoCheckOutAsync(SyncRow row) => UndoAsync(row.Status, row.LocalPath);

        async Task<bool> UndoAsync(DocStatus status, string localPath)
        {
            if (!prompts.Confirm("Undo check-out", "Unlock " + status.FileName + " without checking it in? Your changes stay in the file on " +
                                                   "this PC, but Kosha keeps the last checked-in version."))
                return false;
            await api.UndoCheckOutAsync(status.Id);
            if (!string.IsNullOrEmpty(localPath)) LocalFiles.SetReadOnly(localPath, true);
            return true;
        }

        /// <summary>The page of the file in Kosha's web client.</summary>
        public async Task<string> WebUrlForAsync(string path)
        {
            var st = await DescribeAsync(path);
            RequireInKosha(st);
            return WebUrl(st.Status);
        }

        void RequireInKosha(FileState st)
        {
            if (st.Folder == null) throw new KoshaException(OutsideWorkspace(st.Path), 0, "outside_workspace");
            if (!st.IsInKosha)
                throw new KoshaException(st.FileName + " is not in Kosha yet. Use Check in to Kosha to add it to " + st.Folder + ". Nothing was changed.",
                                         404, "not_in_kosha");
        }

        // ------------------------------------------------------------- sync status
        /// <summary>The files checked out to you, with the state of the copy on this PC (at most 100).</summary>
        public async Task<List<SyncRow>> CheckedOutToMeAsync()
        {
            var docs = await api.CheckedOutToMeAsync();
            var paths = WorkspaceMap.FolderPaths(await api.FoldersAsync());
            var rows = new List<SyncRow>();
            foreach (var d in docs.Take(100))
            {
                string folder = paths.TryGetValue(d.FolderId, out var p) ? p : "";
                var row = new SyncRow { Status = d, KoshaFolder = folder, LocalPath = WorkspaceMap.LocalPathFor(settings, folder, d.FileName) };
                if (row.LocalPath == null) row.Local = LocalCopy.NoPlace;
                else if (!File.Exists(row.LocalPath)) row.Local = LocalCopy.Missing;
                else
                {
                    var s = await api.StatusAsync(folder, d.FileName);   // the list has no fingerprints
                    row.Local = LocalDiffers(row.LocalPath, s.LatestSha) ? LocalCopy.Changed : LocalCopy.Same;
                }
                rows.Add(row);
            }
            return rows;
        }
    }
}
