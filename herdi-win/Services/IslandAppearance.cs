using System.Windows.Media;

namespace Herdi.Services;

/// <summary>
/// How the island paints itself: the silhouette colour plus the two opacity levels it
/// animates between. herdi-mac has no equivalent — its panel hides inside the notch,
/// where pure black at full opacity is the only sensible answer. Here the capsule sits
/// on top of whichever window owns the top-centre of the screen, so how loud it is has
/// to be the user's call.
/// </summary>
/// <param name="Fill">Silhouette colour. Always opaque: transparency is the two
/// opacities' job, and an alpha here would compound with them.</param>
/// <param name="CollapsedOpacity">The capsule at rest, pointer elsewhere.</param>
/// <param name="ExpandedOpacity">Hovered, expanded, or working in a surface.</param>
public readonly record struct IslandAppearance(
    Color Fill,
    double CollapsedOpacity,
    double ExpandedOpacity)
{
    /// <summary>
    /// Floor for both sliders. Below this the collapsed capsule is hard to find and the
    /// expanded text is unreadable — and since the settings dialog is reached through a
    /// tray menu rather than the island, an invisible island would not be unrecoverable,
    /// only annoying. The clamp keeps a hand-edited settings.json inside the same range.
    /// </summary>
    public const double MinOpacity = 0.2;

    public const double DefaultCollapsedOpacity = 0.75;
    public const double DefaultExpandedOpacity = 1.0;

    /// <summary>Pure black, as herdi-mac fills the notch shape with.</summary>
    public static Color DefaultFill => Colors.Black;

    public static IslandAppearance Default =>
        new(DefaultFill, DefaultCollapsedOpacity, DefaultExpandedOpacity);

    /// <summary>Drop any alpha and pull both opacities back into range.</summary>
    public IslandAppearance Normalized() => new(
        Color.FromRgb(Fill.R, Fill.G, Fill.B),
        Clamp(CollapsedOpacity, DefaultCollapsedOpacity),
        Clamp(ExpandedOpacity, DefaultExpandedOpacity));

    public static string ToHex(Color color) => $"#{color.R:X2}{color.G:X2}{color.B:X2}";

    /// <summary>
    /// Parse "#RRGGBB", "RRGGBB", or anything else <see cref="ColorConverter"/> takes.
    /// Null for input that is not a colour yet — half-typed hex is the normal state of
    /// the settings text box, not an error worth reporting.
    /// </summary>
    public static Color? ParseHex(string? text)
    {
        var trimmed = (text ?? string.Empty).Trim();
        if (trimmed.Length == 0) return null;
        if (trimmed[0] != '#' && trimmed.All(Uri.IsHexDigit)) trimmed = "#" + trimmed;

        try
        {
            if (ColorConverter.ConvertFromString(trimmed) is Color color)
                return Color.FromRgb(color.R, color.G, color.B);
        }
        catch (Exception)
        {
            // FormatException for bad hex, but ColorConverter is not documented to stop
            // there and a bad preference must never take the dialog down.
        }
        return null;
    }

    private static double Clamp(double value, double fallback) =>
        double.IsFinite(value) ? Math.Clamp(value, MinOpacity, 1.0) : fallback;
}
