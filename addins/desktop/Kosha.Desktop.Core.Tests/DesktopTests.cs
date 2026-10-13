// SPDX-License-Identifier: Apache-2.0
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using System.Threading.Tasks;
using Kosha.CadCore;
using Kosha.CadCore.Tests;
using Kosha.Client;
using NUnit.Framework;

namespace Kosha.Desktop.Core.Tests
{
    /// <summary>Kosha Desktop's verbs against the in-memory Kosha server, with real files in a temporary workspace.</summary>
    public class DesktopSessionTests
    {
        string root;
        FakeKoshaServer server;
        KoshaSettings settings;
        YesPrompts prompts;
        DesktopSession session;
        ActivityLog log;
        DesktopRunner runner;

        [SetUp]
        public async Task SetUp()
        {
            root = Path.Combine(Path.GetTempPath(), "kosha-desk-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(root);
            server = new FakeKoshaServer();
            settings = KoshaSettings.Load(Path.Combine(root, "settings.json"), new MemoryOnlyTokenProtector());
            settings.ServerUrl = "http://kosha.test";
            settings.WorkspaceRoot = Path.Combine(root, "ws");
            prompts = new YesPrompts();
            session = new DesktopSession(new KoshaApiClient(settings, server), settings, prompts) { KeepFolder = Path.Combine(root, "kept") };
            log = new ActivityLog(Path.Combine(root, "activity.json"));
            runner = new DesktopRunner(session, log);
            await session.SignInAsync("asha", "right");
        }

        [TearDown]
        public void TearDown()
        {
            foreach (var f in Directory.GetFiles(root, "*", SearchOption.AllDirectories)) File.SetAttributes(f, FileAttributes.Normal);
            Directory.Delete(root, true);
        }

        string Ws(params string[] parts) => Path.Combine(new[] { settings.WorkspaceRoot }.Concat(parts).ToArray());

        string NewFile(string content, params string[] parts)
        {
            string path = Ws(parts);
            Directory.CreateDirectory(Path.GetDirectoryName(path));
            File.WriteAllText(path, content);
            return path;
        }

        [Test]
        public async Task NewDocument_IsCheckedIn_WithoutCadData()
        {
            var path = NewFile("test figures", "Products", "Mixers", "MX-100 Test report.docx");
            var st = await session.DescribeAsync(path);
            Assert.That(st.Folder, Is.EqualTo("Kosha/Products/Mixers"));
            Assert.That(st.IsInKosha, Is.False);
            Assert.That(st.CanCheckIn, Is.True);

            var outcome = await runner.RunAsync(DesktopVerb.CheckIn, path, "First issue");
            Assert.That(outcome.Ok, Is.True, outcome.Message);
            Assert.That(outcome.Message, Does.StartWith("Added MX-100 Test report.docx to Kosha/Products/Mixers as revision A, version 1."));
            var saved = server.Find("Kosha/Products/Mixers", "MX-100 Test report.docx");
            Assert.That(saved, Is.Not.Null);
            Assert.That(saved.Source, Is.EqualTo("Kosha Desktop"));
            Assert.That(Encoding.UTF8.GetString(saved.Content), Is.EqualTo("test figures"));
            Assert.That(saved.Props.Count, Is.EqualTo(0), "no CAD properties are sent");
            Assert.That(saved.Structure, Is.Null);
            Assert.That(LocalFiles.IsReadOnly(path), Is.True, "checked-in files are read-only until checked out");
            Assert.That(outcome.Url, Is.EqualTo("http://kosha.test/#/doc/1"));
            var entry = log.Read().Single();
            Assert.That(entry.Ok, Is.True);
            Assert.That(entry.Action, Is.EqualTo("Check in"));
            Assert.That(entry.Path, Is.EqualTo(path));
        }

        [Test]
        public async Task ExistingFile_IsCheckedOutFirst_AndAnOldCopyIsNeverEdited()
        {
            var path = NewFile("v1", "Products", "Spec.xlsx");
            await session.CheckInAsync(path, "", false);
            File.SetAttributes(path, FileAttributes.Normal);
            File.WriteAllText(path, "edited without checking out");

            var refused = await runner.RunAsync(DesktopVerb.CheckIn, path);
            Assert.That(refused.Ok, Is.False);
            Assert.That(refused.Message, Does.Contain("not checked out to you").And.Contain("Check it out first"));
            Assert.That(server.Docs[0].Version, Is.EqualTo(1), "nothing was uploaded");
            Assert.That(log.Read().First().Ok, Is.False, "the failure is listed");

            var co = await runner.RunAsync(DesktopVerb.CheckOut, path);
            Assert.That(co.Ok, Is.True, co.Message);
            Assert.That(prompts.Asked, Does.Contain("Check out"));
            Assert.That(co.Message, Does.Contain("replaced with the latest version"));
            Assert.That(File.ReadAllText(path), Is.EqualTo("v1"));
            var kept = Directory.GetFiles(Path.Combine(root, "kept")).Single();
            Assert.That(File.ReadAllText(kept), Is.EqualTo("edited without checking out"), "nothing is lost");
            Assert.That(LocalFiles.IsReadOnly(path), Is.False);
            Assert.That(server.Docs[0].CheckedOutBy, Is.EqualTo(server.UserId));

            File.WriteAllText(path, "v2");
            var ci = await runner.RunAsync(DesktopVerb.CheckIn, path, "Second issue");
            Assert.That(ci.Ok, Is.True, ci.Message);
            Assert.That(ci.Message, Does.Contain("version 2 of revision A"));
            Assert.That(server.Docs[0].CheckedOutBy, Is.Null);
            Assert.That(LocalFiles.IsReadOnly(path), Is.True);
        }

        [Test]
        public async Task CheckingInTheSameContent_MakesNoNewVersion()
        {
            var path = NewFile("same", "Products", "Notes.txt");
            await session.CheckInAsync(path, "", keepCheckedOut: true);
            var r = await runner.RunAsync(DesktopVerb.CheckIn, path);
            Assert.That(r.Ok, Is.True, r.Message);
            Assert.That(r.Message, Does.Contain("already had this exact content"));
            Assert.That(server.Docs[0].Version, Is.EqualTo(1));
        }

        [Test]
        public async Task GetLatest_KeepsTheOldCopy_AndSkipsTheDownloadWhenUpToDate()
        {
            var path = NewFile("version one", "Products", "Drawing list.pdf");
            await session.CheckInAsync(path, "", false);
            server.Docs[0].Content = Encoding.UTF8.GetBytes("version two");   // someone else checked in

            var r = await runner.RunAsync(DesktopVerb.GetLatest, path);
            Assert.That(r.Ok, Is.True, r.Message);
            Assert.That(File.ReadAllText(path), Is.EqualTo("version two"));
            Assert.That(LocalFiles.IsReadOnly(path), Is.True);
            var kept = Directory.GetFiles(Path.Combine(root, "kept")).Single();
            Assert.That(File.ReadAllText(kept), Is.EqualTo("version one"), "nothing is lost");
            Assert.That(r.Message, Does.Contain(kept));

            int downloads = server.Calls.Count(c => c.EndsWith("/file"));
            var again = await runner.RunAsync(DesktopVerb.GetLatest, path);
            Assert.That(again.Message, Does.Contain("already have the latest version"));
            Assert.That(server.Calls.Count(c => c.EndsWith("/file")), Is.EqualTo(downloads), "no download when the copy is current");
        }

        [Test]
        public async Task Declining_ChangesNothing_AndIsNotListed()
        {
            var path = NewFile("mine", "Products", "Plate.docx");
            await session.CheckInAsync(path, "", false);
            server.Docs[0].Content = Encoding.UTF8.GetBytes("theirs");
            prompts.Answer = false;
            var r = await runner.RunAsync(DesktopVerb.GetLatest, path);
            Assert.That(r.Cancelled, Is.True);
            Assert.That(File.ReadAllText(path), Is.EqualTo("mine"));
            Assert.That(log.Read(), Is.Empty);
        }

        [Test]
        public async Task FilesOutsideTheWorkspace_AreExplained_AndNeverSent()
        {
            var outside = Path.Combine(root, "Desktop", "Notes.docx");
            Directory.CreateDirectory(Path.GetDirectoryName(outside));
            File.WriteAllText(outside, "x");
            var r = await runner.RunAsync(DesktopVerb.CheckIn, outside);
            Assert.That(r.Ok, Is.False);
            Assert.That(r.Message, Does.Contain("not in a sub-folder of your Kosha workspace"));

            var top = NewFile("x", "Loose.docx");    // the workspace folder itself is not a Kosha folder
            Assert.That((await runner.RunAsync(DesktopVerb.CheckIn, top)).Message, Does.Contain("not in a sub-folder"));
            Assert.That((await runner.RunAsync(DesktopVerb.Show, top)).Message, Does.Contain("not in a sub-folder"));
            Assert.That(server.Calls.Any(c => c.Contains("/api/addin/checkin")), Is.False);

            var gone = await runner.RunAsync(DesktopVerb.CheckOut, Ws("Products", "Gone.docx"));
            Assert.That(gone.Message, Does.Contain("was not found"));
        }

        [Test]
        public async Task SomeoneElsesCheckOut_IsShownWithTheReason()
        {
            var path = NewFile("x", "Products", "Belt test.docx");
            await session.CheckInAsync(path, "", false);
            server.Docs[0].CheckedOutBy = 99;
            var st = await session.DescribeAsync(path);
            Assert.That(st.Message, Does.Contain("checked out by Daniel Okafor"));
            Assert.That(st.CanCheckOut || st.CanCheckIn, Is.False);
            var r = await runner.RunAsync(DesktopVerb.CheckOut, path);
            Assert.That(r.Ok, Is.False);
            Assert.That(r.Message, Does.Contain("cannot be checked out").And.Contain("Checked out by someone else."));
            var ci = await runner.RunAsync(DesktopVerb.CheckIn, path);
            Assert.That(ci.Message, Does.Contain("checked out by Daniel Okafor, so you cannot check it in"));
        }

        [Test]
        public async Task ShowInKosha_AndUndoCheckOut()
        {
            var path = NewFile("x", "Products", "Pump curve.png");
            Assert.That((await runner.RunAsync(DesktopVerb.Show, path)).Message, Does.Contain("not in Kosha yet"));
            await session.CheckInAsync(path, "", keepCheckedOut: true);
            var show = await runner.RunAsync(DesktopVerb.Show, path);
            Assert.That(show.Ok, Is.True, show.Message);
            Assert.That(show.Url, Is.EqualTo("http://kosha.test/#/doc/1"));

            prompts.Answer = false;
            Assert.That((await runner.RunAsync(DesktopVerb.UndoCheckOut, path)).Cancelled, Is.True);
            Assert.That(server.Docs[0].CheckedOutBy, Is.EqualTo(server.UserId));
            prompts.Answer = true;
            var undo = await runner.RunAsync(DesktopVerb.UndoCheckOut, path);
            Assert.That(undo.Ok, Is.True, undo.Message);
            Assert.That(server.Docs[0].CheckedOutBy, Is.Null);
            Assert.That(LocalFiles.IsReadOnly(path), Is.True);
        }

        [Test]
        public async Task SignInAndNetworkProblems_BecomePlainMessages()
        {
            var path = NewFile("x", "Products", "Sheet.xlsx");
            session.SignOut();
            var r = await runner.RunAsync(DesktopVerb.Show, path);
            Assert.That(r.NeedsSignIn, Is.True);
            Assert.That(log.Read(), Is.Empty, "nothing happened yet");

            await session.SignInAsync("asha", "right");
            server.Offline = true;
            r = await runner.RunAsync(DesktopVerb.CheckIn, path);
            Assert.That(r.Ok, Is.False);
            Assert.That(r.Message, Does.Contain("Cannot reach the Kosha server"));
            Assert.That(log.Read().Single().Ok, Is.False);
            Assert.That(LocalFiles.IsReadOnly(path), Is.False, "the file is untouched");
        }

        [Test]
        public async Task SyncStatus_ListsMyCheckedOutFiles_WithTheirLocalState()
        {
            var a = NewFile("a1", "Products", "Pumps", "Pump datasheet.pdf");
            var b = NewFile("b1", "Products", "Pumps", "Pump manual.docx");
            var c = NewFile("c1", "Products", "Conveyors", "Belt spec.docx");
            var d = NewFile("d1", "Products", "Conveyors", "Not mine.docx");
            foreach (var p in new[] { a, b, c }) await session.CheckInAsync(p, "", keepCheckedOut: true);
            await session.CheckInAsync(d, "", keepCheckedOut: false);
            File.WriteAllText(b, "b2 edited");
            File.Delete(c);

            var rows = (await session.CheckedOutToMeAsync()).ToDictionary(r => r.FileName);
            Assert.That(rows.Keys, Is.EquivalentTo(new[] { "Pump datasheet.pdf", "Pump manual.docx", "Belt spec.docx" }));
            Assert.That(rows["Pump datasheet.pdf"].Local, Is.EqualTo(LocalCopy.Same));
            Assert.That(rows["Pump datasheet.pdf"].KoshaFolder, Is.EqualTo("Kosha/Products/Pumps"));
            Assert.That(rows["Pump manual.docx"].Local, Is.EqualTo(LocalCopy.Changed));
            Assert.That(rows["Pump manual.docx"].LocalPath, Is.EqualTo(b));
            Assert.That(rows["Belt spec.docx"].Local, Is.EqualTo(LocalCopy.Missing));
            Assert.That(rows["Belt spec.docx"].Describe(), Does.Contain("Not on this PC"));

            prompts.Answer = true;
            Assert.That(await session.UndoCheckOutAsync(rows["Belt spec.docx"]), Is.True, "undo works without a local copy");
            Assert.That(server.Find("Kosha/Products/Conveyors", "Belt spec.docx").CheckedOutBy, Is.Null);
        }
    }

