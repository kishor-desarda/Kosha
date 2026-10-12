// SPDX-License-Identifier: Apache-2.0
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Threading.Tasks;
using Kosha.Client;
using NUnit.Framework;

namespace Kosha.CadCore.Tests
{
    public class CadSessionTests
    {
        string root;
        FakeCadHost host;
        FakeKoshaServer server;
        KoshaSettings settings;
        YesPrompts prompts;
        CadSession session;

        [SetUp]
        public async Task SetUp()
        {
            root = Path.Combine(Path.GetTempPath(), "kosha-cad-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(root);
            host = new FakeCadHost();
            server = new FakeKoshaServer();
            settings = KoshaSettings.Load(Path.Combine(root, "settings.json"), new MemoryOnlyTokenProtector());
            settings.ServerUrl = "http://kosha.test";
            settings.WorkspaceRoot = Path.Combine(root, "ws");
            prompts = new YesPrompts();
            session = new CadSession(host, new KoshaApiClient(settings, server), settings, prompts) { KeepFolder = Path.Combine(root, "kept") };
            await session.SignInAsync("asha", "right");
        }

        [TearDown]
        public void TearDown()
        {
            foreach (var f in Directory.GetFiles(root, "*", SearchOption.AllDirectories)) File.SetAttributes(f, FileAttributes.Normal);
            Directory.Delete(root, true);
        }

        string Ws(params string[] parts) => Path.Combine(new[] { settings.WorkspaceRoot }.Concat(parts).ToArray());

        [Test]
        public async Task NewPart_CanBeCheckedIn_WithPropertiesAndSimilarWarning()
        {
            var doc = host.Add(Ws("Products", "Mixers", "MX-100-020 Agitator Shaft.sldprt"), CadDocumentKind.Part,
                props: new Dictionary<string, string> { ["Material"] = "42CrMo4", ["Finish"] = "Painted" });
            var st = await session.DescribeAsync(doc);
            Assert.That(st.Folder, Is.EqualTo("Kosha/Products/Mixers"));
            Assert.That(st.PartNumber, Is.EqualTo("MX-100-020"));
            Assert.That(st.Description, Is.EqualTo("Agitator Shaft"));
            Assert.That(st.IsInKosha, Is.False);
            Assert.That(st.CanCheckIn, Is.True);
            Assert.That(st.Warning, Does.Contain("STD-BH-2208"));

            var outcome = await session.CheckInAsync(st, "", "", "First issue", keepCheckedOut: false, withComponents: true);
            var saved = server.Find("Kosha/Products/Mixers", "MX-100-020 Agitator Shaft.sldprt");
            Assert.That(saved, Is.Not.Null);
            Assert.That((string)saved.Props["part_number"], Is.EqualTo("MX-100-020"));
            Assert.That((string)saved.Props["material"], Is.EqualTo("42CrMo4"));
            Assert.That((string)saved.Props["finish"], Is.EqualTo("Painted"));
            Assert.That((string)saved.Props["weight"], Is.EqualTo("1.5"));
            Assert.That(saved.Source, Is.EqualTo("FakeCAD 1.0"));
            Assert.That(LocalFiles.IsReadOnly(doc.Path), Is.True, "checked-in files are read-only until checked out");
            Assert.That(outcome.Summary(), Does.Contain("version 1 of revision A"));
        }

        [Test]
        public async Task CheckOut_Edit_CheckIn_RaisesVersion()
        {
            var doc = host.Add(Ws("Products", "MX-100-030 Housing.sldprt"), CadDocumentKind.Part);
            await session.CheckInAsync(await session.DescribeAsync(doc), "", "", "", false, false);

            var st = await session.DescribeAsync(doc);
            Assert.That(st.CanCheckOut && !st.CanCheckIn, Is.True);
            await session.CheckOutAsync(st);
            Assert.That(LocalFiles.IsReadOnly(doc.Path), Is.False);

            File.WriteAllText(doc.Path, "edited");
            st = await session.DescribeAsync(doc);
            Assert.That(st.CheckedOutToMe && st.CanCheckIn && st.CanUndo, Is.True);
            var outcome = await session.CheckInAsync(st, "mx-100-030", "Bearing housing", "Wider base", false, false);
            Assert.That(outcome.Result.Version, Is.EqualTo(2));
            Assert.That(host.Props[doc.Path]["PartNo"], Is.EqualTo("MX-100-030"), "part number written back upper-case");
            Assert.That(host.Log, Does.Contain("save MX-100-030 Housing.sldprt"), "unsaved changes are saved before check-in");
        }

        [Test]
        public async Task Assembly_ChecksInNewComponentsFirst_AndSendsStructure()
        {
            var a = host.Add(Ws("Products", "MX-100-010 Disc.sldprt"), CadDocumentKind.Part);
            var b = host.Add(Ws("Products", "MX-100-011 Hub.sldprt"), CadDocumentKind.Part);
            var outside = host.Add(Path.Combine(root, "elsewhere", "Bolt.sldprt"), CadDocumentKind.Part);
            var asm = host.Add(Ws("Products", "MX-100-000 Agitator.sldasm"), CadDocumentKind.Assembly);
            host.References[asm.Path] = new List<CadDocument> { a, b, outside };

            var outcome = await session.CheckInAsync(await session.DescribeAsync(asm), "", "", "", false, withComponents: true);
            Assert.That(outcome.AlsoCheckedIn, Is.EquivalentTo(new[] { "MX-100-010 Disc.sldprt", "MX-100-011 Hub.sldprt" }));
            Assert.That(outcome.Skipped.Single(), Does.Contain("Bolt.sldprt"));
            var order = server.Docs.Select(d => d.FileName).ToList();
            Assert.That(order.Last(), Is.EqualTo("MX-100-000 Agitator.sldasm"), "components before the assembly");
            var structure = server.Find("Kosha/Products", "MX-100-000 Agitator.sldasm").Structure;
            Assert.That(structure.Count(), Is.EqualTo(3));
            Assert.That((int)structure[0]["qty"], Is.EqualTo(2));
            Assert.That(outcome.Summary(), Does.Contain("engineering BOM"));

            // second time: components already in Kosha and not checked out are left alone
            var asmState = await session.DescribeAsync(asm);
            await session.CheckOutAsync(asmState);
            var again = await session.CheckInAsync(await session.DescribeAsync(asm), "", "", "", false, true);
            Assert.That(again.AlsoCheckedIn, Is.Empty);
        }

        [Test]
        public async Task GetLatest_KeepsTheOldLocalFile_AndVerifiesTheDownload()
        {
            var doc = host.Add(Ws("Products", "MX-200-001 Flange.dwg"), CadDocumentKind.Drawing, "version one");
            await session.CheckInAsync(await session.DescribeAsync(doc), "", "", "", false, false);
            server.Docs[0].Content = System.Text.Encoding.UTF8.GetBytes("version two");   // someone else checked in

            File.SetAttributes(doc.Path, FileAttributes.Normal);
            File.WriteAllText(doc.Path, "my local experiment");
            var st = await session.DescribeAsync(doc);
            Assert.That(st.Warning, Does.Contain("different from the latest version"));
            string kept = await session.GetLatestAsync(st);

            Assert.That(File.ReadAllText(doc.Path), Is.EqualTo("version two"));
            Assert.That(File.ReadAllText(kept), Is.EqualTo("my local experiment"), "nothing is lost");
            Assert.That(host.Log, Is.EqualTo(new[] { "close MX-200-001 Flange.dwg", "open MX-200-001 Flange.dwg" }));
        }

        [Test]
        public async Task Declining_GetLatest_ChangesNothing()
        {
            var doc = host.Add(Ws("Products", "MX-200-002 Plate.dwg"), CadDocumentKind.Drawing, "mine");
            await session.CheckInAsync(await session.DescribeAsync(doc), "", "", "", false, false);
            prompts.Answer = false;
            Assert.That(await session.GetLatestAsync(await session.DescribeAsync(doc)), Is.EqualTo(""));
            Assert.That(File.ReadAllText(doc.Path), Is.EqualTo("mine"));
        }

        [Test]
        public async Task FilesOutsideTheWorkspace_AreExplained()
        {
            var doc = host.Add(Path.Combine(root, "Desktop", "X.sldprt"), CadDocumentKind.Part);
            var st = await session.DescribeAsync(doc);
            Assert.That(st.Message, Does.Contain("outside your Kosha workspace"));
            Assert.That(st.CanCheckIn || st.CanCheckOut, Is.False);
            Assert.That((await session.DescribeAsync(new CadDocument("", "Part1", CadDocumentKind.Part, null))).Message, Does.Contain("Save the document"));
            Assert.That((await session.DescribeAsync(null)).Message, Does.Contain("Open a part"));
        }

        [Test]
        public async Task SomeoneElsesCheckOut_IsShownWithTheReason()
        {
            var doc = host.Add(Ws("Products", "MX-300-001 Belt.sldprt"), CadDocumentKind.Part);
            await session.CheckInAsync(await session.DescribeAsync(doc), "", "", "", false, false);
            server.Docs[0].CheckedOutBy = 99;
            var st = await session.DescribeAsync(doc);
            Assert.That(st.Message, Does.Contain("Checked out by Daniel Okafor"));
            Assert.That(st.CanCheckOut || st.CanCheckIn, Is.False);
            Assert.That(st.Warning, Is.EqualTo("Checked out by someone else."));
        }

        [Test]
        public void ServerErrors_BecomePlainMessages()
        {
            var bad = new CadSession(host, new KoshaApiClient(settings, server), settings, prompts);
            var ex = Assert.ThrowsAsync<KoshaException>(() => bad.SignInAsync("asha", "wrong"));
            Assert.That(ex.Message, Is.EqualTo("Wrong user name or password."));
            server.Offline = true;
            ex = Assert.ThrowsAsync<KoshaException>(() => session.DescribeAsync(host.Add(Ws("P", "A-1 X.sldprt"), CadDocumentKind.Part)));
            Assert.That(ex.IsNetwork && ex.Message.Contains("Cannot reach the Kosha server"), Is.True);
        }

        [Test]
        public async Task NextNumber_IsWrittenToTheDocument()
        {
            var doc = host.Add(Ws("Products", "New part.sldprt"), CadDocumentKind.Part);
            Assert.That(await session.NextNumberAsync(doc, "MX-100-"), Is.EqualTo("MX-100-001"));
            Assert.That(host.Props[doc.Path]["PartNo"], Is.EqualTo("MX-100-001"));
            Assert.That(settings.PartNumberPrefix, Is.EqualTo("MX-100-"));
        }
    }

