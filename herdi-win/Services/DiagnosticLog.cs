using System.IO;

namespace Herdi.Services;

/// <summary>
/// One append-only log for the whole app.
///
/// A tray app has nowhere to report anything. There is no console, no main window, and the
/// only unhandled-exception path is a message box that fires once and takes the detail with
/// it — so every subsystem that fails quietly by design (notifications above all, but also
/// SSH polls and socket reconnects) has no way to account for itself. This is that way.
///
/// The file is deliberately dull: local timestamps, one line per event, no levels, no
/// rotation beyond a size cap. It exists to be pasted into a bug report.
/// </summary>
internal static class DiagnosticLog
{
    private const long MaxBytes = 256 * 1024;

    private static readonly object Gate = new();

    public static string Path { get; } = System.IO.Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
        "herdr-remote", "herdi.log");

    public static void Write(string message)
    {
        lock (Gate)
        {
            try
            {
                Directory.CreateDirectory(System.IO.Path.GetDirectoryName(Path)!);
                // A tray app runs for weeks; cap the file rather than grow it forever.
                if (File.Exists(Path) && new FileInfo(Path).Length > MaxBytes) File.Delete(Path);
                File.AppendAllText(
                    Path, $"{DateTime.Now:yyyy-MM-dd HH:mm:ss} {message}{Environment.NewLine}");
            }
            catch (Exception)
            {
                // Logging is best-effort by definition: a failure to record a problem must
                // never become a second problem.
            }
        }
    }
}
