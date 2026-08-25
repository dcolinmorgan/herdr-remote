using Microsoft.Win32;

namespace Herdi.Services;

/// <summary>
/// Run-at-login via the per-user Run key — the Windows counterpart to herdi-mac's
/// SMAppService.mainApp.register(). HKCU needs no elevation.
/// </summary>
internal static class StartupManager
{
    private const string RunKey = @"Software\Microsoft\Windows\CurrentVersion\Run";
    private const string ValueName = "Herdi";

    public static bool IsEnabled
    {
        get
        {
            try
            {
                using var key = Registry.CurrentUser.OpenSubKey(RunKey);
                return key?.GetValue(ValueName) is string existing && existing.Length > 0;
            }
            catch (Exception)
            {
                return false;
            }
        }
    }

    /// <summary>Returns true when the requested state was achieved.</summary>
    public static bool SetEnabled(bool enabled)
    {
        try
        {
            using var key = Registry.CurrentUser.CreateSubKey(RunKey);
            if (key is null) return false;

            if (!enabled)
            {
                key.DeleteValue(ValueName, throwOnMissingValue: false);
                return true;
            }

            var exePath = Environment.ProcessPath;
            if (string.IsNullOrEmpty(exePath)) return false;
            key.SetValue(ValueName, $"\"{exePath}\"");
            return true;
        }
        catch (Exception)
        {
            return false;
        }
    }
}
