// SPDX-License-Identifier: Apache-2.0
using System;

namespace Kosha.Client
{
    /// <summary>An error with a message the user can act on. Status 0 means the server could not be reached.</summary>
    public class KoshaException : Exception
    {
        public int Status { get; }
        public string Code { get; }

        public KoshaException(string message, int status, string code) : base(message)
        {
            Status = status;
            Code = code ?? "";
        }

        public bool IsSignInNeeded => Status == 401;
        public bool IsNetwork => Status == 0;
    }
}
