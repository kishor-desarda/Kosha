// SPDX-License-Identifier: Apache-2.0
using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Threading.Tasks;
using Kosha.Client;

namespace Kosha.CadCore
{
    /// <summary>Questions the session needs a person to answer. The pane implements it with message boxes.</summary>
    public interface IUserPrompts
    {
        bool Confirm(string title, string message);
    }

    /// <summary>Everything the pane shows about the active document.</summary>
    public sealed class DocumentState
    {
        public CadDocument Document { get; set; }
        public string Title { get; set; } = "No document open";
        public string Folder { get; set; } = "";
        public string PartNumber { get; set; } = "";
        public string Description { get; set; } = "";
        public string Material { get; set; } = "";
        public string Message { get; set; } = "";
        public string Warning { get; set; } = "";
        public DocStatus Status { get; set; }
        public bool IsInKosha => Status != null && Status.Exists;
        public bool CheckedOutToMe { get; set; }
        public bool CanCheckOut { get; set; }
        public bool CanCheckIn { get; set; }
        public bool CanUndo { get; set; }
        public bool CanGetLatest { get; set; }
        public bool CanOpenInBrowser { get; set; }
        public bool OffersComponents => Document != null && Document.Kind == CadDocumentKind.Assembly;
    }

    public sealed class CheckinOutcome
    {
        public DocStatus Result { get; set; }
        public List<string> AlsoCheckedIn { get; } = new List<string>();
        public List<string> Skipped { get; } = new List<string>();

        public string Summary()
        {
            string msg = string.Format(CultureInfo.InvariantCulture, "Checked in {0} as version {1} of revision {2}.",
                Result?.FileName, Result?.Version, Result?.Revision);
            if (AlsoCheckedIn.Count > 0) msg += Environment.NewLine + Environment.NewLine + "Also checked in: " + string.Join(", ", AlsoCheckedIn);
            if (Skipped.Count > 0) msg += Environment.NewLine + Environment.NewLine + "Skipped: " + string.Join(", ", Skipped);
            if (Result?.Bom != null) msg += Environment.NewLine + Environment.NewLine + "The engineering BOM was updated from the assembly.";
            return msg;
        }
    }

    /// <summary>
    /// The shared CAD workflow. Call it from the CAD program's main (UI) thread: awaits resume on that
    /// thread, so every <see cref="ICadHost"/> call stays on it while network work runs in the background.
    /// </summary>
    public sealed class CadSession
    {
        readonly ICadHost host;
        readonly KoshaApiClient api;
        readonly KoshaSettings settings;
        readonly IUserPrompts prompts;
        UserInfo me;

        public CadSession(ICadHost host, KoshaApiClient api, KoshaSettings settings, IUserPrompts prompts)
        {
            this.host = host ?? throw new ArgumentNullException(nameof(host));
            this.api = api ?? throw new ArgumentNullException(nameof(api));
            this.settings = settings ?? throw new ArgumentNullException(nameof(settings));
            this.prompts = prompts ?? throw new ArgumentNullException(nameof(prompts));
        }

        public ICadHost Host => host;
        public KoshaSettings Settings => settings;
        public string KeepFolder { get; set; } = LocalFiles.DefaultKeepFolder;

        public async Task<UserInfo> SignInAsync(string username, string password)
        {
            me = await api.SignInAsync(username, password);
            return me;
        }

        public void SignOut()
        {
            settings.Token = "";
            me = null;
        }

        async Task<int> MyIdAsync()
        {
            if (me == null) me = await api.MeAsync();
            return me?.Id ?? -1;
        }

        public string WebUrl(DocumentState st) =>
            settings.ServerUrl.TrimEnd('/') + (st?.IsInKosha == true ? "/#/doc/" + st.Status.Id : "/");