    public class DesktopRulesTests
    {
        [Test]
        public void CommandLine()
        {
            var c = DesktopCommand.Parse(new[] { "checkin", @"C:\Kosha\Products\Spec sheet.docx" });
            Assert.That(c.Verb, Is.EqualTo(DesktopVerb.CheckIn));
            Assert.That(c.Path, Is.EqualTo(@"C:\Kosha\Products\Spec sheet.docx"));
            Assert.That(DesktopCommand.Parse(new string[0]).Verb, Is.EqualTo(DesktopVerb.Tray));
            Assert.That(DesktopCommand.Parse(new[] { "LATEST", "x.pdf" }).Verb, Is.EqualTo(DesktopVerb.GetLatest));
            Assert.That(DesktopCommand.Parse(new[] { "--register" }).Verb, Is.EqualTo(DesktopVerb.Register));

            var bad = DesktopCommand.Parse(new[] { "delete", "x.pdf" });
            Assert.That(bad.Verb, Is.EqualTo(DesktopVerb.Invalid));
            Assert.That(bad.Error, Does.Contain("does not know"));
            Assert.That(DesktopCommand.Parse(new[] { "checkout" }).Error, Does.Contain("Say which file"));
            Assert.That(DesktopCommand.Parse(new[] { "show", "a.pdf", "b.pdf" }).Error, Does.Contain("one file at a time"));
            Assert.That(DesktopCommand.Parse(new[] { "register", "x" }).Verb, Is.EqualTo(DesktopVerb.Invalid));
        }

