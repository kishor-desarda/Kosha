// SPDX-License-Identifier: Apache-2.0
using System;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;

namespace Kosha.Client
{
    /// <summary>Keeps the sign-in token unreadable on disk.</summary>
    public interface ITokenProtector
    {
        string Protect(string token);
        string Unprotect(string stored);
    }

    /// <summary>Windows DPAPI, scoped to the current Windows user: another user or PC cannot read the token.</summary>
    public sealed class DpapiTokenProtector : ITokenProtector
    {
        static readonly byte[] Entropy = Encoding.UTF8.GetBytes("Kosha.Client.Token.v1");

        public string Protect(string token)
        {
            if (string.IsNullOrEmpty(token)) return "";
            var bytes = ProtectedData.Protect(Encoding.UTF8.GetBytes(token), Entropy, DataProtectionScope.CurrentUser);
            return "dpapi:" + Convert.ToBase64String(bytes);
        }

        public string Unprotect(string stored)
        {
            if (string.IsNullOrEmpty(stored) || !stored.StartsWith("dpapi:", StringComparison.Ordinal)) return "";
            try
            {
                var bytes = ProtectedData.Unprotect(Convert.FromBase64String(stored.Substring(6)), Entropy, DataProtectionScope.CurrentUser);
                return Encoding.UTF8.GetString(bytes);
            }
            catch (CryptographicException)
            {
                return "";   // copied from another user or PC: sign in again
            }
            catch (FormatException)
            {
                return "";
            }
        }
    }

    /// <summary>Keeps the token in memory only (tests, and platforms without DPAPI). Nothing is written to disk.</summary>
    public sealed class MemoryOnlyTokenProtector : ITokenProtector
    {
        public string Protect(string token) => "";
        public string Unprotect(string stored) => "";
    }

    public static class TokenProtectors
    {
        public static ITokenProtector ForThisMachine() =>
            RuntimeInformation.IsOSPlatform(OSPlatform.Windows) ? (ITokenProtector)new DpapiTokenProtector() : new MemoryOnlyTokenProtector();
    }
}
