using System.Collections.Specialized;
using System.IO;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using System.Web;
using Herdi.Models;
using Microsoft.Win32;
using Windows.Data.Xml.Dom;
using Windows.UI.Notifications;

namespace Herdi.Services;

/// <summary>What the user picked on a toast.</summary>
public sealed record ToastAction(string Kind, string PaneId, string? Text);

/// <summary>
/// Windows toast notifications for blocked agents.
///
/// This goes beyond herdi-mac's sendNotification (Sources/RelayConnection.swift:433),
/// which posts title + body + sound and nothing else: the toast here carries the
/// permission buttons and a reply box, so a prompt can be answered without opening
/// the island at all.
/// </summary>
public sealed class ToastService : IDisposable
{
    /// <summary>Identity backed by the Start Menu shortcut ShortcutHelper writes.</summary>
    public const string Aumid = "dcolinmorgan.Herdi.Win";

    /// <summary>COM class Windows instantiates when a toast button is pressed.</summary>
    public static readonly Guid ActivatorClsid = new("B6B4B0C1-6E2A-4F1D-9A73-2C7E5D8F4A21");

    private const string ToastTag = "herdr-blocked";
    private const string ToastGroup = "herdr";

    private readonly SettingsStore _settings;
    private uint _comRegistration;
    private bool _registered;

    public ToastService(SettingsStore settings) => _settings = settings;

    /// <summary>Raised on the UI thread when a toast button or reply is submitted.</summary>
    public event Action<ToastAction>? ActionInvoked;

    /// <summary>Non-null when notifications could not be set up (surfaced in the tray menu).</summary>
    public string? SetupError { get; private set; }

    /// <summary>
    /// Non-null when Windows itself is refusing to show notifications. Nothing failed in
    /// that case — the OS is simply configured not to show them — so it is kept apart from
    /// <see cref="SetupError"/>, which means something broke.
    /// </summary>
    public string? DeliveryBlocked { get; private set; }

    /// <summary>Whatever is currently stopping notifications, for the tray menu to show.</summary>
    public string? Problem => SetupError ?? DeliveryBlocked;

    public bool Initialize()
    {
        try
        {
            if (!ShortcutHelper.EnsureShortcut(Aumid, ActivatorClsid, "Herdi"))
            {
                SetupError = "Could not create the Start Menu shortcut; toasts are unavailable.";
                Log("registration failed: no Start Menu shortcut");
                return false;
            }
            _settings.ShortcutInstalled = true;

            RegisterActivatorClsid();
            RegisterClassObject();
            NotificationActivator.Handler = OnActivated;
            _registered = true;
            Log($"registered: aumid={Aumid} exe={Environment.ProcessPath}");
            return true;
        }
        catch (Exception ex)
        {
            SetupError = $"{ex.Message} (0x{ex.HResult:X8})";
            Log($"registration failed: {ex.GetType().Name}: {SetupError}");
            return false;
        }
    }

    /// <summary>
    /// Point the activator CLSID at this exe. Windows requires the LocalServer32 entry
    /// to exist even when the running process already handles activation in-proc.
    /// </summary>
    private static void RegisterActivatorClsid()
    {
        var exePath = Environment.ProcessPath;
        if (string.IsNullOrEmpty(exePath)) return;
        var keyPath = $@"Software\Classes\CLSID\{{{ActivatorClsid}}}\LocalServer32";
        using var key = Registry.CurrentUser.CreateSubKey(keyPath);
        key?.SetValue(null, $"\"{exePath}\" --toast-activated");
    }

    /// <summary>
    /// Keep activation inside this process so a button press reaches the live
    /// WebSocket instead of spawning a second copy of the app.
    /// </summary>
    private void RegisterClassObject()
    {
        var factory = new ClassFactory();
        var clsid = ActivatorClsid;
        var hr = CoRegisterClassObject(
            ref clsid, factory, ClsCtxLocalServer, RegClsMultipleUse | RegClsSuspended, out _comRegistration);
        if (hr < 0) Marshal.ThrowExceptionForHR(hr);
        hr = CoResumeClassObjects();
        if (hr < 0) Marshal.ThrowExceptionForHR(hr);
    }

    private static void Log(string message) => DiagnosticLog.Write("toast: " + message);

    private void OnActivated(string args, IReadOnlyDictionary<string, string> input)
    {
        var parsed = HttpUtility.ParseQueryString(args);
        var kind = parsed["action"] ?? "open";
        var paneId = parsed["pane"] ?? string.Empty;
        var text = parsed["text"];

        if (kind == "reply")
        {
            input.TryGetValue("reply", out var typed);
            if (string.IsNullOrWhiteSpace(typed)) return;
            text = typed;
        }

        ActionInvoked?.Invoke(new ToastAction(kind, paneId, text));
    }

