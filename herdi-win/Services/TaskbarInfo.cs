using System.Runtime.InteropServices;
using Drawing = System.Drawing;
using Forms = System.Windows.Forms;

namespace Herdi.Services;

/// <summary>Screen edge the taskbar is docked to. Values match the shell's ABE_* constants.</summary>
public enum TaskbarEdge
{
    Left = 0,
    Top = 1,
    Right = 2,
    Bottom = 3,
}

/// <summary>
/// Where the taskbar is and how much room is left beside it, in physical pixels. Both
/// rectangles come from the shell rather than from any assumption about where the taskbar
/// lives: it can be docked to any of the four edges, auto-hide, or (Windows 11) sit
/// centred on a display that is not the primary one.
///
/// The notification area is always at the taskbar's trailing end — right for a horizontal
/// bar, bottom for a vertical one — so <see cref="TrayCorner"/> is enough to anchor a
/// flyout under the tray icon without asking the shell for the icon's own rectangle.
/// Shell_NotifyIconGetRect would give that exactly, but it needs the window handle and id
/// WinForms' NotifyIcon keeps private, and it reports the overflow chevron rather than the
/// icon whenever the user has hidden it — so the corner is both simpler and steadier.
/// </summary>
/// <param name="Edge">Which edge the bar is docked to.</param>
/// <param name="Bounds">The bar itself. Empty height/width for an auto-hidden bar.</param>
/// <param name="WorkArea">Usable area of the display the bar is on.</param>
public readonly record struct TaskbarInfo(TaskbarEdge Edge, Drawing.Rectangle Bounds, Drawing.Rectangle WorkArea)
{
    /// <summary>True while the bar runs left-to-right, i.e. docked top or bottom.</summary>
    public bool IsHorizontal => Edge is TaskbarEdge.Top or TaskbarEdge.Bottom;

    /// <summary>
    /// The corner of the bar the notification area sits in, and which a flyout hangs off.
    /// </summary>
    public Drawing.Point TrayCorner => Edge switch
    {
        // Horizontal bars: tray at the right end, flyout grows away from the bar.
        TaskbarEdge.Bottom => new Drawing.Point(Bounds.Right, Bounds.Top),
        TaskbarEdge.Top => new Drawing.Point(Bounds.Right, Bounds.Bottom),
        // Vertical bars: tray at the bottom end.
        TaskbarEdge.Left => new Drawing.Point(Bounds.Right, Bounds.Bottom),
        _ => new Drawing.Point(Bounds.Left, Bounds.Bottom),
    };

    /// <summary>
    /// Ask the shell where the taskbar is. Falls back to deriving it from the primary
    /// display when the shell does not answer — during an Explorer restart, most often.
    /// </summary>
    public static TaskbarInfo Current()
    {
        var data = new AppBarData { cbSize = (uint)Marshal.SizeOf<AppBarData>() };
        if (SHAppBarMessage(AbmGetTaskbarPos, ref data) != IntPtr.Zero)
        {
            var bounds = Drawing.Rectangle.FromLTRB(
                data.rc.Left, data.rc.Top, data.rc.Right, data.rc.Bottom);
            if (bounds.Width > 0 && bounds.Height > 0)
            {
                var edge = data.uEdge <= 3 ? (TaskbarEdge)data.uEdge : TaskbarEdge.Bottom;
                // The bar's own display, not the primary one: Windows 11 can put the
                // taskbar-with-tray on whichever monitor the user made primary for it.
                return new TaskbarInfo(edge, bounds, Forms.Screen.FromRectangle(bounds).WorkingArea);
            }
        }
        return FromPrimaryScreen();
    }

    /// <summary>
    /// Derive the bar from the gap between the primary display's bounds and its work area.
    /// That gap is the strip the bar reserves, so its thickness and its side both fall out
    /// of the comparison. An auto-hidden bar reserves nothing, which leaves the bottom edge
    /// as the guess — the corner is still right, only the clearance is assumed.
    /// </summary>
    private static TaskbarInfo FromPrimaryScreen()
    {
        var screen = Forms.Screen.PrimaryScreen;
        if (screen is null)
        {
            return new TaskbarInfo(TaskbarEdge.Bottom, Drawing.Rectangle.Empty, Drawing.Rectangle.Empty);
        }

        var full = screen.Bounds;
        var work = screen.WorkingArea;

        if (work.Bottom < full.Bottom)
            return new(TaskbarEdge.Bottom, Drawing.Rectangle.FromLTRB(full.Left, work.Bottom, full.Right, full.Bottom), work);
        if (work.Top > full.Top)
            return new(TaskbarEdge.Top, Drawing.Rectangle.FromLTRB(full.Left, full.Top, full.Right, work.Top), work);
        if (work.Right < full.Right)
            return new(TaskbarEdge.Right, Drawing.Rectangle.FromLTRB(work.Right, full.Top, full.Right, full.Bottom), work);
        if (work.Left > full.Left)
            return new(TaskbarEdge.Left, Drawing.Rectangle.FromLTRB(full.Left, full.Top, work.Left, full.Bottom), work);

        // Nothing reserved: an auto-hidden bottom bar, collapsed to a line at the edge.
        return new(TaskbarEdge.Bottom, Drawing.Rectangle.FromLTRB(full.Left, full.Bottom, full.Right, full.Bottom), work);
    }

    // --- Win32

    private const uint AbmGetTaskbarPos = 0x00000005;

    [StructLayout(LayoutKind.Sequential)]
    private struct NativeRect
    {
        public int Left;
        public int Top;
        public int Right;
        public int Bottom;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct AppBarData
    {
        public uint cbSize;
        public IntPtr hWnd;
        public uint uCallbackMessage;
        public uint uEdge;
        public NativeRect rc;
        public int lParam;
    }

    [DllImport("shell32.dll", SetLastError = true)]
    private static extern IntPtr SHAppBarMessage(uint message, ref AppBarData data);
}
