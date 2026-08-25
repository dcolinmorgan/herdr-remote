using System.Windows.Media;
using Herdi.Controls;

namespace Herdi.ViewModels;

/// <summary>
/// One button in the approval card's response row. The four brushes are precomputed
/// so the XAML can bind them directly — herdi-mac derives them inline with
/// tint.opacity(...) per state (NotchContentView.swift:803).
/// </summary>
public sealed class ResponseAction
{
    public ResponseAction(string label, string glyph, SolidColorBrush tint, string? shortcut, string rawValue)
    {
        Label = label;
        Glyph = glyph;
        Tint = tint;
        Shortcut = shortcut;
        RawValue = rawValue;

        FillNormal = Tinted(tint.Color, 0.08);
        FillHover = Tinted(tint.Color, 0.25);
        StrokeNormal = Tinted(tint.Color, 0.20);
        StrokeHover = Tinted(tint.Color, 0.50);
        ShortcutBrush = Tinted(tint.Color, 0.50);
    }

    public string Label { get; }
    public string Glyph { get; }
    public SolidColorBrush Tint { get; }
    public string? Shortcut { get; }
    public string RawValue { get; }

    public SolidColorBrush FillNormal { get; }
    public SolidColorBrush FillHover { get; }
    public SolidColorBrush StrokeNormal { get; }
    public SolidColorBrush StrokeHover { get; }
    public SolidColorBrush ShortcutBrush { get; }

    private static SolidColorBrush Tinted(Color color, double opacity)
    {
        var brush = new SolidColorBrush(color) { Opacity = opacity };
        brush.Freeze();
        return brush;
    }
}

public static class ResponseActionMapper
{
    /// <summary>
    /// Turn a raw herdr option string into a labelled button. Straight port of
    /// herdi-mac's ResponseButtonGrid.mapOption (Sources/NotchContentView.swift:719),
    /// with ⌘ shortcuts rewritten as Ctrl and SF Symbols replaced by plain Unicode —
    /// text symbols render identically on Windows 10 and 11, unlike the Segoe icon
    /// fonts, whose glyph coverage differs between them.
    /// </summary>
    public static ResponseAction Map(string option)
    {
        var lower = option.ToLowerInvariant();

        // Permission responses
        if (lower.Contains("single permission") || lower is "y" or "yes")
            return new ResponseAction("Allow", "✓", Palette.Green, "Ctrl+Y", option);
        if (lower.Contains("always allow") || lower.Contains("trust"))
            return new ResponseAction("Trust", "⛨", Palette.Blue, "Ctrl+T", option);
        if (lower.Contains("tab to edit") || lower.StartsWith("no") || lower == "n")
            return new ResponseAction("Deny", "✕", Palette.Red, "Ctrl+N", option);

        // Batch responses
        if (lower.Contains("approve all"))
            return new ResponseAction("Approve All", "✓", Palette.Green, "Ctrl+A", option);
        if (lower.Contains("configure individually"))
            return new ResponseAction("Configure", "⚙", Palette.Orange, null, option);

        // Flow control
        if (lower.Contains("continue") || lower.Contains("proceed"))
            return new ResponseAction("Continue", "▶", Palette.Green, "Ctrl+Enter", option);
        if (lower.Contains("edit") || lower.Contains("modify"))
            return new ResponseAction("Edit", "✎", Palette.Orange, "Ctrl+E", option);
        if (lower.Contains("retry") || lower.Contains("again"))
            return new ResponseAction("Retry", "⟳", Palette.Blue, "Ctrl+R", option);
        if (lower.Contains("skip"))
            return new ResponseAction("Skip", "»", Palette.Gray, null, option);
        if (lower.Contains("exit") || lower.Contains("cancel") || lower.Contains("abort"))
            return new ResponseAction("Cancel", "⊘", Palette.Red, "Ctrl+.", option);

        // Fallback
        var shortLabel = option.Length > 16 ? option[..16] : option;
        return new ResponseAction(shortLabel, "•", Palette.White(0.6), null, option);
    }
}