        [Test]
        public void ExplorerVerbs_ArePerUser_AndRunTheRightCommand()
        {
            const string exe = @"C:\Users\asha\AppData\Local\Programs\Kosha Desktop\Kosha.Desktop.exe";
            var entries = ExplorerVerbs.Entries(exe);
            Assert.That(entries.All(e => e.Key.StartsWith(@"Software\Classes\*\shell\Kosha", StringComparison.Ordinal)), Is.True);
            var labels = entries.Where(e => e.Name.Length == 0 && !e.Key.EndsWith(@"\command", StringComparison.Ordinal)).Select(e => e.Value);
            Assert.That(labels, Is.EqualTo(new[] { "Check in to Kosha", "Check out from Kosha", "Show in Kosha", "Get latest version from Kosha" }));
            var checkIn = entries.Single(e => e.Key == @"Software\Classes\*\shell\KoshaCheckIn\command");
            Assert.That(checkIn.Value, Is.EqualTo("\"" + exe + "\" checkin \"%1\""));
            Assert.That(entries.Single(e => e.Key == @"Software\Classes\*\shell\KoshaShow" && e.Name == "Icon").Value, Is.EqualTo(exe + ",0"));
            foreach (var v in ExplorerVerbs.Verbs)
            {
                string word = DesktopCommand.ArgumentFor(v.Verb);
                Assert.That(ExplorerVerbs.CommandFor(exe, v.Verb), Does.Contain(" " + word + " \"%1\""));
                Assert.That(DesktopCommand.Parse(new[] { word, "x.docx" }).Verb, Is.EqualTo(v.Verb), "Explorer's command parses back to the verb");
            }
            Assert.That(ExplorerVerbs.VerbKeys.Count(), Is.EqualTo(4));
            Assert.That(ExplorerVerbs.StartupEntry(exe).Value, Is.EqualTo("\"" + exe + "\" tray"));
            Assert.That(ExplorerVerbs.StartupEntry(exe).Key, Does.StartWith(@"Software\Microsoft\Windows\CurrentVersion\Run"));
        }

