using System.Collections.ObjectModel;
using System.Collections.Specialized;
using System.ComponentModel;
using System.Runtime.CompilerServices;
using Herdi.Models;
using Herdi.Services;

namespace Herdi.ViewModels;

/// <summary>Which face the island is showing. Mirrors herdi-mac's IslandSurface.</summary>
public enum IslandSurface
{
    Collapsed,
    SessionList,
    Approval,
}

/// <summary>
/// State behind the island. Corresponds to herdi-mac's NotchPanelView plus the
/// surface state machine that lives on its PanelWindowController.
/// </summary>
public sealed class IslandViewModel : INotifyPropertyChanged
{
    private readonly RelayConnection _relay;

    public IslandViewModel(RelayConnection relay, Updater updater)
    {
        _relay = relay;
        Updater = updater;

        SelectAgentCommand = new RelayCommand(p => { if (p is Agent a) ShowApproval(a); });
        DismissCommand = new RelayCommand(_ => ShowSessionList());
        ShowSessionListCommand = new RelayCommand(_ => ShowSessionList());
        RespondCommand = new RelayCommand(p =>
        {
            if (ActiveAgent is { } agent && p is string text) Respond(agent, text);
        });
        RespondFromRowCommand = new RelayCommand(p =>
        {
            // The row-level Allow button always answers with the permission grant.
            if (p is Agent agent) Respond(agent, "yes, single permission");
        });
        SendCustomReplyCommand = new RelayCommand(_ => SendCustomReply());
        InterruptCommand = new RelayCommand(p =>
        {
            if (p is Agent agent) _relay.Interrupt(agent);
        });
        ToggleOptionCommand = new RelayCommand(p =>
        {
            if (ActiveAgent is not { } agent || p is not MultiOption option) return;
            _relay.ToggleQuestionOption(agent, option.Option);
            option.IsSelected = agent.SelectedOptions.Contains(option.Option);
        });
        SubmitQuestionCommand = new RelayCommand(_ =>
        {
            if (ActiveAgent is { } agent)
            {
                _relay.SubmitQuestion(agent);
                ShowSessionList();
            }
        });

        _relay.Agents.CollectionChanged += OnAgentsChanged;
        _relay.PropertyChanged += (_, e) =>
        {
            if (e.PropertyName is nameof(RelayConnection.IsConnected))
            {
                OnPropertyChanged(nameof(IsConnected));
                OnPropertyChanged(nameof(StatusSummary));
                OnPropertyChanged(nameof(ConnectionError));
            }
            else if (e.PropertyName is nameof(RelayConnection.LastError))
            {
                OnPropertyChanged(nameof(ConnectionError));
            }
        };
        Rebuild();
    }

    public Updater Updater { get; }

    public ObservableCollection<Agent> Blocked { get; } = new();
    public ObservableCollection<Agent> Working { get; } = new();
    public ObservableCollection<Agent> Idle { get; } = new();

    public bool IsConnected => _relay.IsConnected;
    public int AgentCount => _relay.Agents.Count;
    public bool IsActive => _relay.Agents.Count > 0;
    public bool HasNoAgents => _relay.Agents.Count == 0;

    /// <summary>Tray tooltip / menu header text.</summary>
    public string StatusSummary => IsConnected
        ? $"● Connected · {AgentCount} agents"
        : "○ Disconnected";

    /// <summary>
    /// Why the relay is unreachable, for the tray menu; null while connected or when the
    /// failure is unknown. A token-guarded relay refuses the handshake with a 401 and is
    /// otherwise indistinguishable from an unreachable one, so that case gets named
    /// outright instead of showing the bare WebSocket exception.
    /// </summary>
    public string? ConnectionError
    {
        get
        {
            if (IsConnected) return null;
            var error = _relay.LastError;
            if (string.IsNullOrWhiteSpace(error)) return null;
            if (error.Contains("401") || error.Contains("403"))
                return "Relay rejected the token — check Relay Settings";
            return error.Length > 70 ? error[..70] + "…" : error;
        }
    }

    private IslandSurface _surface = IslandSurface.Collapsed;
    public IslandSurface Surface
    {
        get => _surface;
        private set
        {
            if (Set(ref _surface, value))
            {
                OnPropertyChanged(nameof(IsExpanded));
                OnPropertyChanged(nameof(ShowSessionListSurface));
                OnPropertyChanged(nameof(ShowApprovalSurface));
            }
        }
    }

    public bool IsExpanded => Surface != IslandSurface.Collapsed;
    public bool ShowSessionListSurface => Surface == IslandSurface.SessionList;
    public bool ShowApprovalSurface => Surface == IslandSurface.Approval && ActiveAgent is not null;

    private Agent? _activeAgent;
    public Agent? ActiveAgent
    {
        get => _activeAgent;
        private set
        {
            if (Set(ref _activeAgent, value))
            {
                OnPropertyChanged(nameof(ShowApprovalSurface));
                RefreshResponseButtons();
            }
        }
    }

    public ObservableCollection<ResponseAction> ResponseButtons { get; } = new();

    /// <summary>Checkboxes for a multi-select question, rebuilt with the active agent.</summary>
    public ObservableCollection<MultiOption> MultiOptions { get; } = new();

    private string _customReply = string.Empty;
    public string CustomReply
    {
        get => _customReply;
        set
        {
            if (Set(ref _customReply, value)) OnPropertyChanged(nameof(CanSendCustomReply));
        }
    }