    /// <summary>Post (or replace) the blocked-agent toast.</summary>
    public void ShowBlocked(Agent agent)
    {
        if (!_registered)
        {
            Log($"skipped: not registered ({SetupError ?? "no reason recorded"})");
            return;
        }
        Post(() => BuildBlockedXml(agent), $"blocked: {agent.Name} @ {agent.Id}");
    }

    /// <summary>
    /// Post the agent-finished toast.
    ///
    /// Deliberately unlike the blocked one in three ways, because it is saying something
    /// different: it does not use scenario="reminder" (an approval prompt must survive you
    /// walking away, a "done" notice going stale on screen is just clutter), it gets its own
    /// sound so the two are distinguishable without looking, and it is tagged per pane so
    /// three agents finishing at once produce three lines rather than overwriting each other.
    /// </summary>
    public void ShowFinished(Agent agent)
    {
        if (!_registered)
        {
            Log($"finished skipped: not registered ({SetupError ?? "no reason recorded"})");
            return;
        }
        Post(() => BuildFinishedXml(agent), $"finished: {agent.Name} @ {agent.Id}", FinishedTag(agent));
    }

    /// <summary>
    /// Per-pane tag, so distinct agents stack in Action Center while the *same* agent
    /// replaces its own earlier notice. That second half matters: if a status flaps between
    /// working and idle, reusing the tag turns what would be a stream of toasts into one that
    /// keeps being refreshed.
    ///
    /// Hashed rather than truncated because Tag is capped at 64 characters and a pane id is
    /// not, and two panes whose ids share a prefix must not collapse into one toast.
    /// </summary>
    private static string FinishedTag(Agent agent)
    {
        var digest = SHA256.HashData(Encoding.UTF8.GetBytes(agent.Id));
        return "herdr-done-" + Convert.ToHexString(digest)[..16];
    }

    internal static string BuildFinishedXml(Agent agent)
    {
        var body = $"{agent.Name} finished in {agent.DisplayLocation}";
        var pane = Uri.EscapeDataString(agent.Id);
        var attribution = agent.IsRemote ? $"{agent.Host} · herdr" : "herdr";
        var launch = $"action=open&amp;pane={pane}";

        // A reply box rather than buttons: a finished agent has no options to pick from, but
        // "now do this next" is the obvious thing to want, and it costs nothing — the reply
        // path already routes free text through agent_prompt.
        var reply = Esc($"Tell {agent.Name} what's next…");

        return $"""
            <toast launch="{launch}" activationType="background">
              <visual>
                <binding template="ToastGeneric">
                  <text>Agent Finished</text>
                  <text>{Esc(body)}</text>
                  <text placement="attribution">{Esc(attribution)}</text>
                </binding>
              </visual>
              <audio src="ms-winsoundevent:Notification.IM"/>
              <actions>
                <input id="reply" type="text" placeHolderContent="{reply}"/>
                <action content="Send" hint-inputId="reply" arguments="action=reply&amp;pane={pane}" activationType="background"/>
              </actions>
            </toast>
            """;
    }

    /// <summary>
    /// Hand one toast to Windows, recording what happened. Every failure mode below is
    /// otherwise invisible: a malformed payload, a notifier that will not construct, and an
    /// OS that has notifications switched off all produce the same nothing on screen.
    /// </summary>
    private void Post(Func<string> buildXml, string what, string? tag = null)
    {
        // Built inside the try, not passed in already built: an exception while composing the
        // payload would otherwise escape past this handler and reach the dispatcher, turning a
        // notification that cannot be sent into a message box the user has to dismiss.
        var xml = "<not built>";
        try
        {
            xml = buildXml();
            var doc = new XmlDocument();
            // Throws on malformed XML, which is worth separating from a toast that parsed
            // and was then rejected — the log says which.
            doc.LoadXml(xml);

            var notifier = ToastNotificationManager.CreateToastNotifier(Aumid);

            // Windows will accept the toast and show nothing if notifications are off for
            // this app, for the user, or by policy. It says so here, and only here.
            var setting = notifier.Setting;
            DeliveryBlocked = setting switch
            {
                NotificationSetting.Enabled => null,
                NotificationSetting.DisabledForApplication =>
                    "turned off for Herdi in Settings › System › Notifications",
                NotificationSetting.DisabledForUser =>
                    "turned off for this user in Settings › System › Notifications",
                NotificationSetting.DisabledByGroupPolicy => "blocked by group policy",
                NotificationSetting.DisabledByManifest => "blocked by the app manifest",
                _ => $"unavailable ({setting})",
            };

            var toast = new ToastNotification(doc)
            {
                // Blocked toasts all share one tag, so a newer prompt replaces the older
                // rather than stacking, matching the relay's push collapse topic. Finished
                // ones pass their own per-pane tag instead.
                Tag = tag ?? ToastTag,
                Group = ToastGroup,
            };
            notifier.Show(toast);

            Log(DeliveryBlocked is null
                ? $"shown ({what})"
                : $"handed over but Windows is {DeliveryBlocked} ({what})");
        }
        catch (Exception ex)
        {
            // HRESULT included because the useful ones are numbers: 0x80070490
            // (element not found) means the AUMID shortcut is not being resolved, which is
            // the usual first-run failure.
            SetupError = $"{ex.Message} (0x{ex.HResult:X8})";
            Log($"failed ({what}): {ex.GetType().Name}: {SetupError}\n{xml}");
        }
    }

