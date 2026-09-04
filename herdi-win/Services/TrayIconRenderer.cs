using System.Runtime.InteropServices;
using Drawing = System.Drawing;
using Drawing2D = System.Drawing.Drawing2D;
using Text = System.Drawing.Text;

namespace Herdi.Services;

/// <summary>
/// What the tray icon's corner badge is saying, in priority order: an agent waiting on an
/// answer outranks one that finished, which outranks one merely working, and none is worth
/// a badge when there is nothing to report.
/// </summary>
public enum TrayBadge
{
    /// <summary>Nothing needs saying — the plain glyph.</summary>
    None,

    /// <summary>Agents are blocked on the user. Red, and the number is the point.</summary>
    Blocked,

    /// <summary>
    /// Agents finished (herdr's `done` — a harness-reported completion, which is a
    /// different status from `idle` and is never folded into it). Orange.
    /// </summary>
    Done,

    /// <summary>Agents are working. Green, informational.</summary>
    Working,
}

/// <summary>
/// Draws the tray icon: the app glyph with a counted badge in its bottom-right corner.
///
/// The shipped Assets/herdi-blocked.ico already puts a red "!" disc exactly there, so this
/// keeps that composition and only replaces the glyph inside the disc with the count. What
/// it cannot be is a fixed set of .ico files — the count is unbounded, and the tray asks for
/// a different pixel size at every DPI (16 at 100 %, 20 at 125 %, 24 at 150 %, 32 at 200 %),
/// so the icon has to be composed at the size the shell actually wants.
/// </summary>
internal static class TrayIconRenderer
{
    /// <summary>
    /// Badge diameter as a fraction of the icon. Big enough that a digit survives the 16 px
    /// the shell asks for at 100 % DPI, small enough to leave the sheep recognisable — the
    /// trade every badged tray icon makes, settled by rendering it rather than guessing.
    ///
    /// The tray icon's own size is not ours to grow: the shell asks for SM_CXSMICON, which is
    /// 16 px logical whatever the DPI, and the .ico frames already fill their canvas edge to
    /// edge. So this constant and <see cref="DigitFraction"/> are the only two things "make
    /// it bigger" can mean. Past about 0.70 the sheep is gone and the icon reads as a bare
    /// number, which is a different icon rather than a larger one.
    /// </summary>
    private const double BadgeFraction = 0.64;

    /// <summary>
    /// Digit em size as a fraction of the disc's inner diameter. Near 1.0 because a digit's
    /// ink is only its cap height — roughly 0.7 em for Segoe UI, all of it above the baseline
    /// — so an em the size of the disc draws a digit filling about two thirds of it. Anything
    /// under ~0.9 was leaving legibility on the table at 16 px for nothing.
    /// </summary>
    private const double DigitFraction = 0.98;

    /// <summary>Counts above this are shown as a bare "+"; two digits do not fit at 16 px.</summary>
    public const int MaxShownCount = 9;

    // Red disc, white digit for blocked — the palette's Red, and what the shipped
    // herdi-blocked.ico already uses for its "!".
    private static readonly Drawing.Color BlockedFill = Drawing.Color.FromArgb(0xFF, 0x45, 0x3A);
    private static readonly Drawing.Color BlockedInk = Drawing.Color.White;

    // Working is the inverse: a dark disc carrying a green digit, rather than a green disc
    // carrying a dark one. Green-on-green is the obvious choice and it is the wrong one —
    // the disc vanishes into the glyph's own green and the digit turns to mush by 20 px.
    // Reversing it buys separation from the glyph and contrast for the digit at once, and
    // keeps green as what "working" is coloured, which a neutral disc would have spent.
    private static readonly Drawing.Color WorkingFill = Drawing.Color.FromArgb(0x1C, 0x1C, 0x1E);
    private static readonly Drawing.Color WorkingInk = Drawing.Color.FromArgb(0x32, 0xD7, 0x4B);

    // Done is the same trick turned the other way round: an orange disc (the web app's
    // ready colour) carrying the dark ink. The disc itself must be the signal, because at
    // 16 px a hue change on a 6 px digit is not one a glance can read; and the ink is dark
    // rather than white because white on this orange measures under 2:1 while the dark
    // measures over 8:1. White digits stay exclusive to blocked — the one badge that is a
    // question rather than a report.
    private static readonly Drawing.Color DoneFill = Drawing.Color.FromArgb(0xFF, 0x9F, 0x0A);
    private static readonly Drawing.Color DoneInk = Drawing.Color.FromArgb(0x1C, 0x1C, 0x1E);

    /// <summary>
    /// Ring between the disc and whatever is behind it, so the red one reads against a light
    /// taskbar as well as a dark one. The dark disc does not need it — near-black on
    /// near-black just widens the disc a hair — but one rule for both is simpler than a rule
    /// with an exception.
    /// </summary>
    private static readonly Drawing.Color RingColor = Drawing.Color.FromArgb(0xE6, 0x11, 0x11, 0x13);