    public bool CanSendCustomReply => !string.IsNullOrWhiteSpace(CustomReply);

    public RelayCommand SelectAgentCommand { get; }
    public RelayCommand DismissCommand { get; }
    public RelayCommand ShowSessionListCommand { get; }
    public RelayCommand RespondCommand { get; }
    public RelayCommand RespondFromRowCommand { get; }
    public RelayCommand SendCustomReplyCommand { get; }
    public RelayCommand InterruptCommand { get; }
    public RelayCommand ToggleOptionCommand { get; }
    public RelayCommand SubmitQuestionCommand { get; }

    /// <summary>Raised when the surface changes so the window can resize and animate.</summary>
    public event Action? SurfaceChanged;

    // --- Surface transitions

    public void ShowApproval(Agent agent)
    {
        ActiveAgent = agent;
        CustomReply = string.Empty;
        Surface = IslandSurface.Approval;
        SurfaceChanged?.Invoke();
    }

    public void ShowSessionList()
    {
        ActiveAgent = null;
        Surface = IslandSurface.SessionList;
        SurfaceChanged?.Invoke();
    }

    public void Collapse()
    {
        ActiveAgent = null;
        Surface = IslandSurface.Collapsed;
        SurfaceChanged?.Invoke();
    }

    /// <summary>
    /// Auto-open the approval card for a newly blocked agent — the counterpart of
    /// herdi-mac's observeBlockedAgents auto-pop (Sources/HerdiMacApp.swift:180).
    /// </summary>
    public void PopApproval(Agent agent)
    {
        if (Surface == IslandSurface.Collapsed) ShowApproval(agent);
    }

    private void Respond(Agent agent, string text)
    {
        _relay.Respond(agent, text);
        if (ReferenceEquals(agent, ActiveAgent)) ShowSessionList();
    }

    private void SendCustomReply()
    {
        if (ActiveAgent is not { } agent || !CanSendCustomReply) return;
        var text = CustomReply.Trim();
        CustomReply = string.Empty;
        // Free-form text is not in the relay's SAFE_RESPONSES allowlist, so
        // RelayConnection.Respond routes it through agent_prompt instead.
        _relay.Respond(agent, text);
        ShowSessionList();
    }

    private void RefreshResponseButtons()
    {
        ResponseButtons.Clear();
        if (ActiveAgent?.Options is { } options)
        {
            foreach (var option in options) ResponseButtons.Add(ResponseActionMapper.Map(option));
        }

        MultiOptions.Clear();
        if (ActiveAgent is { } agent)
        {
            foreach (var option in agent.MultiOptions)
            {
                MultiOptions.Add(new MultiOption(option, agent.SelectedOptions.Contains(option)));
            }
        }
    }

    // --- Grouping

    private void OnAgentsChanged(object? sender, NotifyCollectionChangedEventArgs e)
    {
        if (e.OldItems is not null)
        {
            foreach (Agent agent in e.OldItems) agent.PropertyChanged -= OnAgentPropertyChanged;
        }
        if (e.NewItems is not null)
        {
            foreach (Agent agent in e.NewItems) agent.PropertyChanged += OnAgentPropertyChanged;
        }
        Rebuild();
    }

    private void OnAgentPropertyChanged(object? sender, PropertyChangedEventArgs e)
    {
        if (e.PropertyName is nameof(Agent.Status))
        {
            Rebuild();
            // The active agent may have been answered elsewhere; fall back to the list.
            if (ActiveAgent is { } active && active.Status != AgentStatus.Blocked && Surface == IslandSurface.Approval)
            {
                ShowSessionList();
            }
        }
        else if (e.PropertyName is nameof(Agent.Options) && ReferenceEquals(sender, ActiveAgent))
        {
            RefreshResponseButtons();
        }
    }

    private void Rebuild()
    {
        Sync(Blocked, _relay.Agents.Where(a => a.Status == AgentStatus.Blocked));
        Sync(Working, _relay.Agents.Where(a => a.Status == AgentStatus.Working));
        Sync(Idle, _relay.Agents.Where(a => a.Status is AgentStatus.Idle or AgentStatus.Unknown));

        OnPropertyChanged(nameof(AgentCount));
        OnPropertyChanged(nameof(IsActive));
        OnPropertyChanged(nameof(HasNoAgents));
        OnPropertyChanged(nameof(StatusSummary));
    }

    /// <summary>Reconcile in place so WPF keeps row identity (and hover state) stable.</summary>
    private static void Sync(ObservableCollection<Agent> target, IEnumerable<Agent> source)
    {
        var desired = source.ToList();
        for (var i = target.Count - 1; i >= 0; i--)
        {
            if (!desired.Contains(target[i])) target.RemoveAt(i);
        }
        for (var i = 0; i < desired.Count; i++)
        {
            var agent = desired[i];
            var existing = target.IndexOf(agent);
            if (existing < 0) target.Insert(Math.Min(i, target.Count), agent);
            else if (existing != i) target.Move(existing, i);
        }
    }

    public event PropertyChangedEventHandler? PropertyChanged;

    private bool Set<T>(ref T field, T value, [CallerMemberName] string? name = null)
    {
        if (EqualityComparer<T>.Default.Equals(field, value)) return false;
        field = value;
        OnPropertyChanged(name);
        return true;
    }

    private void OnPropertyChanged([CallerMemberName] string? name = null) =>
        PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(name));
}
