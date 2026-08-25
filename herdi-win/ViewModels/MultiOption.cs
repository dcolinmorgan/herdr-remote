using System.ComponentModel;

namespace Herdi.ViewModels;

/// <summary>
/// One checkbox of a multi-select question. Wraps the raw option string so the
/// checked state is observable — on macOS the view recomputes
/// agent.selectedOptions.contains(option) on every render instead.
/// </summary>
public sealed class MultiOption(string option, bool isSelected) : INotifyPropertyChanged
{
    public string Option { get; } = option;

    private bool _isSelected = isSelected;
    public bool IsSelected
    {
        get => _isSelected;
        set
        {
            if (_isSelected == value) return;
            _isSelected = value;
            PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(nameof(IsSelected)));
            PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(nameof(Glyph)));
        }
    }

    public string Glyph => IsSelected ? "☑" : "☐";

    public event PropertyChangedEventHandler? PropertyChanged;
}
