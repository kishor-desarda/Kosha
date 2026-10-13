// SPDX-License-Identifier: Apache-2.0
using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Net.Http;
using System.Net.Http.Headers;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace Kosha.Client
{
    /// <summary>
    /// Client for the Kosha REST API. Every call is asynchronous and has a time-out, so a CAD program's
    /// window never freezes while Kosha is busy. Library code here never touches a CAD API, so it uses
    /// ConfigureAwait(false); callers continue on their own thread.
    /// </summary>
    public sealed class KoshaApiClient : IDisposable
    {
        readonly KoshaSettings settings;
        readonly HttpClient http;

        public KoshaApiClient(KoshaSettings settings, HttpMessageHandler handler = null, TimeSpan? timeout = null)
        {
            this.settings = settings ?? throw new ArgumentNullException(nameof(settings));
            http = handler == null ? new HttpClient() : new HttpClient(handler, disposeHandler: false);
            http.Timeout = timeout ?? TimeSpan.FromMinutes(5);   // large files take time; small calls are fast
        }

        public void Dispose() => http.Dispose();

        string Url(string path) => settings.ServerUrl.TrimEnd('/') + path;

        async Task<string> SendAsync(HttpRequestMessage req, CancellationToken ct)
        {
            if (!string.IsNullOrEmpty(settings.Token))
                req.Headers.Authorization = new AuthenticationHeaderValue("Bearer", settings.Token);
            HttpResponseMessage resp;
            try
            {
                resp = await http.SendAsync(req, ct).ConfigureAwait(false);
            }
            catch (TaskCanceledException) when (!ct.IsCancellationRequested)
            {
                throw new KoshaException("Kosha did not answer in time. Nothing was changed; try again.", 0, "timeout");
            }
            catch (HttpRequestException e)
            {
                throw new KoshaException("Cannot reach the Kosha server at " + settings.ServerUrl + ". Check the address and your network. (" +
                                         (e.InnerException?.Message ?? e.Message) + ")", 0, "network");
            }
            using (resp)
            {
                string body = resp.Content == null ? "" : await resp.Content.ReadAsStringAsync().ConfigureAwait(false);
                if (resp.IsSuccessStatusCode) return body;
                string message = resp.ReasonPhrase, code = "";
                try
                {
                    var j = JObject.Parse(body);
                    message = (string)j["error"] ?? message;
                    code = (string)j["code"] ?? "";
                }
                catch (JsonException)
                {
                    // not JSON: keep the HTTP reason
                }
                throw new KoshaException(message, (int)resp.StatusCode, code);
            }
        }

        async Task<T> GetAsync<T>(string path, CancellationToken ct)
        {
            var body = await SendAsync(new HttpRequestMessage(HttpMethod.Get, Url(path)), ct).ConfigureAwait(false);
            return JsonConvert.DeserializeObject<T>(body);
        }

        async Task<T> PostAsync<T>(string path, object payload, CancellationToken ct)
        {
            var req = new HttpRequestMessage(HttpMethod.Post, Url(path))
            {
                Content = new StringContent(JsonConvert.SerializeObject(payload ?? new object()), Encoding.UTF8, "application/json"),
            };
            var body = await SendAsync(req, ct).ConfigureAwait(false);
            return JsonConvert.DeserializeObject<T>(body);
        }

        public async Task<UserInfo> SignInAsync(string username, string password, CancellationToken ct = default)
        {
            settings.Token = "";
            var r = await PostAsync<JObject>("/api/auth/login", new { username, password }, ct).ConfigureAwait(false);
            settings.Token = (string)r["token"];
            settings.Username = username;
            return r["user"]?.ToObject<UserInfo>();
        }

        public async Task<UserInfo> MeAsync(CancellationToken ct = default) =>
            (await GetAsync<JObject>("/api/auth/me", ct).ConfigureAwait(false))["user"]?.ToObject<UserInfo>();

        public Task<DocStatus> StatusAsync(string folder, string fileName, CancellationToken ct = default) =>
            GetAsync<DocStatus>("/api/addin/status?folder=" + Uri.EscapeDataString(folder) + "&filename=" + Uri.EscapeDataString(fileName), ct);

        public Task<DocStatus> CheckOutAsync(string folder, string fileName, CancellationToken ct = default) =>
            PostAsync<DocStatus>("/api/addin/checkout", new { folder, filename = fileName }, ct);

        public Task<DocStatus> UndoCheckOutAsync(int documentId, CancellationToken ct = default) =>
            PostAsync<DocStatus>("/api/documents/" + documentId + "/undo-checkout", new { }, ct);

        /// <summary>Every Kosha folder, flat; build paths with the parent ids.</summary>
        public async Task<List<FolderInfo>> FoldersAsync(CancellationToken ct = default) =>
            (await GetAsync<JObject>("/api/folders", ct).ConfigureAwait(false))["folders"]?.ToObject<List<FolderInfo>>()
            ?? new List<FolderInfo>();

        /// <summary>The files checked out to the signed-in user, newest first.</summary>
        public async Task<List<DocStatus>> CheckedOutToMeAsync(CancellationToken ct = default) =>
            (await GetAsync<JObject>("/api/documents?checked_out=me", ct).ConfigureAwait(false))["documents"]?.ToObject<List<DocStatus>>()
            ?? new List<DocStatus>();

        public async Task<string> NextPartNumberAsync(string prefix, CancellationToken ct = default) =>
            (string)(await PostAsync<JObject>("/api/numbering/next", new { prefix }, ct).ConfigureAwait(false))["part_number"];

        public async Task<List<SimilarPart>> SimilarAsync(double x, double y, double z, double volume, double area, CancellationToken ct = default)
        {
            var ci = CultureInfo.InvariantCulture;
            var r = await GetAsync<JObject>(string.Format(ci, "/api/addin/similar?x={0}&y={1}&z={2}&volume={3}&area={4}", x, y, z, volume, area), ct)
                .ConfigureAwait(false);
            return r["similar"]?.ToObject<List<SimilarPart>>() ?? new List<SimilarPart>();
        }

        /// <summary>Downloads the latest version and checks it against its fingerprint before returning it.</summary>
        public async Task<byte[]> DownloadAsync(int documentId, string expectedSha256, CancellationToken ct = default)
        {
            var req = new HttpRequestMessage(HttpMethod.Get, Url("/api/documents/" + documentId + "/file"));
            if (!string.IsNullOrEmpty(settings.Token)) req.Headers.Authorization = new AuthenticationHeaderValue("Bearer", settings.Token);
            HttpResponseMessage resp;
            try { resp = await http.SendAsync(req, ct).ConfigureAwait(false); }
            catch (HttpRequestException e) { throw new KoshaException("Download failed: " + e.Message, 0, "network"); }
            using (resp)
            {
                if (!resp.IsSuccessStatusCode) throw new KoshaException("Download failed: " + resp.ReasonPhrase, (int)resp.StatusCode, "download");
                var bytes = await resp.Content.ReadAsByteArrayAsync().ConfigureAwait(false);
                if (!string.IsNullOrEmpty(expectedSha256) && !string.Equals(Sha256(bytes), expectedSha256, StringComparison.OrdinalIgnoreCase))
                    throw new KoshaException("The downloaded file was damaged on the way. Nothing was replaced; try again.", 0, "checksum");
                return bytes;
            }
        }

        public async Task<DocStatus> CheckInAsync(CheckinRequest r, CancellationToken ct = default)
        {
            var form = new MultipartFormDataContent();
            var fc = new ByteArrayContent(r.File);
            fc.Headers.ContentType = new MediaTypeHeaderValue("application/octet-stream");
            form.Add(fc, "file", r.FileName);
            form.Add(new StringContent(r.Folder, Encoding.UTF8), "folder");
            form.Add(new StringContent(r.Comment ?? "", Encoding.UTF8), "comment");
            form.Add(new StringContent(r.KeepCheckedOut ? "1" : "0"), "keep_checked_out");
            form.Add(new StringContent(r.Source ?? "cad-addin", Encoding.UTF8), "source");
            form.Add(new StringContent(JsonConvert.SerializeObject(r.Properties ?? new Dictionary<string, string>()), Encoding.UTF8), "props");
            if (!string.IsNullOrEmpty(r.Configuration)) form.Add(new StringContent(r.Configuration, Encoding.UTF8), "configuration");
            if (r.Structure != null) form.Add(new StringContent(JsonConvert.SerializeObject(r.Structure), Encoding.UTF8), "structure");
            if (r.PreviewPng != null && r.PreviewPng.Length > 0)
            {
                var pc = new ByteArrayContent(r.PreviewPng);
                pc.Headers.ContentType = new MediaTypeHeaderValue("image/png");
                form.Add(pc, "preview", "preview.png");
            }
            if (r.MeshStl != null && r.MeshStl.Length > 0)
            {
                var mc = new ByteArrayContent(r.MeshStl);
                mc.Headers.ContentType = new MediaTypeHeaderValue("application/octet-stream");
                form.Add(mc, "mesh", "mesh.stl");
            }
            var req = new HttpRequestMessage(HttpMethod.Post, Url("/api/addin/checkin")) { Content = form };
            return JsonConvert.DeserializeObject<DocStatus>(await SendAsync(req, ct).ConfigureAwait(false));
        }

        public static string Sha256(byte[] data)
        {
            using (var sha = SHA256.Create()) return Hex(sha.ComputeHash(data));
        }

        /// <summary>Fingerprint of a local file, readable even while a CAD program has it open.</summary>
        public static string Sha256File(string path)
        {
            using (var fs = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete))
            using (var sha = SHA256.Create()) return Hex(sha.ComputeHash(fs));
        }

        static string Hex(byte[] hash)
        {
            var sb = new StringBuilder(hash.Length * 2);
            foreach (var b in hash) sb.Append(b.ToString("x2", CultureInfo.InvariantCulture));
            return sb.ToString();
        }
    }
}
