using System.Collections.ObjectModel;
using System.Collections.Specialized;
using System.ComponentModel;
using System.Runtime.CompilerServices;
using System.Windows;
using System.Windows.Threading;
using Herdi.Models;
using Herdi.Services;

namespace Herdi.ViewModels;

/// <summary>Which face the island is showing. Mirrors herdi-mac's IslandSurface.</summary>
public enum IslandSurface
{
    Collapsed,
    SessionList,
    Approval,

    /// <summary>
    /// Live terminal for one agent. No macOS counterpart: there, a row opens the pane in
    /// the terminal app itself (`herdr workspace focus`), which is meaningless from a
    /// machine that is not the one running herdr — every agent may be an SSH hop away.
    /// Reading the pane and submitting to it work from anywhere, so that is what a row
    /// opens here.
    /// </summary>
    Pane,
}

/// <summary>
/// State behind the island. Corresponds to herdi-mac's NotchPanelView plus the
/// surface state machine that lives on its PanelWindowController.
/// </summary>
public sealed class IslandViewModel : INotifyPropertyChanged
{
    /// <summary>How often an open pane view re-reads its terminal — the relay's POLL_INTERVAL.</summary>
    private static readonly TimeSpan PaneRefreshInterval = TimeSpan.FromSeconds(2);

    /// <summary>Lines per read. The tail is what matters and every read costs a round trip.</summary>
    private const int PaneReadLines = 40;

    private readonly RelayConnection _relay;
    private readonly DispatcherTimer _paneTimer = new();

