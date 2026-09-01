using System.Collections.ObjectModel;
using System.ComponentModel;
using System.Runtime.CompilerServices;

namespace Herdi.Models;

public enum AgentStatus
{
    Working,
    Blocked,
    Idle,
    Unknown,
}

public static class AgentStatusParser
{
    public static AgentStatus Parse(string? raw) => raw?.ToLowerInvariant() switch
    {
        "working" => AgentStatus.Working,
        "blocked" => AgentStatus.Blocked,
        "idle" => AgentStatus.Idle,
        _ => AgentStatus.Unknown,
    };
}

/// <summary>
/// One herdr pane running an agent. Mirrors herdi-mac's Agent (Sources/Agent.swift).
/// </summary>
public sealed class Agent : INotifyPropertyChanged
{
    /// <summary>
    /// Separates the source key from the pane id in <see cref="Id"/>. A vertical bar cannot
    /// occur in either half — a herdr pane id is `w&lt;n&gt;:p&lt;n&gt;` and a source key is a
    /// WebSocket URL or the literal "direct" — so the composite never has to be un-parsed.
    /// </summary>
    private const char SourceSeparator = '|';

    public Agent(
        string paneId,
        string name,
        AgentStatus status,
        string project,
        string cwd,
        string host = "local",
        string sourceId = DirectSource,
        string sourceLabel = "")
    {
        PaneId = paneId;
        SourceId = sourceId;
        Id = ComposeId(sourceId, paneId);
        _name = name;
        _status = status;
        _project = project;
        _cwd = cwd;
        _host = host;
        _sourceLabel = sourceLabel;
    }

    /// <summary>Source key for direct mode, which is a single source by construction.</summary>
    public const string DirectSource = "direct";

    /// <summary>
    /// The <see cref="Id"/> a pane would have, without needing an Agent to ask. A
    /// `pane_content` reply can arrive for a pane no snapshot has introduced yet, and
    /// dropping the read for want of a row would leave the pane view on "Reading pane…"
    /// until the next tick.
    /// </summary>
    public static string ComposeId(string sourceId, string paneId) =>
        sourceId + SourceSeparator + paneId;

    /// <summary>
    /// Unique across every source this client is watching, which is why it is composite:
    /// every herdr numbers its own panes, so two relays routinely both report a `w1:p1` and
    /// a flat pane id would make them the same row. This is the collection identity, what
    /// RelayConnection.Find matches, and what a toast carries so its
    /// buttons come back to the right agent. It is never put on the wire — see
    /// <see cref="PaneId"/>.
    /// </summary>
    public string Id { get; }

    /// <summary>
    /// The pane id as the owning source knows it, i.e. what goes out in a relay message or
    /// a herdr CLI argument. In direct mode this still carries the host prefix
    /// HerdrPoller.ParsePanes puts on a remote pane, and HerdrPoller.PaneIdOf takes it off.
    /// </summary>
    public string PaneId { get; }

    /// <summary>Which source reported this pane: a relay URL, or <see cref="DirectSource"/>.</summary>
    public string SourceId { get; }

    private string _sourceLabel;

    /// <summary>
    /// Short name of that source for display — a relay URL's authority. Mutable because the
    /// same pane keeps its identity across a settings save that renames nothing but the
    /// label it is shown under.
    /// </summary>
    public string SourceLabel { get => _sourceLabel; set => Set(ref _sourceLabel, value); }

    private bool _showSource;

    /// <summary>
    /// Whether a row should print <see cref="SourceLabel"/>. False while there is only one
    /// relay, which is the ordinary case: naming the only source there is says nothing and
    /// costs a line of a 580px card.
    /// </summary>
    public bool ShowSource { get => _showSource; set => Set(ref _showSource, value); }

    private string _name;
    public string Name { get => _name; set => Set(ref _name, value); }

    private AgentStatus _status;
    public AgentStatus Status
    {
        get => _status;
        set
        {
            if (Set(ref _status, value))
            {
                OnPropertyChanged(nameof(IsBlocked));
                OnPropertyChanged(nameof(IsWorking));
                OnPropertyChanged(nameof(CanInterrupt));
            }
        }
    }

    private string _project;
    public string Project { get => _project; set => Set(ref _project, value); }

    private string _cwd;
    public string Cwd { get => _cwd; set => Set(ref _cwd, value); }

    private string _host;
    public string Host
    {
        get => _host;
        set
        {
            if (Set(ref _host, value)) OnPropertyChanged(nameof(IsRemote));
        }
    }

    private string? _prompt;
    public string? Prompt
    {
        get => _prompt;
        set
        {
            if (Set(ref _prompt, value))
            {
                OnPropertyChanged(nameof(PromptLines));
                OnPropertyChanged(nameof(PromptTail));
            }
        }
    }

    private string? _promptId;
    public string? PromptId { get => _promptId; set => Set(ref _promptId, value); }

    private IReadOnlyList<string>? _options;
    public IReadOnlyList<string>? Options { get => _options; set => Set(ref _options, value); }

    private string? _interaction;
    public string? Interaction { get => _interaction; set => Set(ref _interaction, value); }

    private bool _isMultiSelect;
    public bool IsMultiSelect { get => _isMultiSelect; set => Set(ref _isMultiSelect, value); }

    public ObservableCollection<string> MultiOptions { get; } = new();
    public ObservableCollection<string> SelectedOptions { get; } = new();

    public bool IsBlocked => Status == AgentStatus.Blocked;
    public bool IsWorking => Status == AgentStatus.Working;
    public bool IsRemote => !string.Equals(Host, "local", StringComparison.OrdinalIgnoreCase);

    /// <summary>
    /// ^C is offered on the agents it can actually stop. An idle pane has nothing to
    /// interrupt, and AgentSessionRow hides the button there too (NotchContentView.swift:500).
    /// </summary>
    public bool CanInterrupt => Status is AgentStatus.Working or AgentStatus.Blocked;

    /// <summary>Display label: project when known, else the raw cwd.</summary>
    public string DisplayLocation => string.IsNullOrEmpty(Project) ? Cwd : Project;

    public IReadOnlyList<string> PromptLines =>
        string.IsNullOrEmpty(Prompt)
            ? Array.Empty<string>()
            : Prompt.Replace("\r\n", "\n").Split('\n');

    /// <summary>Last non-empty prompt line — shown inline on a blocked row.</summary>
    public string PromptTail
    {
        get
        {
            var lines = PromptLines;
            for (var i = lines.Count - 1; i >= 0; i--)
            {
                if (!string.IsNullOrWhiteSpace(lines[i])) return lines[i].Trim();
            }
            return string.Empty;
        }
    }

    /// <summary>Clear prompt state after a response is sent.</summary>
    public void ClearPrompt()
    {
        Prompt = null;
        PromptId = null;
        Options = null;
        Interaction = null;
        IsMultiSelect = false;
        MultiOptions.Clear();
        SelectedOptions.Clear();
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