    /// <summary>
    /// Pull the toast once the agent is no longer blocked — the same courtesy the
    /// relay's Web Push `clear` message provides to browser clients.
    /// </summary>
    public void ClearBlocked()
    {
        if (!_registered) return;
        try
        {
            ToastNotificationManager.History.Remove(ToastTag, ToastGroup, Aumid);
        }
        catch (Exception)
        {
            // Nothing to retract.
        }
    }

    internal static string BuildBlockedXml(Agent agent)
    {
        var body = $"{agent.Name} needs input in {agent.DisplayLocation}";
        var pane = Uri.EscapeDataString(agent.Id);

        var actions = new StringBuilder();
        actions.Append($"<input id=\"reply\" type=\"text\" placeHolderContent=\"{Esc($"Reply to {agent.Name}…")}\"/>");

        // Two option buttons at most: a toast allows five actions total and the reply
        // Send button plus Windows' own dismiss control claim the rest.
        var options = (agent.Options ?? Array.Empty<string>()).Take(2).ToList();
        foreach (var option in options)
        {
            var label = ToastButtonLabel(option);
            var args = $"action=respond&amp;pane={pane}&amp;text={Uri.EscapeDataString(option)}";
            actions.Append(
                $"<action content=\"{Esc(label)}\" arguments=\"{args}\" activationType=\"background\"/>");
        }

        actions.Append(
            $"<action content=\"Send\" hint-inputId=\"reply\" arguments=\"action=reply&amp;pane={pane}\" activationType=\"background\"/>");

        var attribution = agent.IsRemote ? $"{agent.Host} · herdr" : "herdr";
        var launch = $"action=open&amp;pane={pane}";

        // scenario="reminder" holds the toast on screen until it is dismissed, which is the
        // point: an approval prompt the user walked away from must still be there when they
        // come back. Its documented precondition — at least one action that activates in
        // background — is satisfied by the buttons above.
        //
        // Two things here were wrong and each one is enough for Windows to drop the toast
        // without a word, which is exactly how this failed:
        //   * scenario was "urgentReminder", which is not a value. The four that exist are
        //     reminder, alarm, incomingCall and urgent.
        //   * <audio> sat after <actions>. The toast element is an ordered sequence —
        //     visual, audio?, commands?, actions?, header? — so audio has to come first.
        // "urgent" was the other candidate and is deliberately not used: it exists to punch
        // through Focus Assist, and PopForBlocked already declines to interrupt a game or a
        // presentation, so breaking that restraint here would contradict it.
        return $"""
            <toast launch="{launch}" activationType="background" scenario="reminder">
              <visual>
                <binding template="ToastGeneric">
                  <text>Agent Blocked</text>
                  <text>{Esc(body)}</text>
                  <text placement="attribution">{Esc(attribution)}</text>
                </binding>
              </visual>
              <audio src="ms-winsoundevent:Notification.Default"/>
              <actions>
                {actions}
              </actions>
            </toast>
            """;
    }

    /// <summary>
    /// Shorten a raw herdr option into a button label, reusing the mapping
    /// herdi-mac applies in ResponseButtonGrid.mapOption.
    /// </summary>
    internal static string ToastButtonLabel(string option)
    {
        var lower = option.ToLowerInvariant();
        if (lower.Contains("single permission") || lower is "y" or "yes") return "Allow";
        if (lower.Contains("always allow") || lower.Contains("trust")) return "Trust";
        if (lower.Contains("tab to edit") || lower.StartsWith("no") || lower == "n") return "Deny";
        if (lower.Contains("approve all")) return "Approve All";
        if (lower.Contains("configure individually")) return "Configure";
        if (lower.Contains("exit") || lower.Contains("cancel")) return "Cancel";
        return option.Length > 16 ? option[..16] : option;
    }

