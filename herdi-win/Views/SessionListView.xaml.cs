using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Input;
using System.Windows.Media;
using Herdi.Models;
using Herdi.ViewModels;

namespace Herdi.Views;

public partial class SessionListView : UserControl
{
    public SessionListView() => InitializeComponent();

    private IslandViewModel? Vm => DataContext as IslandViewModel;

    /// <summary>
    /// Clicking a row opens it — the approval card when it is blocked, its terminal
    /// otherwise. Every section is wired, not just NEEDS YOU: a list where most rows
    /// swallow a click is the reason the expanded island read as inert.
    /// </summary>
    private void OnRowClicked(object sender, MouseButtonEventArgs e)
    {
        if (e.OriginalSource is not DependencyObject source) return;

        // A row-level action button is not a row click. This is a Preview handler, so it
        // runs on the way down — before the button sees the release — and would otherwise
        // fire alongside every allow, interrupt and open press.
        for (var node = source; node is Visual; node = VisualTreeHelper.GetParent(node))
        {
            if (node is ButtonBase) return;
            if (node is ItemsControl) break;
        }

        if (source is FrameworkElement { DataContext: Agent agent }) Vm?.OpenAgent(agent);
    }

    // --- Row context menu. Its DataContext is the agent the menu was opened on.

    private static Agent? AgentOf(object sender) => (sender as FrameworkElement)?.DataContext as Agent;

    private void OnMenuAnswer(object sender, RoutedEventArgs e)
    {
        if (AgentOf(sender) is { } agent) Vm?.ShowApproval(agent);
    }

    private void OnMenuOpenPane(object sender, RoutedEventArgs e)
    {
        if (AgentOf(sender) is { } agent) Vm?.ShowPane(agent);
    }

    private void OnMenuInterrupt(object sender, RoutedEventArgs e)
    {
        if (AgentOf(sender) is { } agent) Vm?.InterruptCommand.Execute(agent);
    }

    private void OnMenuCopyPaneId(object sender, RoutedEventArgs e)
    {
        if (AgentOf(sender) is { } agent) Vm?.CopyPaneIdCommand.Execute(agent);
    }

    private async void OnInstallUpdate(object sender, RoutedEventArgs e)
    {
        if (Vm?.Updater is { } updater) await updater.PerformUpdateAsync();
    }
}
