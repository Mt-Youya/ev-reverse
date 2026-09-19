// Reads a generic credential out of Windows Credential Manager.
//
// Stored credentials are encrypted with DPAPI and readable only by the account that wrote them, which
// is why this exists instead of a plaintext file. Two things about the API are easy to get wrong:
//
//   * `CRED_TYPE_GENERIC` credentials come back with `CredentialBlobSize` already in **bytes**, while
//     the docs describe the field as a character count. Converting it with `* 2` truncates or
//     overruns; the length is used as-is here.
//   * `CredFree` must be called on the pointer the API allocated, not on the `CredentialBlob`.
//
// The blob is UTF-16LE with no terminating null, so it is decoded as Unicode.
//
//   CredRead.exe <target>              writes the secret to stdout
//   CredRead.exe --check <target>      writes only its length

using System;
using System.Runtime.InteropServices;
using System.Text;

static class CredReadTool
{
    [DllImport("advapi32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern bool CredRead(string target, int type, int flags, out IntPtr credential);

    [DllImport("advapi32.dll")]
    static extern void CredFree(IntPtr buffer);

    const int CRED_TYPE_GENERIC = 1;

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    struct CREDENTIAL
    {
        public int Flags;
        public int Type;
        public IntPtr TargetName;
        public IntPtr Comment;
        public long LastWritten;
        public int CredentialBlobSize;
        public IntPtr CredentialBlob;
        public int Persist;
        public int AttributeCount;
        public IntPtr Attributes;
        public IntPtr TargetAlias;
        public IntPtr UserName;
    }

    static int Main(string[] args)
    {
        var check = args.Length > 1 && args[0] == "--check";
        if (args.Length == 0 || (check && args.Length < 2))
        {
            Console.Error.WriteLine("usage: CredRead.exe [--check] <target>");
            return 2;
        }
        var target = check ? args[1] : args[0];

        IntPtr raw;
        if (!CredRead(target, CRED_TYPE_GENERIC, 0, out raw))
        {
            Console.Error.WriteLine("CredRead failed for '" + target + "' (Win32 error "
                                    + Marshal.GetLastWin32Error() + ")");
            return 1;
        }
        try
        {
            var credential = (CREDENTIAL)Marshal.PtrToStructure(raw, typeof(CREDENTIAL));
            var blob = new byte[credential.CredentialBlobSize];
            if (credential.CredentialBlobSize > 0)
            {
                Marshal.Copy(credential.CredentialBlob, blob, 0, credential.CredentialBlobSize);
            }
            var text = Encoding.Unicode.GetString(blob);

            if (check)
            {
                Console.WriteLine(text.Length);
                return text.Length > 0 ? 0 : 1;
            }
            Console.Out.Write(text);
            Console.Out.Flush();
            return 0;
        }
        finally
        {
            CredFree(raw);
        }
    }
}