    private static string Esc(string value) =>
        value.Replace("&", "&amp;").Replace("<", "&lt;").Replace(">", "&gt;").Replace("\"", "&quot;");

    public void Dispose()
    {
        if (_comRegistration != 0)
        {
            CoRevokeClassObject(_comRegistration);
            _comRegistration = 0;
        }
    }

    // --- COM plumbing for toast activation

    private const uint ClsCtxLocalServer = 4;
    private const uint RegClsMultipleUse = 1;
    private const uint RegClsSuspended = 4;

    [DllImport("ole32.dll")]
    private static extern int CoRegisterClassObject(
        ref Guid clsid,
        [MarshalAs(UnmanagedType.IUnknown)] object factory,
        uint context, uint flags, out uint register);

    [DllImport("ole32.dll")]
    private static extern int CoResumeClassObjects();

    [DllImport("ole32.dll")]
    private static extern int CoRevokeClassObject(uint register);

    /// <summary>Hands Windows a NotificationActivator when a toast is acted on.</summary>
    [ComVisible(true)]
    [ClassInterface(ClassInterfaceType.None)]
    private sealed class ClassFactory : IClassFactory
    {
        public int CreateInstance(IntPtr outer, ref Guid iid, out IntPtr instance)
        {
            instance = IntPtr.Zero;
            if (outer != IntPtr.Zero) return unchecked((int)0x80040110); // CLASS_E_NOAGGREGATION

            // Honour the requested interface: Windows may ask for IUnknown rather than
            // INotificationActivationCallback, and hard-coding the latter would fail.
            var unknown = Marshal.GetIUnknownForObject(new NotificationActivator());
            try
            {
                return Marshal.QueryInterface(unknown, in iid, out instance);
            }
            finally
            {
                Marshal.Release(unknown);
            }
        }

        public int LockServer(bool lockIt) => 0;
    }

    [ComImport, Guid("00000001-0000-0000-C000-000000000046")]
    [InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    private interface IClassFactory
    {
        [PreserveSig] int CreateInstance(IntPtr outer, ref Guid iid, out IntPtr instance);
        [PreserveSig] int LockServer([MarshalAs(UnmanagedType.Bool)] bool lockIt);
    }
}

/// <summary>
/// COM entry point Windows calls with the pressed button's arguments and any text the
/// user typed into the toast's input box.
/// </summary>
[ComVisible(true)]
[Guid("B6B4B0C1-6E2A-4F1D-9A73-2C7E5D8F4A21")]
[ClassInterface(ClassInterfaceType.None)]
public sealed class NotificationActivator : INotificationActivationCallback
{
    internal static Action<string, IReadOnlyDictionary<string, string>>? Handler;

    public void Activate(
        string appUserModelId,
        string invokedArgs,
        [MarshalAs(UnmanagedType.LPArray, SizeParamIndex = 3)] NotificationUserInputData[] data,
        uint count)
    {
        var input = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
        if (data is not null)
        {
            for (var i = 0; i < Math.Min(data.Length, (int)count); i++)
            {
                input[data[i].Key] = data[i].Value;
            }
        }

        var handler = Handler;
        if (handler is null) return;

        // Windows calls this on a COM thread; hop to the UI thread before touching
        // observable state.
        var app = System.Windows.Application.Current;
        if (app is not null) app.Dispatcher.Invoke(() => handler(invokedArgs ?? string.Empty, input));
        else handler(invokedArgs ?? string.Empty, input);
    }
}

[StructLayout(LayoutKind.Sequential)]
public struct NotificationUserInputData
{
    [MarshalAs(UnmanagedType.LPWStr)] public string Key;
    [MarshalAs(UnmanagedType.LPWStr)] public string Value;
}

[ComImport, Guid("53E31837-6600-4A81-9395-75CFFE746F94"), ComVisible(true)]
[InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
public interface INotificationActivationCallback
{
    void Activate(
        [MarshalAs(UnmanagedType.LPWStr)] string appUserModelId,
        [MarshalAs(UnmanagedType.LPWStr)] string invokedArgs,
        [MarshalAs(UnmanagedType.LPArray, SizeParamIndex = 3)] NotificationUserInputData[] data,
        uint count);
}
