// SPDX-License-Identifier: Apache-2.0
using System.Collections.Generic;
using NUnit.Framework;

namespace Kosha.CadCore.Tests
{
    /// <summary>The DWG rules shared by the AutoCAD and GStarCAD add-ins (their CAD-specific walk is not built in CI).</summary>
    public class TitleBlockTests
    {
        [TestCase("PART_NO", ExpectedResult = true)]
        [TestCase("part_no", ExpectedResult = true)]
        [TestCase(" DWG_NO ", ExpectedResult = true)]
        [TestCase("MATERIAL", ExpectedResult = true)]
        [TestCase("SCALE", ExpectedResult = false)]
        [TestCase("", ExpectedResult = false)]
        [TestCase(null, ExpectedResult = false)]
        public bool KnownTags(string tag) => TitleBlocks.IsKnownTag(tag);

        [TestCase("PartNo", ExpectedResult = "PART_NO")]
        [TestCase("partno", ExpectedResult = "PART_NO")]
        [TestCase("Description", ExpectedResult = "DESCRIPTION")]
        [TestCase("Material", ExpectedResult = "MATERIAL")]
        [TestCase("Finish", ExpectedResult = null)]
        [TestCase(null, ExpectedResult = null)]
        public string PropertiesWrittenToTheTitleBlock(string property) => TitleBlocks.TagFor(property);

        [Test]
        public void Reader_KeepsTheFirstNonEmptyValueOfKnownTags()
        {
            var r = new TitleBlockReader();
            r.Add("PART_NO", "  ");                  // empty attribute in a revision table
            r.Add("PART_NO", " CP-80-014 ");
            r.Add("part_no", "CP-80-999");          // a second title block on another layout
            r.Add("SCALE", "1:2");                  // not a Kosha tag
            r.Add(null, "x");
            r.Add("MATERIAL", "EN-GJL-250");
            Assert.That(r.Values["PART_NO"], Is.EqualTo("CP-80-014"));
            Assert.That(r.Values["Material"], Is.EqualTo("EN-GJL-250"), "case-insensitive");
            Assert.That(r.Values.ContainsKey("SCALE"), Is.False);
            Assert.That(r.Values.Count, Is.EqualTo(2));
        }

        [Test]
        public void Merge_DrawingPropertiesWin_ThenTitle_ThenTitleBlock()
        {
            var custom = new Dictionary<string, string> { ["PartNo"] = "CP-80-014", ["Material"] = " ", ["Vendor"] = "Demo Castings" };
            var block = new Dictionary<string, string> { ["PART_NO"] = "CP-80-999", ["MATERIAL"] = "EN-GJL-250", ["partno"] = "ignored" };
            var merged = TitleBlocks.Merge(custom, "Inlet flange", block);

            Assert.That(merged["PartNo"], Is.EqualTo("CP-80-014"), "a drawing property beats the title block");
            Assert.That(merged["material"], Is.EqualTo("EN-GJL-250"), "an empty property does not hide the title block");
            Assert.That(merged["Title"], Is.EqualTo("Inlet flange"));
            Assert.That(merged["Vendor"], Is.EqualTo("Demo Castings"));
            Assert.That(PartNumbers.PartNumberOf(merged, "x.dwg"), Is.EqualTo("CP-80-014"));
            Assert.That(PartNumbers.DescriptionOf(merged, "x.dwg"), Is.EqualTo("Inlet flange"));
        }

        [Test]
        public void Merge_TitleBlockAlone_GivesPartNumberAndDescription()
        {
            var block = new Dictionary<string, string> { ["DWG_NO"] = "SC-300-021", ["DESCRIPTION"] = "Conveyor side plate" };
            var merged = TitleBlocks.Merge(null, null, block);
            Assert.That(PartNumbers.PartNumberOf(merged, "Drawing1.dwg"), Is.EqualTo("SC-300-021"));
            Assert.That(PartNumbers.DescriptionOf(merged, "Drawing1.dwg"), Is.EqualTo("Conveyor side plate"));
        }
    }
}
