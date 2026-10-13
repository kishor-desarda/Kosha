// SPDX-License-Identifier: Apache-2.0
using System.Collections.Generic;
using Newtonsoft.Json;

namespace Kosha.Client
{
    public sealed class UserInfo
    {
        [JsonProperty("id")] public int Id { get; set; }
        [JsonProperty("username")] public string Username { get; set; }
        [JsonProperty("full_name")] public string FullName { get; set; }
        [JsonProperty("role")] public string Role { get; set; }
    }

    public sealed class ActionCheck
    {
        [JsonProperty("ok")] public bool Ok { get; set; }
        [JsonProperty("why")] public string Why { get; set; }
    }

    /// <summary>What Kosha knows about one file (GET /api/addin/status and the check-in/out replies).</summary>
    public sealed class DocStatus
    {
        [JsonProperty("exists")] public bool Exists { get; set; } = true;
        [JsonProperty("id")] public int Id { get; set; }
        [JsonProperty("folder_id")] public int FolderId { get; set; }
        [JsonProperty("filename")] public string FileName { get; set; }
        [JsonProperty("part_number")] public string PartNumber { get; set; }
        [JsonProperty("description")] public string Description { get; set; }
        [JsonProperty("revision")] public string Revision { get; set; }
        [JsonProperty("state")] public string State { get; set; }
        [JsonProperty("state_label")] public string StateLabel { get; set; }
        [JsonProperty("version")] public int Version { get; set; }
        [JsonProperty("checked_out_by")] public int? CheckedOutBy { get; set; }
        [JsonProperty("checked_out_name")] public string CheckedOutName { get; set; }
        [JsonProperty("latest_sha")] public string LatestSha { get; set; }
        [JsonProperty("actions")] public Dictionary<string, ActionCheck> Actions { get; set; } = new Dictionary<string, ActionCheck>();
        [JsonProperty("bom")] public object Bom { get; set; }

        public ActionCheck Action(string name) =>
            Actions != null && Actions.TryGetValue(name, out var a) && a != null ? a : new ActionCheck { Ok = false, Why = "" };
    }

    /// <summary>One Kosha folder (GET /api/folders). The root folder, "Kosha", has no parent.</summary>
    public sealed class FolderInfo
    {
        [JsonProperty("id")] public int Id { get; set; }
        [JsonProperty("parent_id")] public int? ParentId { get; set; }
        [JsonProperty("name")] public string Name { get; set; }
    }

    public sealed class SimilarPart
    {
        [JsonProperty("id")] public int Id { get; set; }
        [JsonProperty("part_number")] public string PartNumber { get; set; }
        [JsonProperty("description")] public string Description { get; set; }
        [JsonProperty("score")] public double Score { get; set; }
    }

    /// <summary>One line of an assembly's structure, as the server turns it into the engineering BOM.</summary>
    public sealed class StructureLine
    {
        [JsonProperty("file")] public string File { get; set; }
        [JsonProperty("configuration")] public string Configuration { get; set; } = "";
        [JsonProperty("part_number")] public string PartNumber { get; set; }
        [JsonProperty("description")] public string Description { get; set; }
        [JsonProperty("material")] public string Material { get; set; } = "";
        [JsonProperty("qty")] public int Quantity { get; set; } = 1;
        [JsonProperty("virtual")] public bool IsVirtual { get; set; }
    }

    /// <summary>Everything sent with one check-in.</summary>
    public sealed class CheckinRequest
    {
        public string Folder { get; set; }
        public string FileName { get; set; }
        public byte[] File { get; set; }
        public Dictionary<string, string> Properties { get; set; } = new Dictionary<string, string>();
        public List<StructureLine> Structure { get; set; }
        public byte[] PreviewPng { get; set; }
        public byte[] MeshStl { get; set; }
        public string Comment { get; set; } = "";
        public bool KeepCheckedOut { get; set; }
        public string Configuration { get; set; } = "";
        public string Source { get; set; } = "cad-addin";
    }
}