    /// <summary>
    /// Compose the glyph with a badge. Returns null if GDI+ will not play along, which the
    /// caller answers by falling back to a shipped .ico — a tray icon is not worth an
    /// exception reaching the dispatcher.
    /// </summary>
    public static Drawing.Icon? Compose(Drawing.Icon glyph, TrayBadge badge, int count, int size)
    {
        if (badge == TrayBadge.None || count <= 0 || size <= 0) return null;

        try
        {
            using var canvas = new Drawing.Bitmap(size, size, Drawing.Imaging.PixelFormat.Format32bppArgb);
            using (var g = Drawing.Graphics.FromImage(canvas))
            {
                g.SmoothingMode = Drawing2D.SmoothingMode.AntiAlias;
                g.InterpolationMode = Drawing2D.InterpolationMode.HighQualityBicubic;
                g.PixelOffsetMode = Drawing2D.PixelOffsetMode.HighQuality;
                // ClearType on a transparent background fringes the glyph edges with colour,
                // because subpixel rendering assumes it knows what is behind the text.
                g.TextRenderingHint = Text.TextRenderingHint.AntiAlias;

                using var glyphImage = glyph.ToBitmap();
                g.DrawImage(glyphImage, new Drawing.Rectangle(0, 0, size, size));

                DrawBadge(g, badge, count, size);
            }
            return FromBitmap(canvas);
        }
        catch (Exception)
        {
            return null;
        }
    }

    private static void DrawBadge(Drawing.Graphics g, TrayBadge badge, int count, int size)
    {
        var diameter = Math.Max(6f, (float)(size * BadgeFraction));
        var ring = Math.Max(1f, size / 16f);

        // Flush with the bottom-right corner, as the shipped blocked icon's disc is.
        var outer = new Drawing.RectangleF(size - diameter, size - diameter, diameter, diameter);
        var inner = Drawing.RectangleF.Inflate(outer, -ring, -ring);

        using (var ringBrush = new Drawing.SolidBrush(RingColor))
        {
            g.FillEllipse(ringBrush, outer);
        }

        var blocked = badge == TrayBadge.Blocked;
        var (fill, ink) = badge switch
        {
            TrayBadge.Blocked => (BlockedFill, BlockedInk),
            TrayBadge.Done => (DoneFill, DoneInk),
            _ => (WorkingFill, WorkingInk),
        };
        using (var fillBrush = new Drawing.SolidBrush(fill))
        {
            g.FillEllipse(fillBrush, inner);
        }

        DrawCount(g, count, inner, ink);
    }

    private static void DrawCount(Drawing.Graphics g, int count, Drawing.RectangleF disc, Drawing.Color ink)
    {
        var text = count > MaxShownCount ? "+" : count.ToString();
        var emSize = Math.Max(5f, (float)(disc.Height * DigitFraction));

        using var font = new Drawing.Font(
            "Segoe UI", emSize, Drawing.FontStyle.Bold, Drawing.GraphicsUnit.Pixel);
        using var brush = new Drawing.SolidBrush(ink);
        using var format = new Drawing.StringFormat(Drawing.StringFormatFlags.NoWrap)
        {
            Alignment = Drawing.StringAlignment.Center,
            LineAlignment = Drawing.StringAlignment.Center,
            Trimming = Drawing.StringTrimming.None,
        };

        // Centred on a point, not laid out in a rectangle. The rectangle overload clips to
        // its bounds, and at this DigitFraction the line box — em plus descent — is taller
        // than the disc, so a rectangle would centre the box and then cut the digit off top
        // and bottom. Given a point, the same StringFormat centres on it and nothing clips.
        var centre = new Drawing.PointF(disc.X + disc.Width / 2f, disc.Y + disc.Height / 2f);
        g.DrawString(text, font, brush, centre, format);
    }

    /// <summary>
    /// Turn the composed bitmap into an Icon that owns its own bits. GetHicon hands back a
    /// raw HICON that Icon.FromHandle only borrows — leaking one per repaint would run the
    /// process out of GDI handles — so the handle is cloned into a managed icon and
    /// destroyed here, which is the documented pairing for it.
    /// </summary>
    private static Drawing.Icon FromBitmap(Drawing.Bitmap bitmap)
    {
        var handle = bitmap.GetHicon();
        try
        {
            using var borrowed = Drawing.Icon.FromHandle(handle);
            return (Drawing.Icon)borrowed.Clone();
        }
        finally
        {
            DestroyIcon(handle);
        }
    }

    [DllImport("user32.dll", SetLastError = true)]
    private static extern bool DestroyIcon(IntPtr handle);
}