        [Test]
        public void ActivityLog_KeepsTheNewestEntries_AndSurvivesADamagedFile()
        {
            var dir = Path.Combine(Path.GetTempPath(), "kosha-log-" + Guid.NewGuid().ToString("N"));
            var file = Path.Combine(dir, "sub", "activity.json");
            var log = new ActivityLog(file, max: 3);
            Assert.That(log.Read(), Is.Empty);
            for (int i = 1; i <= 5; i++)
                Assert.That(log.Add(new ActivityEntry { Action = "Check in", Path = "f" + i, Ok = true, AtUtc = DateTime.UtcNow.AddSeconds(i) }), Is.True);
            Assert.That(log.Read().Select(e => e.Path), Is.EqualTo(new[] { "f5", "f4", "f3" }));
            Assert.That(File.Exists(file + ".lock"), Is.False, "the lock is released");

            File.WriteAllText(file, "{ damaged");
            Assert.That(log.Read(), Is.Empty);
            Assert.That(log.Add(new ActivityEntry { Path = "f6" }), Is.True);
            Assert.That(log.Read().Single().Path, Is.EqualTo("f6"));
            Directory.Delete(dir, true);
        }

        [Test]
        public void WorkspaceMap_IsTheReverseOfKoshaFolderFor()
        {
            var folders = new List<FolderInfo>
            {
                new FolderInfo { Id = 1, Name = "Kosha" },
                new FolderInfo { Id = 2, ParentId = 1, Name = "Products" },
                new FolderInfo { Id = 3, ParentId = 2, Name = "Pumps" },
                new FolderInfo { Id = 9, ParentId = 9, Name = "Loop" },
            };
            var paths = WorkspaceMap.FolderPaths(folders);
            Assert.That(paths[3], Is.EqualTo("Kosha/Products/Pumps"));
            Assert.That(paths[9], Is.EqualTo("Loop"), "a loop ends the path instead of hanging");

            var s = new KoshaSettings { WorkspaceRoot = Path.Combine(Path.GetTempPath(), "kosha-ws") };
            string local = WorkspaceMap.LocalPathFor(s, "Kosha/Products/Pumps", "P-100 Curve.pdf");
            Assert.That(local, Is.EqualTo(Path.Combine(s.WorkspaceRoot, "Products", "Pumps", "P-100 Curve.pdf")));
            Assert.That(s.KoshaFolderFor(local), Is.EqualTo("Kosha/Products/Pumps"));
            Assert.That(WorkspaceMap.LocalPathFor(s, "Kosha", "P.pdf"), Is.Null, "files in the top folder have no place in the workspace");
            Assert.That(WorkspaceMap.LocalPathFor(s, "Kosha/../Windows", "P.pdf"), Is.Null);
        }
    }
}
