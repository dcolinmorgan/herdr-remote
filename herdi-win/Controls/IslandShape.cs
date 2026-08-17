using System.Windows;
using System.Windows.Media;
using System.Windows.Shapes;

namespace Herdi.Controls;

/// <summary>
/// The island's silhouette: a flat top edge flush with the screen, shoulder curves
/// flaring outward, and squircle corners at the bottom.
///
/// Direct port of herdi-mac's NotchPanelShape (Sources/NotchContentView.swift:883),
/// including its k = 0.62 continuous-curvature factor. On macOS the shoulders let the
/// panel melt into the physical notch; here they give the same Dynamic Island read of
/// something growing out of the top edge of the display.
/// </summary>
public sealed class IslandShape : Shape
{
    /// <summary>Apple-style continuous curvature factor for the bottom corners.</summary>
    private const double K = 0.62;

    public static readonly DependencyProperty TopExtensionProperty = DependencyProperty.Register(
        nameof(TopExtension), typeof(double), typeof(IslandShape),
        new FrameworkPropertyMetadata(3.0, FrameworkPropertyMetadataOptions.AffectsRender));

    public static readonly DependencyProperty BottomRadiusProperty = DependencyProperty.Register(
        nameof(BottomRadius), typeof(double), typeof(IslandShape),
        new FrameworkPropertyMetadata(12.0, FrameworkPropertyMetadataOptions.AffectsRender));

    /// <summary>How far the shoulders flare past the body, in DIPs.</summary>
    public double TopExtension
    {
        get => (double)GetValue(TopExtensionProperty);
        set => SetValue(TopExtensionProperty, value);
    }

    public double BottomRadius
    {
        get => (double)GetValue(BottomRadiusProperty);
        set => SetValue(BottomRadiusProperty, value);
    }

    protected override Geometry DefiningGeometry => BuildGeometry(RenderSize);

    // The silhouette is painted behind the content and sized by it, so it must not
    // contribute to measurement (returning zero) and must fill whatever it is given.
    protected override Size MeasureOverride(Size constraint) => new(0, 0);

    protected override Size ArrangeOverride(Size finalSize) => finalSize;

    internal Geometry BuildGeometry(Size size)
    {
        var ext = Math.Max(0, TopExtension);
        var width = size.Width;
        var height = size.Height;
        if (width <= 0 || height <= 0) return Geometry.Empty;

        // The shoulders occupy `ext` on each side, so the body is inset by that much.
        var minX = ext;
        var maxX = Math.Max(minX, width - ext);
        var minY = 0.0;
        var maxY = height;

        var bodyWidth = maxX - minX;
        var br = Math.Min(Math.Min(BottomRadius, bodyWidth / 4), (maxY - minY) / 2);
        br = Math.Max(0, br);

        var geometry = new StreamGeometry();
        using (var ctx = geometry.Open())
        {
            // Top edge, running shoulder to shoulder.
            ctx.BeginFigure(new Point(minX - ext, minY), isFilled: true, isClosed: true);
            ctx.LineTo(new Point(maxX + ext, minY), isStroked: true, isSmoothJoin: false);

            // Right shoulder: top edge easing down into the side.
            ctx.BezierTo(
                new Point(maxX + ext * 0.35, minY),
                new Point(maxX, minY + ext * 0.35),
                new Point(maxX, minY + ext),
                isStroked: true, isSmoothJoin: true);

            ctx.LineTo(new Point(maxX, maxY - br), isStroked: true, isSmoothJoin: false);

            // Bottom-right squircle.
            ctx.BezierTo(
                new Point(maxX, maxY - br * (1 - K)),
                new Point(maxX - br * (1 - K), maxY),
                new Point(maxX - br, maxY),
                isStroked: true, isSmoothJoin: true);

            ctx.LineTo(new Point(minX + br, maxY), isStroked: true, isSmoothJoin: false);

            // Bottom-left squircle.
            ctx.BezierTo(
                new Point(minX + br * (1 - K), maxY),
                new Point(minX, maxY - br * (1 - K)),
                new Point(minX, maxY - br),
                isStroked: true, isSmoothJoin: true);

            ctx.LineTo(new Point(minX, minY + ext), isStroked: true, isSmoothJoin: false);

            // Left shoulder.
            ctx.BezierTo(
                new Point(minX, minY + ext * 0.35),
                new Point(minX - ext * 0.35, minY),
                new Point(minX - ext, minY),
                isStroked: true, isSmoothJoin: true);
        }

        geometry.Freeze();
        return geometry;
    }
}