    public IslandViewModel(RelayConnection relay, Updater updater)
    {
        _relay = relay;
        Updater = updater;

        SelectAgentCommand = new RelayCommand(p => { if (p is Agent a) OpenAgent(a); });
        ShowPaneCommand = new RelayCommand(p => { if (p is Agent a) ShowPane(a); });
        RefreshPaneCommand = new RelayCommand(_ => RefreshPane());
        SendPaneInputCommand = new RelayCommand(_ => SendPaneInput());
        CopyPaneIdCommand = new RelayCommand(p => { if (p is Agent a) CopyToClipboard(a.Id); });
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

        _relay.PaneContentReceived += OnPaneContent;
        _paneTimer.Interval = PaneRefreshInterval;
        _paneTimer.Tick += (_, _) => RefreshPane();

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

    // Section visibility goes through these rather than binding a converter straight at
    // Blocked/Working/Idle: those properties hand back the same collection instance for
    // the app's lifetime, so such a binding is evaluated once — while every list is still
    // empty — and never again, leaving all three sections permanently collapsed.
    // Rebuild() raises them alongside the grouping itself.
    public bool HasBlocked => Blocked.Count > 0;
    public bool HasWorking => Working.Count > 0;
    public bool HasIdle => Idle.Count > 0;

    /// <summary>Tray tooltip / menu header text.</summary>
    public string StatusSummary => IsConnected
        ? $"● Connected · {AgentCount} agents"
        : "○ Disconnected";

    /// <summary>
    /// What state is being read from, shown under the status line: the relay URL, or the
    /// hosts direct mode polls.
    /// </summary>
    public string SourceSummary => _relay.Mode == ConnectionMode.Direct
        ? _relay.DescribeDirectSources()
        : _relay.HostAddress;

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
                return "Relay rejected the token — check Settings";
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
                OnPropertyChanged(nameof(IsSticky));
                OnPropertyChanged(nameof(ShowSessionListSurface));
                OnPropertyChanged(nameof(ShowApprovalSurface));
                OnPropertyChanged(nameof(ShowPaneSurface));
            }
        }
    }

    public bool IsExpanded => Surface != IslandSurface.Collapsed;
    public bool ShowSessionListSurface => Surface == IslandSurface.SessionList;
    public bool ShowApprovalSurface => Surface == IslandSurface.Approval && ActiveAgent is not null;
    public bool ShowPaneSurface => Surface == IslandSurface.Pane && ActiveAgent is not null;

    /// <summary>
    /// Surfaces the pointer must not close. Both are worked in rather than glanced at —
    /// answering a prompt or typing to an agent means leaving the island to reach for the
    /// keyboard, and a hover timer that shut it mid-sentence would be unusable.
    /// </summary>
    public bool IsSticky => Surface is IslandSurface.Approval or IslandSurface.Pane;

    private Agent? _activeAgent;
    public Agent? ActiveAgent
    {
        get => _activeAgent;
        private set
        {
            if (Set(ref _activeAgent, value))
            {
                OnPropertyChanged(nameof(ShowApprovalSurface));
                OnPropertyChanged(nameof(ShowPaneSurface));
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

    private string _paneContent = string.Empty;
    /// <summary>Terminal text of the agent the pane view is showing.</summary>
    public string PaneContent { get => _paneContent; private set => Set(ref _paneContent, value); }

    private string _paneInput = string.Empty;
    public string PaneInput
    {
        get => _paneInput;
        set
        {
            if (Set(ref _paneInput, value)) OnPropertyChanged(nameof(CanSendPaneInput));
        }
    }

    public bool CanSendPaneInput => !string.IsNullOrWhiteSpace(PaneInput);

    public RelayCommand SelectAgentCommand { get; }
    public RelayCommand ShowPaneCommand { get; }
    public RelayCommand RefreshPaneCommand { get; }
    public RelayCommand SendPaneInputCommand { get; }
    public RelayCommand CopyPaneIdCommand { get; }
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

    /// <summary>
    /// Raised when the agent grouping changed. The collapsed island's width and its
    /// working indicator depend on how many agents there are, which moves independently
    /// of the surface, so the window has to re-apply them here too.
    /// </summary>
    public event Action? GroupingChanged;

    // --- Surface transitions

    /// <summary>
    /// What a click on a row opens: the approval card when the agent is waiting on an
    /// answer, its terminal otherwise. macOS routes the second case to `onJump`, which
    /// focuses the pane in the terminal app on the machine running herdr — not something
    /// this client can do for an agent reached over SSH.
    /// </summary>
    public void OpenAgent(Agent agent)
    {
        if (agent.IsBlocked) ShowApproval(agent);
        else ShowPane(agent);
    }

    public void ShowApproval(Agent agent)
    {
        _paneTimer.Stop();
        ActiveAgent = agent;
        CustomReply = string.Empty;
        Surface = IslandSurface.Approval;
        SurfaceChanged?.Invoke();
    }

    /// <summary>Open one agent's terminal and keep it refreshing while it is on screen.</summary>
    public void ShowPane(Agent agent)
    {
        ActiveAgent = agent;
        PaneInput = string.Empty;
        PaneContent = string.Empty;
        Surface = IslandSurface.Pane;
        SurfaceChanged?.Invoke();
        RefreshPane();
        _paneTimer.Start();
    }

    public void ShowSessionList()
    {
        _paneTimer.Stop();
        ActiveAgent = null;
        Surface = IslandSurface.SessionList;
        SurfaceChanged?.Invoke();
    }

    public void Collapse()
    {
        _paneTimer.Stop();
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

    // --- Pane view

    private void RefreshPane()
    {
        if (Surface != IslandSurface.Pane || ActiveAgent is not { } agent)
        {
            _paneTimer.Stop();
            return;
        }
        _relay.ReadPane(agent, PaneReadLines);
    }

    /// <summary>
    /// A pane read came back. Both transports answer on this event, and a read in flight
    /// when the surface changed still arrives — hence the check that it is the pane still
    /// being shown.
    /// </summary>
    private void OnPaneContent(string paneId, string content)
    {
        if (Surface != IslandSurface.Pane || ActiveAgent?.Id != paneId) return;
        PaneContent = content.TrimEnd();
    }

    private void SendPaneInput()
    {
        if (ActiveAgent is not { } agent || !CanSendPaneInput) return;
        var text = PaneInput;
        PaneInput = string.Empty;
        _relay.SendPrompt(agent, text);
        // The submission takes a moment to show up in the pane; pull it early so the
        // echo appears without waiting out the refresh interval.
        RefreshPane();
    }

    private static void CopyToClipboard(string text)
    {
        try
        {
            Clipboard.SetText(text);
        }
        catch (Exception)
        {
            // Another process can hold the clipboard open; there is nothing to recover.
        }
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
        OnPropertyChanged(nameof(HasBlocked));
        OnPropertyChanged(nameof(HasWorking));
        OnPropertyChanged(nameof(HasIdle));
        OnPropertyChanged(nameof(StatusSummary));

        // A pane that closed while it was on screen has nothing left to read, and the
        // reads would keep going out every couple of seconds against an id neither
        // transport still knows.
        if (Surface == IslandSurface.Pane &&
            ActiveAgent is { } active &&
            !_relay.Agents.Contains(active))
        {
            ShowSessionList();
        }

        GroupingChanged?.Invoke();
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