        // ------------------------------------------------------------------ status
        public async Task<DocumentState> DescribeAsync(CadDocument doc)
        {
            var st = new DocumentState { Document = doc };
            if (doc == null)
            {
                st.Message = "Open a part, assembly or drawing to work with Kosha.";
                return st;
            }
            st.Title = doc.IsSaved ? doc.FileName : doc.Title;
            if (!doc.IsSaved)
            {
                st.Message = "Save the document inside your workspace folder (" + settings.WorkspaceRoot + ") first.";
                return st;
            }
            string folder = settings.KoshaFolderFor(doc.Path);
            if (folder == null)
            {
                st.Message = "This file is outside your Kosha workspace. Save it in a sub-folder of " + settings.WorkspaceRoot + ".";
                return st;
            }
            st.Folder = folder;
            var props = host.ReadProperties(doc) ?? new Dictionary<string, string>();
            st.PartNumber = PartNumbers.PartNumberOf(props, doc.Path);
            st.Description = PartNumbers.DescriptionOf(props, doc.Path);
            st.Material = FirstNonEmpty(host.ReadMaterial(doc), PartNumbers.Pick(props, PartNumbers.MaterialKeys));
            st.CanOpenInBrowser = true;

            st.Status = await api.StatusAsync(folder, doc.FileName);
            if (!st.Status.Exists)
            {
                st.Message = "Not in Kosha yet." + Environment.NewLine + "Press Check in to add it as revision A.";
                st.CanCheckIn = true;
                st.Warning = await SimilarPartWarningAsync(doc);
                return st;
            }
            int myId = await MyIdAsync();
            var s = st.Status;
            st.CheckedOutToMe = s.CheckedOutBy.HasValue && s.CheckedOutBy.Value == myId;
            string who = s.CheckedOutBy == null ? "Not checked out" : st.CheckedOutToMe ? "Checked out to you" : "Checked out by " + s.CheckedOutName;
            st.Message = string.Format(CultureInfo.InvariantCulture, "Revision {0} · {1} · version {2}{3}{4}",
                s.Revision, s.StateLabel, s.Version, Environment.NewLine, who);
            var checkout = s.Action("checkout");
            st.CanCheckOut = checkout.Ok;
            st.CanCheckIn = st.CheckedOutToMe;
            st.CanUndo = st.CheckedOutToMe;
            st.CanGetLatest = !st.CheckedOutToMe;
            if (!st.CheckedOutToMe && LocalDiffers(doc.Path, s.LatestSha))
                st.Warning = "Your local copy is different from the latest version in Kosha. Use Get latest version before editing.";
            else if (!checkout.Ok && !st.CheckedOutToMe)
                st.Warning = checkout.Why ?? "";
            return st;
        }

        static string FirstNonEmpty(params string[] values) => values.FirstOrDefault(v => !string.IsNullOrWhiteSpace(v)) ?? "";

        static bool LocalDiffers(string path, string latestSha)
        {
            if (string.IsNullOrEmpty(latestSha) || !File.Exists(path)) return false;
            try { return !string.Equals(KoshaApiClient.Sha256File(path), latestSha, StringComparison.OrdinalIgnoreCase); }
            catch (Exception ex) when (ex is IOException || ex is UnauthorizedAccessException) { return false; }
        }

        async Task<string> SimilarPartWarningAsync(CadDocument doc)
        {
            if (doc.Kind != CadDocumentKind.Part) return "";
            var p = host.ReadPhysical(doc);
            if (p == null || p.VolumeMm3 <= 0 || p.BoxMm == null || p.BoxMm.Length < 3) return "";
            try
            {
                var sim = await api.SimilarAsync(p.BoxMm[0], p.BoxMm[1], p.BoxMm[2], p.VolumeMm3, p.AreaMm2);
                var top = sim.OrderByDescending(x => x.Score).FirstOrDefault();
                return top == null ? "" : string.Format(CultureInfo.InvariantCulture,
                    "A very similar part already exists: {0} {1} ({2:P0} match). Consider reusing it instead of a new part.",
                    top.PartNumber, top.Description, top.Score);
            }
            catch (KoshaException)
            {
                return "";   // the warning is a help, never a blocker
            }
        }

        // ----------------------------------------------------------------- actions
        public async Task<string> NextNumberAsync(CadDocument doc, string prefix)
        {
            if (string.IsNullOrWhiteSpace(prefix)) return "";
            settings.PartNumberPrefix = prefix.Trim();
            string pn = await api.NextPartNumberAsync(settings.PartNumberPrefix);
            if (doc != null && !string.IsNullOrEmpty(pn)) host.WriteProperty(doc, "PartNo", pn);
            return pn;
        }

        public async Task CheckOutAsync(DocumentState st)
        {
            var doc = st.Document;
            if (LocalDiffers(doc.Path, st.Status?.LatestSha) &&
                !prompts.Confirm("Check out", "Your local copy is not the latest version in Kosha. Check out anyway? " +
                                              "(Use Get latest version first to avoid working on an old copy.)"))
                return;
            await api.CheckOutAsync(st.Folder, doc.FileName);
            LocalFiles.SetReadOnly(doc.Path, false);
        }

        public async Task UndoCheckOutAsync(DocumentState st)
        {
            if (st.Status == null || !st.Status.Exists) return;
            if (!prompts.Confirm("Undo check-out", "Unlock the file without checking in? Your local edits stay in the file on this PC, " +
                                                   "but Kosha keeps the last checked-in version."))
                return;
            await api.UndoCheckOutAsync(st.Status.Id);
            LocalFiles.SetReadOnly(st.Document.Path, true);
        }

        /// <summary>Replaces the local copy with Kosha's latest version. The old local file is kept in <see cref="KeepFolder"/>.</summary>
        public async Task<string> GetLatestAsync(DocumentState st)
        {
            if (st.Status == null || !st.Status.Exists) return "";
            var doc = st.Document;
            if (!prompts.Confirm("Get latest version", "Replace your local copy with the latest version in Kosha? " + host.ProgramName +
                                                       " closes the document and opens it again. Your current local file is kept in " + KeepFolder + "."))
                return "";
            byte[] bytes = await api.DownloadAsync(st.Status.Id, st.Status.LatestSha);   // verified before anything is touched
            string path = doc.Path;
            var kind = doc.Kind;
            host.Close(doc);
            string kept = LocalFiles.ReplaceKeepingOld(path, bytes, KeepFolder);
            LocalFiles.SetReadOnly(path, true);
            host.Open(path, kind);
            return kept;
        }

