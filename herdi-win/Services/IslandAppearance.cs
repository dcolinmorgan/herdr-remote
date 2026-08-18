using System.Windows.Media;

namespace Herdi.Services;

/// <summary>
/// How the flyout paints itself: the card colour and how see-through it is. herdi-mac has
/// no equivalent — its panel hides inside the notch, where pure black at full opacity is
/// the only sensible answer. Here the card is a surface of its own above the tray, so its
/// colour is worth a preference; the dark default is what the rest of the styling assumes,
/// since every foreground in Themes/Styles.xaml is white at some opacity.
/// </summary>
/// <param name="Fill">Card colour. Always opaque: transparency is
/// <paramref name="Opacity"/>'s job, and an alpha here would compound with it.</param>
/// <param name="Opacity">The whole card, text included.</param>
public readonly record struct IslandAppearance(Color Fill, double Opacity)
{
    /// <summary>
    /// Floor for the slider. Below this the text is unreadable — and since the settings
    /// dialog is reached through the tray menu rather than through the flyout, an invisible
    /// flyout would not be unrecoverable, only annoying. The clamp keeps a hand-edited
    /// settings.json inside the same range.
    /// </summary>
    public const double MinOpacity = 0.2;

    /// <summary>
    /// Opaque by default. A flyout is a surface the user summoned and is reading, not
    /// signage sitting over someone else's window, so there is nothing to see through it
    /// for — but a translucent card over a wallpaper is a reasonable thing to want.
    /// </summary>
    public const double DefaultOpacity = 1.0;

    /// <summary>Pure black, as herdi-mac fills the notch shape with.</summary>
    public static Color DefaultFill => Colors.Black;

    public static IslandAppearance Default => new(DefaultFill, DefaultOpacity);

    /// <summary>Drop any alpha and pull the opacity back into range.</summary>
    public IslandAppearance Normalized() => new(
        Color.FromRgb(Fill.R, Fill.G, Fill.B),
        double.IsFinite(Opacity) ? Math.Clamp(Opacity, MinOpacity, 1.0) : DefaultOpacity);

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
}
