// SPDX-License-Identifier: Apache-2.0
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Net;
using System.Net.Http;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using Kosha.Client;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace Kosha.CadCore.Tests
{
    /// <summary>A CAD program in memory: documents are real files in a temporary workspace.</summary>
    public sealed class FakeCadHost : ICadHost
    {
        public readonly Dictionary<string, Dictionary<string, string>> Props = new Dictionary<string, Dictionary<string, string>>(StringComparer.OrdinalIgnoreCase);
        public readonly Dictionary<string, List<CadDocument>> References = new Dictionary<string, List<CadDocument>>(StringComparer.OrdinalIgnoreCase);
        public readonly HashSet<string> Unsaved = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        public readonly List<string> Log = new List<string>();
        public CadDocument Active { get; set; }

        public string ProgramName => "FakeCAD 1.0";
        public CadTheme Theme => CadTheme.Light;
        public CadDocument ActiveDocument => Active;

        public CadDocument Add(string path, CadDocumentKind kind, string content = "content", Dictionary<string, string> props = null)
        {
            Directory.CreateDirectory(Path.GetDirectoryName(path));
            File.WriteAllText(path, content);
            Props[path] = props ?? new Dictionary<string, string>();
            return new CadDocument(path, Path.GetFileName(path), kind, null);
        }

        public IDictionary<string, string> ReadProperties(CadDocument doc) =>
            Props.TryGetValue(doc.Path, out var p) ? new Dictionary<string, string>(p) : new Dictionary<string, string>();

        public void WriteProperty(CadDocument doc, string name, string value)
        {
            Props[doc.Path][name] = value;
            Unsaved.Add(doc.Path);
        }

        public string ActiveConfiguration(CadDocument doc) => "Default";
        public string ReadMaterial(CadDocument doc) => Props[doc.Path].TryGetValue("Material", out var m) ? m : "";
        public CadPhysical ReadPhysical(CadDocument doc) =>
            new CadPhysical { MassKg = 1.5, VolumeMm3 = 190000, AreaMm2 = 22000, BoxMm = new double[] { 220, 120, 80 } };

        public IList<CadComponent> ReadStructure(CadDocument doc) =>
            (References.TryGetValue(doc.Path, out var refs) ? refs : new List<CadDocument>())
            .Select(r => new CadComponent { Path = r.Path, Properties = Props[r.Path], Quantity = 2 }).ToList();

        public IList<CadDocument> ReferencedDocumentsDeepestFirst(CadDocument doc) =>
            References.TryGetValue(doc.Path, out var refs) ? refs : new List<CadDocument>();

        public bool HasUnsavedChanges(CadDocument doc) => Unsaved.Contains(doc.Path);

        public bool Save(CadDocument doc)
        {
            Log.Add("save " + doc.FileName);
            Unsaved.Remove(doc.Path);
            return true;
        }

        public byte[] ExportPreviewPng(CadDocument doc) => new byte[] { 0x89, 0x50, 0x4E, 0x47 };
        public byte[] ExportMeshStl(CadDocument doc) => Encoding.ASCII.GetBytes("solid x\nendsolid x\n");
        public byte[] ReadFileBytes(CadDocument doc) => File.ReadAllBytes(doc.Path);

        public void Close(CadDocument doc) => Log.Add("close " + doc.FileName);

        public CadDocument Open(string path, CadDocumentKind kind)
        {
            Log.Add("open " + Path.GetFileName(path));
            Active = new CadDocument(path, Path.GetFileName(path), kind, null);
            return Active;
        }
    }

    public sealed class YesPrompts : IUserPrompts
    {
        public readonly List<string> Asked = new List<string>();
        public bool Answer { get; set; } = true;
        public bool Confirm(string title, string message) { Asked.Add(title); return Answer; }
    }

    /// <summary>The add-in endpoints of the Kosha server, in memory, following the server's real replies.</summary>
    public sealed class FakeKoshaServer : HttpMessageHandler
    {
        public sealed class Doc
        {
            public int Id; public string Folder; public string FileName; public string Revision = "A"; public int Version = 1;
            public int? CheckedOutBy; public byte[] Content; public string State = "in_work"; public JObject Props; public JToken Structure;
            public string Source;
        }

        public readonly List<Doc> Docs = new List<Doc>();
        public readonly List<string> Calls = new List<string>();
        public int UserId = 7;
        public bool Offline;
        int nextNumber = 1;

        public Doc Find(string folder, string name) =>
            Docs.FirstOrDefault(d => string.Equals(d.Folder, folder, StringComparison.OrdinalIgnoreCase) &&
                                     string.Equals(d.FileName, name, StringComparison.OrdinalIgnoreCase));

        static string Sha(byte[] b) => KoshaApiClient.Sha256(b);

        JObject Summary(Doc d) => JObject.FromObject(new
        {
            exists = true, id = d.Id, filename = d.FileName, revision = d.Revision, version = d.Version, state = d.State,
            state_label = d.State == "released" ? "Released" : "In work", checked_out_by = d.CheckedOutBy,
            checked_out_name = d.CheckedOutBy == null ? null : d.CheckedOutBy == UserId ? "Asha Menon" : "Daniel Okafor",
            latest_sha = Sha(d.Content),
            actions = new Dictionary<string, object>
            {
                ["checkout"] = d.CheckedOutBy == null ? (object)new { ok = true } : new { ok = false, why = "Checked out by someone else." },
            },
        });

        static HttpResponseMessage Json(object o, HttpStatusCode code = HttpStatusCode.OK) =>
            new HttpResponseMessage(code) { Content = new StringContent(JsonConvert.SerializeObject(o), Encoding.UTF8, "application/json") };

        protected override async Task<HttpResponseMessage> SendAsync(HttpRequestMessage req, CancellationToken ct)
        {
            if (Offline) throw new HttpRequestException("No route to host");
            string path = req.RequestUri.AbsolutePath;
            Calls.Add(req.Method + " " + path);
            if (path == "/api/auth/login")
            {
                var b = JObject.Parse(await req.Content.ReadAsStringAsync());
                if ((string)b["password"] != "right") return Json(new { error = "Wrong user name or password.", code = "bad_login" }, HttpStatusCode.BadRequest);
                return Json(new { token = "tok-123", user = new { id = UserId, username = (string)b["username"], full_name = "Asha Menon", role = "engineer" } });
            }
            if (req.Headers.Authorization?.Parameter != "tok-123") return Json(new { error = "Please sign in.", code = "unauthorized" }, HttpStatusCode.Unauthorized);
            var q = System.Web.HttpUtility.ParseQueryString(req.RequestUri.Query);
            switch (path)
            {
                case "/api/auth/me":
                    return Json(new { user = new { id = UserId, username = "asha", full_name = "Asha Menon", role = "engineer" } });
                case "/api/addin/status":
                    var d = Find(q["folder"], q["filename"]);
                    return d == null ? Json(new { exists = false }) : Json(Summary(d));
                case "/api/addin/checkout":
                {
                    var b = JObject.Parse(await req.Content.ReadAsStringAsync());
                    var doc = Find((string)b["folder"], (string)b["filename"]);
                    if (doc == null) return Json(new { error = "This file is not in Kosha yet.", code = "not_found" }, HttpStatusCode.NotFound);
                    if (doc.CheckedOutBy != null) return Json(new { error = "Already checked out.", code = "conflict" }, HttpStatusCode.Conflict);
                    doc.CheckedOutBy = UserId;
                    return Json(Summary(doc));
                }
                case "/api/addin/checkin":
                {
                    var form = await ReadForm(req);
                    var folder = form["folder"].Text;
                    var name = form["file"].FileName;
                    var doc = Find(folder, name);
                    if (doc != null && doc.CheckedOutBy != UserId)
                        return Json(new { error = "Check the file out first.", code = "conflict" }, HttpStatusCode.Conflict);
                    if (doc == null) { doc = new Doc { Id = Docs.Count + 1, Folder = folder, FileName = name }; Docs.Add(doc); }
                    else doc.Version++;
                    doc.Content = form["file"].Bytes;
                    doc.Props = JObject.Parse(form["props"].Text);
                    doc.Source = form["source"].Text;
                    doc.Structure = form.ContainsKey("structure") ? JToken.Parse(form["structure"].Text) : null;
                    doc.CheckedOutBy = form["keep_checked_out"].Text == "1" ? (int?)UserId : null;
                    var s = Summary(doc);
                    if (doc.Structure != null) s["bom"] = JObject.FromObject(new { id = 1 });
                    return Json(s);
                }
                case "/api/numbering/next":
                    return Json(new { part_number = "MX-100-" + (nextNumber++).ToString("000") });
                case "/api/addin/similar":
                    return Json(new { similar = new[] { new { id = 1, part_number = "STD-BH-2208", description = "BEARING HOUSING", score = 0.93 } } });
            }
            if (path.StartsWith("/api/documents/") && path.EndsWith("/undo-checkout"))
            {
                var doc = Docs.First(x => x.Id == int.Parse(path.Split('/')[3]));
                doc.CheckedOutBy = null;
                return Json(Summary(doc));
            }
            if (path.StartsWith("/api/documents/") && path.EndsWith("/file"))
            {
                var doc = Docs.First(x => x.Id == int.Parse(path.Split('/')[3]));
                return new HttpResponseMessage(HttpStatusCode.OK) { Content = new ByteArrayContent(doc.Content) };
            }
            return Json(new { error = "Not found", code = "not_found" }, HttpStatusCode.NotFound);
        }

        public sealed class Part { public string Text; public byte[] Bytes; public string FileName; }

        static async Task<Dictionary<string, Part>> ReadForm(HttpRequestMessage req)
        {
            var parts = new Dictionary<string, Part>();
            var multipart = (MultipartFormDataContent)req.Content;
            foreach (var c in multipart)
            {
                var cd = c.Headers.ContentDisposition;
                string name = cd.Name.Trim('"');
                var bytes = await c.ReadAsByteArrayAsync();
                parts[name] = new Part { Bytes = bytes, Text = Encoding.UTF8.GetString(bytes), FileName = cd.FileName?.Trim('"') };
            }
            return parts;
        }
    }
}
