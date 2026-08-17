using System.IO;
using System.Runtime.InteropServices;

namespace Herdi.Services;

/// <summary>
/// Creates the Start Menu shortcut that gives this unpackaged app a stable
/// AppUserModelID and a ToastActivatorCLSID.
///
/// Windows will not deliver toasts for a plain Win32 exe unless the AUMID it calls
/// CreateToastNotifier with is backed by such a shortcut — without it notifications
/// either never appear or show up under someone else's name (the classic
/// "my toast says PowerShell" symptom). herdi-mac needs none of this: macOS derives
/// notification identity from the .app bundle id.
/// </summary>
internal static class ShortcutHelper
{
    /// <summary>Install the shortcut if missing or stale. Returns true when present afterwards.</summary>
    public static bool EnsureShortcut(string aumid, Guid activatorClsid, string displayName)
    {
        try
        {
            var exePath = Environment.ProcessPath;
            if (string.IsNullOrEmpty(exePath)) return false;

            var shortcutPath = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData),
                "Microsoft", "Windows", "Start Menu", "Programs", displayName + ".lnk");

            if (File.Exists(shortcutPath) && PointsAt(shortcutPath, exePath)) return true;

            Directory.CreateDirectory(Path.GetDirectoryName(shortcutPath)!);
            Create(shortcutPath, exePath, aumid, activatorClsid, displayName);
            return true;
        }
        catch (Exception)
        {
            // A missing shortcut degrades to "no toasts", which the caller reports;
            // it must not take the app down at startup.
            return false;
        }
    }

    private static bool PointsAt(string shortcutPath, string exePath)
    {
        try
        {
            var link = (IShellLinkW)new CShellLink();
            ((IPersistFile)link).Load(shortcutPath, 0);
            var sb = new System.Text.StringBuilder(260);
            link.GetPath(sb, sb.Capacity, IntPtr.Zero, 0);
            return string.Equals(sb.ToString(), exePath, StringComparison.OrdinalIgnoreCase);
        }
        catch (Exception)
        {
            return false;
        }
    }

    private static void Create(string shortcutPath, string exePath, string aumid, Guid clsid, string displayName)
    {
        var link = (IShellLinkW)new CShellLink();
        link.SetPath(exePath);
        link.SetArguments(string.Empty);
        link.SetDescription(displayName);
        var dir = Path.GetDirectoryName(exePath);
        if (!string.IsNullOrEmpty(dir)) link.SetWorkingDirectory(dir);

        var store = (IPropertyStore)link;
        // System.AppUserModel.ID — the identity CreateToastNotifier(aumid) resolves against.
        using (var v = PropVariant.FromString(aumid))
        {
            store.SetValue(PropertyKeys.AppUserModelId, v.Ref);
        }
        // System.AppUserModel.ToastActivatorCLSID — lets toast buttons call back into us.
        using (var v = PropVariant.FromClsid(clsid))
        {
            store.SetValue(PropertyKeys.ToastActivatorClsid, v.Ref);
        }
        store.Commit();

        ((IPersistFile)link).Save(shortcutPath, true);
    }

    private static class PropertyKeys
    {
        private static readonly Guid AppUserModel = new("9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3");
        public static PropertyKey AppUserModelId => new(AppUserModel, 5);
        public static PropertyKey ToastActivatorClsid => new(AppUserModel, 26);
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct PropertyKey(Guid formatId, uint propertyId)
    {
        public Guid FormatId = formatId;
        public uint PropertyId = propertyId;
    }

    /// <summary>
    /// Minimal PROPVARIANT holder for the two property types we set (VT_LPWSTR, VT_CLSID).
    /// </summary>
    private sealed class PropVariant : IDisposable
    {
        private const ushort VtLpwstr = 31;
        private const ushort VtClsid = 72;

        private IntPtr _buffer;
        private readonly ushort _type;
        private IntPtr _native;

        private PropVariant(ushort type, IntPtr buffer)
        {
            _type = type;
            _buffer = buffer;
            // PROPVARIANT is 24 bytes on x64: 2 (vt) + 6 padding + 8 (pointer) + 8 slack.
            _native = Marshal.AllocCoTaskMem(24);
            for (var i = 0; i < 24; i++) Marshal.WriteByte(_native, i, 0);
            Marshal.WriteInt16(_native, 0, (short)_type);
            Marshal.WriteIntPtr(_native, 8, _buffer);
        }

        public IntPtr Ref => _native;

        public static PropVariant FromString(string value) =>
            new(VtLpwstr, Marshal.StringToCoTaskMemUni(value));

        public static PropVariant FromClsid(Guid value)
        {
            var buffer = Marshal.AllocCoTaskMem(16);
            Marshal.Copy(value.ToByteArray(), 0, buffer, 16);
            return new PropVariant(VtClsid, buffer);
        }

        public void Dispose()
        {
            if (_buffer != IntPtr.Zero)
            {
                Marshal.FreeCoTaskMem(_buffer);
                _buffer = IntPtr.Zero;
            }
            if (_native != IntPtr.Zero)
            {
                Marshal.FreeCoTaskMem(_native);
                _native = IntPtr.Zero;
            }
        }
    }

    [ComImport, Guid("00021401-0000-0000-C000-000000000046")]
    private class CShellLink;

    [ComImport, Guid("000214F9-0000-0000-C000-000000000046")]
    [InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    private interface IShellLinkW
    {
        void GetPath(
            [MarshalAs(UnmanagedType.LPWStr)] System.Text.StringBuilder file,
            int maxPath, IntPtr findData, uint flags);
        void GetIDList(out IntPtr idList);
        void SetIDList(IntPtr idList);
        void GetDescription(
            [MarshalAs(UnmanagedType.LPWStr)] System.Text.StringBuilder name, int maxName);
        void SetDescription([MarshalAs(UnmanagedType.LPWStr)] string name);
        void GetWorkingDirectory(
            [MarshalAs(UnmanagedType.LPWStr)] System.Text.StringBuilder dir, int maxPath);
        void SetWorkingDirectory([MarshalAs(UnmanagedType.LPWStr)] string dir);
        void GetArguments(
            [MarshalAs(UnmanagedType.LPWStr)] System.Text.StringBuilder args, int maxArgs);
        void SetArguments([MarshalAs(UnmanagedType.LPWStr)] string args);
        void GetHotKey(out short hotkey);
        void SetHotKey(short hotkey);
        void GetShowCmd(out uint showCmd);
        void SetShowCmd(uint showCmd);
        void GetIconLocation(
            [MarshalAs(UnmanagedType.LPWStr)] System.Text.StringBuilder icon, int maxPath, out int index);
        void SetIconLocation([MarshalAs(UnmanagedType.LPWStr)] string icon, int index);
        void SetRelativePath([MarshalAs(UnmanagedType.LPWStr)] string pathRel, uint reserved);
        void Resolve(IntPtr hwnd, uint flags);
        void SetPath([MarshalAs(UnmanagedType.LPWStr)] string file);
    }

    [ComImport, Guid("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99")]
    [InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    private interface IPropertyStore
    {
        void GetCount(out uint count);
        void GetAt(uint index, out PropertyKey key);
        void GetValue(ref PropertyKey key, IntPtr value);
        void SetValue(PropertyKey key, IntPtr value);
        void Commit();
    }

    [ComImport, Guid("0000010B-0000-0000-C000-000000000046")]
    [InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    private interface IPersistFile
    {
        void GetClassID(out Guid classId);
        [PreserveSig] int IsDirty();
        void Load([MarshalAs(UnmanagedType.LPWStr)] string fileName, uint mode);
        void Save([MarshalAs(UnmanagedType.LPWStr)] string? fileName, [MarshalAs(UnmanagedType.Bool)] bool remember);
        void SaveCompleted([MarshalAs(UnmanagedType.LPWStr)] string fileName);
        void GetCurFile([MarshalAs(UnmanagedType.LPWStr)] out string fileName);
    }
}