    public class SettingsAndRulesTests
    {
        [Test]
        public void WorkspaceMapping()
        {
            var ws = Path.Combine(Path.GetTempPath(), "kosha-ws-" + Guid.NewGuid().ToString("N"));
            var s = new KoshaSettings { WorkspaceRoot = ws };
            Assert.That(s.KoshaFolderFor(Path.Combine(ws, "Products", "Pumps", "a.dwg")), Is.EqualTo("Kosha/Products/Pumps"));
            Assert.That(s.KoshaFolderFor(Path.Combine(ws, "a.dwg")), Is.Null, "the workspace root itself is not a Kosha folder");
            Assert.That(s.KoshaFolderFor(Path.Combine(Path.GetTempPath(), "other", "a.dwg")), Is.Null);
            Assert.That(s.KoshaFolderFor(""), Is.Null);
        }

        [Test]
        public void SettingsSurviveARoundTrip_AndADamagedFile()
        {
            var dir = Path.Combine(Path.GetTempPath(), "kosha-set-" + Guid.NewGuid().ToString("N"));
            var file = Path.Combine(dir, "settings.json");
            var p = new MemoryOnlyTokenProtector();
            var s = KoshaSettings.Load(file, p);
            s.ServerUrl = "https://plm.example.com";
            s.Token = "secret-token";
            s.Save(p);
            Assert.That(File.ReadAllText(file), Does.Not.Contain("secret-token"), "the token is never written in plain text");
            Assert.That(KoshaSettings.Load(file, p).ServerUrl, Is.EqualTo("https://plm.example.com"));
            File.WriteAllText(file, "{ not json");
            Assert.That(KoshaSettings.Load(file, p).ServerUrl, Is.EqualTo("http://kosha-server:8080"));
            Directory.Delete(dir, true);
        }

