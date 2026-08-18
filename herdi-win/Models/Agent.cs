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
    public Agent(string id, string name, AgentStatus status, string project, string cwd, string host = "local")
    {
        Id = id;
        _name = name;
        _status = status;
        _project = project;
        _cwd = cwd;
        _host = host;
    }

    public string Id { get; }

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
