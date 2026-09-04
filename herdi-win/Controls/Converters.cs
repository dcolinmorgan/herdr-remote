using System.Globalization;
using System.Windows;
using System.Windows.Data;
using System.Windows.Media;
using Herdi.Models;

namespace Herdi.Controls;

/// <summary>Palette shared by the island and the diff view.</summary>
public static class Palette
{
    public static readonly SolidColorBrush Red = Freeze(Color.FromRgb(0xFF, 0x45, 0x3A));
    public static readonly SolidColorBrush Green = Freeze(Color.FromRgb(0x32, 0xD7, 0x4B));
    public static readonly SolidColorBrush Gray = Freeze(Color.FromRgb(0x8E, 0x8E, 0x93));
    public static readonly SolidColorBrush Blue = Freeze(Color.FromRgb(0x0A, 0x84, 0xFF));
    public static readonly SolidColorBrush Cyan = Freeze(Color.FromRgb(0x64, 0xD2, 0xFF));
    public static readonly SolidColorBrush Orange = Freeze(Color.FromRgb(0xFF, 0x9F, 0x0A));

    private static SolidColorBrush Freeze(Color c)
    {
        var b = new SolidColorBrush(c);
        b.Freeze();
        return b;
    }

    public static SolidColorBrush White(double opacity)
    {
        var b = new SolidColorBrush(Colors.White) { Opacity = opacity };
        b.Freeze();
        return b;
    }

    /// <summary>Row accent per status, matching AgentSessionRow.accentColor on macOS.
    /// Done takes the orange the web app's ready bucket uses — a completion is yours to
    /// collect, which is not the resting grey an idle pane earns.</summary>
    public static SolidColorBrush ForStatus(AgentStatus status) => status switch
    {
        AgentStatus.Blocked => Red,
        AgentStatus.Working => Green,
        AgentStatus.Done => Orange,
        _ => Gray,
    };
}

public sealed class StatusToBrushConverter : IValueConverter
{
    public object Convert(object? value, Type targetType, object? parameter, CultureInfo culture) =>
        Palette.ForStatus(value is AgentStatus s ? s : AgentStatus.Unknown);

    public object ConvertBack(object? value, Type targetType, object? parameter, CultureInfo culture) =>
        throw new NotSupportedException();
}

public sealed class BoolToVisibilityConverter : IValueConverter
{
    /// <summary>Set Invert="True" in XAML to hide when true.</summary>
    public bool Invert { get; set; }

    public object Convert(object? value, Type targetType, object? parameter, CultureInfo culture)
    {
        var flag = value is true;
        if (Invert) flag = !flag;
        return flag ? Visibility.Visible : Visibility.Collapsed;
    }

    public object ConvertBack(object? value, Type targetType, object? parameter, CultureInfo culture) =>
        throw new NotSupportedException();
}

/// <summary>Visible when a collection or string is non-empty.</summary>
public sealed class NonEmptyToVisibilityConverter : IValueConverter
{
    public object Convert(object? value, Type targetType, object? parameter, CultureInfo culture)
    {
        var visible = value switch
        {
            null => false,
            string s => !string.IsNullOrWhiteSpace(s),
            System.Collections.ICollection c => c.Count > 0,
            int i => i > 0,
            _ => true,
        };
        return visible ? Visibility.Visible : Visibility.Collapsed;
    }

    public object ConvertBack(object? value, Type targetType, object? parameter, CultureInfo culture) =>
        throw new NotSupportedException();
}

/// <summary>
/// Colors one line of agent output as diff. Port of herdi-mac's DiffLine
/// (Sources/NotchContentView.swift:822). Pass "bg" as the converter parameter for
/// the row background, anything else for the foreground.
/// </summary>
public sealed class DiffLineBrushConverter : IValueConverter
{
    private enum LineType { Added, Removed, Hunk, Context }

    public object Convert(object? value, Type targetType, object? parameter, CultureInfo culture)
    {
        var type = Classify(value as string);
        var wantBackground = parameter as string == "bg";

        if (wantBackground)
        {
            return type switch
            {
                LineType.Added => Tint(Palette.Green.Color, 0.08),
                LineType.Removed => Tint(Palette.Red.Color, 0.08),
                LineType.Hunk => Tint(Palette.Cyan.Color, 0.03),
                _ => Brushes.Transparent,
            };
        }

        return type switch
        {
            LineType.Added => Palette.Green,
            LineType.Removed => Palette.Red,
            LineType.Hunk => Tint(Palette.Cyan.Color, 0.70),
            _ => Palette.White(0.60),
        };
    }

    private static LineType Classify(string? text)
    {
        var t = (text ?? string.Empty).Trim();
        if (t.StartsWith("+") && !t.StartsWith("+++")) return LineType.Added;
        if (t.StartsWith("-") && !t.StartsWith("---")) return LineType.Removed;
        if (t.StartsWith("@@")) return LineType.Hunk;
        return LineType.Context;
    }

    private static SolidColorBrush Tint(Color color, double opacity)
    {
        var b = new SolidColorBrush(color) { Opacity = opacity };
        b.Freeze();
        return b;
    }

    public object ConvertBack(object? value, Type targetType, object? parameter, CultureInfo culture) =>
        throw new NotSupportedException();
}