        [TestCase("PartNo", "mx-1-2", "x.sldprt", ExpectedResult = "MX-1-2")]
        [TestCase("PART_NO", "SC-300-021", "x.dwg", ExpectedResult = "SC-300-021")]
        [TestCase(null, null, "CP-80-014 Inlet Flange.dwg", ExpectedResult = "CP-80-014")]
        [TestCase(null, null, "notes.dwg", ExpectedResult = "")]
        public string PartNumberRules(string key, string value, string file)
        {
            var props = new Dictionary<string, string>();
            if (key != null) props[key] = value;
            return PartNumbers.PartNumberOf(props, file);
        }

        [Test]
        public void ReplaceKeepingOld_NeverLosesTheOldFile()
        {
            var dir = Path.Combine(Path.GetTempPath(), "kosha-rep-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(dir);
            var f = Path.Combine(dir, "a.dwg");
            File.WriteAllText(f, "old");
            LocalFiles.SetReadOnly(f, true);
            var kept = LocalFiles.ReplaceKeepingOld(f, System.Text.Encoding.UTF8.GetBytes("new"), Path.Combine(dir, "kept"));
            Assert.That(File.ReadAllText(f), Is.EqualTo("new"));
            Assert.That(File.ReadAllText(kept), Is.EqualTo("old"));
            Assert.That(File.Exists(f + ".kosha-new"), Is.False);
            Directory.Delete(dir, true);
        }
    }
}
