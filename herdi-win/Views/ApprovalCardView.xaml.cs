using System.Windows.Controls;
using System.Windows.Input;
using Herdi.ViewModels;

namespace Herdi.Views;

public partial class ApprovalCardView : UserControl
{
    public ApprovalCardView() => InitializeComponent();

    private IslandViewModel? Vm => DataContext as IslandViewModel;

    /// <summary>Enter sends, matching the TextField's onSubmit on macOS.</summary>
    private void OnReplyKeyDown(object sender, KeyEventArgs e)
    {
        if (e.Key != Key.Enter) return;
        e.Handled = true;
        if (Vm is { CanSendCustomReply: true } vm) vm.SendCustomReplyCommand.Execute(null);
    }

    /// <summary>Focus the reply box when the card appears so typing just works.</summary>
    public void FocusReply() => ReplyInput.Focus();
}
