using System.Windows;
using System.Windows.Controls;
using Herdi.Models;
using Herdi.ViewModels;

namespace Herdi.Views;

public partial class SessionListView : UserControl
{
    public SessionListView() => InitializeComponent();

    private IslandViewModel? Vm => DataContext as IslandViewModel;

    /// <summary>
    /// Clicking a blocked row opens its approval card, as onSelectAgent does in
    /// SessionListContent (NotchContentView.swift:294).
    /// </summary>
    private void OnRowClicked(object sender, System.Windows.Input.MouseButtonEventArgs e)
    {
        if (e.OriginalSource is FrameworkElement { DataContext: Agent agent })
        {
            Vm?.ShowApproval(agent);
        }
    }

    private async void OnInstallUpdate(object sender, RoutedEventArgs e)
    {
        if (Vm?.Updater is { } updater) await updater.PerformUpdateAsync();
    }
}
