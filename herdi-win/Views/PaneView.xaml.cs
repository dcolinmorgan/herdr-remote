using System.Windows.Controls;
using System.Windows.Data;
using System.Windows.Input;
using Herdi.ViewModels;

namespace Herdi.Views;

public partial class PaneView : UserControl
{
    public PaneView() => InitializeComponent();

    private IslandViewModel? Vm => DataContext as IslandViewModel;

    /// <summary>Enter submits, as it does on the approval card's reply box.</summary>
    private void OnInputKeyDown(object sender, KeyEventArgs e)
    {
        if (e.Key != Key.Enter) return;
        e.Handled = true;
        if (Vm is { CanSendPaneInput: true } vm) vm.SendPaneInputCommand.Execute(null);
    }

    /// <summary>
    /// Follow the tail of the pane, which is where an agent writes. Only while the reader
    /// is already at the bottom: the offsets still describe the previous content here, so
    /// a manual scroll up is detected and survives the refresh two seconds later.
    /// </summary>
    private void OnPaneContentUpdated(object sender, DataTransferEventArgs e)
    {
        if (PaneScroll.VerticalOffset >= PaneScroll.ScrollableHeight - 2) PaneScroll.ScrollToEnd();
    }

    /// <summary>Focus the input when the surface appears, so typing just works.</summary>
    public void FocusInput() => MessageInput.Focus();
}