        public async Task<CheckinOutcome> CheckInAsync(DocumentState st, string partNumber, string description, string comment,
                                                      bool keepCheckedOut, bool withComponents)
        {
            var doc = st.Document;
            if (!string.IsNullOrWhiteSpace(partNumber)) host.WriteProperty(doc, "PartNo", partNumber.Trim().ToUpperInvariant());
            if (!string.IsNullOrWhiteSpace(description)) host.WriteProperty(doc, "Description", description.Trim());
            if (host.HasUnsavedChanges(doc) && !host.Save(doc))
                throw new KoshaException(host.ProgramName + " could not save the document, so nothing was checked in.", 0, "save_failed");

            var outcome = new CheckinOutcome();
            if (withComponents && doc.Kind == CadDocumentKind.Assembly)
            {
                int myId = await MyIdAsync();
                foreach (var comp in host.ReferencedDocumentsDeepestFirst(doc))
                {
                    string cfolder = settings.KoshaFolderFor(comp.Path);
                    if (cfolder == null) { outcome.Skipped.Add(comp.FileName + " (outside the workspace)"); continue; }
                    var cs = await api.StatusAsync(cfolder, comp.FileName);
                    bool mine = cs.Exists && cs.CheckedOutBy.HasValue && cs.CheckedOutBy.Value == myId;
                    if (cs.Exists && !mine) continue;      // unchanged in Kosha, or someone else's
                    if (host.HasUnsavedChanges(comp) && !host.Save(comp))
                    {
                        outcome.Skipped.Add(comp.FileName + " (could not be saved)");
                        continue;
                    }
                    await CheckInOneAsync(comp, cfolder, comment, isActive: false, keep: false);
                    outcome.AlsoCheckedIn.Add(comp.FileName);
                }
            }
            outcome.Result = await CheckInOneAsync(doc, st.Folder, comment, isActive: true, keep: keepCheckedOut);
            return outcome;
        }

        async Task<DocStatus> CheckInOneAsync(CadDocument doc, string folder, string comment, bool isActive, bool keep)
        {
            var raw = host.ReadProperties(doc) ?? new Dictionary<string, string>();
            var props = new Dictionary<string, string>
            {
                { "part_number", PartNumbers.PartNumberOf(raw, doc.Path) },
                { "description", PartNumbers.DescriptionOf(raw, doc.Path) },
            };
            string material = FirstNonEmpty(host.ReadMaterial(doc), PartNumbers.Pick(raw, PartNumbers.MaterialKeys));
            if (material.Length > 0) props["material"] = material;
            var phys = doc.Kind == CadDocumentKind.Drawing ? null : host.ReadPhysical(doc);
            if (phys != null && phys.MassKg > 0) props["weight"] = phys.MassKg.ToString("0.###", CultureInfo.InvariantCulture);
            foreach (var k in new[] { "Finish", "Vendor" })
            {
                string v = PartNumbers.Pick(raw, new[] { k });
                if (v.Length > 0) props[k.ToLowerInvariant()] = v;
            }
            var request = new CheckinRequest
            {
                Folder = folder,
                FileName = doc.FileName,
                File = host.ReadFileBytes(doc),
                Properties = props,
                PreviewPng = isActive ? host.ExportPreviewPng(doc) : null,
                MeshStl = settings.ExportMesh && doc.Kind != CadDocumentKind.Drawing ? host.ExportMeshStl(doc) : null,
                Comment = comment ?? "",
                KeepCheckedOut = isActive && keep,
                Configuration = host.ActiveConfiguration(doc) ?? "",
                Source = host.ProgramName,
            };
            if (doc.Kind == CadDocumentKind.Assembly)
                request.Structure = host.ReadStructure(doc).Select(ToLine).ToList();
            if (request.File == null || request.File.Length == 0)
                throw new KoshaException("Could not read " + doc.FileName + " from disk, so it was not checked in.", 0, "read_failed");
            var result = await api.CheckInAsync(request);
            if (!request.KeepCheckedOut) LocalFiles.SetReadOnly(doc.Path, true);
            return result;
        }

        static StructureLine ToLine(CadComponent c) => new StructureLine
        {
            File = Path.GetFileName(c.Path),
            Configuration = c.Configuration ?? "",
            PartNumber = PartNumbers.PartNumberOf(c.Properties, c.Path),
            Description = PartNumbers.DescriptionOf(c.Properties, c.Path),
            Material = c.Material ?? "",
            Quantity = c.Quantity,
            IsVirtual = c.IsVirtual,
        };
    }
}
