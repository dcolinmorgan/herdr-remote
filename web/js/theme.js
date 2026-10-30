// Runs in <head>, before the stylesheet: saved palettes must apply on the first paint.
// Only these semantic colours are configurable. Never accept CSS, URLs, or terminal tokens.
const THEME_KEY = 'herdr_theme';
const CUSTOM_THEME_KEY = 'herdr_custom_theme';
const THEME_META = {light: '#f5f5f5', dark: '#0a0a0a'};
const THEME_TOKENS = ['bg', 'surface', 'border', 'text', 'muted', 'green', 'red', 'blue', 'orange', 'on-accent'];
const THEME_MAX_LENGTH = 4096;

function themeContrast(a, b) {
  const luminance = hex => {
    const channels = [1, 3, 5].map(i => {
      const v = parseInt(hex.slice(i, i + 2), 16) / 255;
      return v <= 0.04045 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
    });
    return channels[0] * 0.2126 + channels[1] * 0.7152 + channels[2] * 0.0722;
  };
  const x = luminance(a), y = luminance(b);
  return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05);
}

function parseCustomTheme(raw) {
  if (raw.length > THEME_MAX_LENGTH) throw new Error('Palette JSON must be at most 4096 characters.');
  let value;
  try { value = JSON.parse(raw); }
  catch (e) { throw new Error('Enter valid JSON, using double quotes around names and colors.'); }
  if (!value || typeof value !== 'object' || Array.isArray(value)
      || Object.keys(value).some(k => k !== 'colorScheme' && k !== 'colors')
      || !['light', 'dark'].includes(value.colorScheme))
    throw new Error('Set colorScheme to "light" or "dark", and provide a colors object.');
  const source = value.colors;
  if (!source || typeof source !== 'object' || Array.isArray(source)
      || Object.keys(source).length !== THEME_TOKENS.length
      || THEME_TOKENS.some(k => !Object.hasOwn(source, k) || typeof source[k] !== 'string'
        || !/^#[0-9a-f]{6}$/i.test(source[k])))
    throw new Error('Provide exactly these colors as #RRGGBB: ' + THEME_TOKENS.join(', ') + '.');
  const colors = Object.fromEntries(THEME_TOKENS.map(k => [k, source[k].toLowerCase()]));
  // Keep the settings and recovery controls legible, not just the transcript's main text.
  const accents = ['green', 'red', 'blue', 'orange'];
  const pairs = ['bg', 'surface'].flatMap(bg => ['text', 'muted', ...accents].map(fg => [fg, bg]));
  pairs.push(...accents.map(bg => ['on-accent', bg]));
  for (const [fg, bg] of pairs) {
    if (themeContrast(colors[fg], colors[bg]) < 4.5)
      throw new Error(fg + ' on ' + bg + ' needs at least 4.5:1 contrast (WCAG AA).');
  }
  return {colorScheme: value.colorScheme, colors};
}

// Keep an in-memory choice too: a blocked/full localStorage must not make the control a no-op.
let themeChoice = 'auto', customTheme = null;
try {
  const pref = localStorage.getItem(THEME_KEY);
  if (['light', 'dark', 'custom'].includes(pref)) themeChoice = pref;
  const raw = localStorage.getItem(CUSTOM_THEME_KEY);
  if (raw) {
    try { customTheme = parseCustomTheme(raw); }
    catch (e) {}  // Corrupt or older palettes cannot break the rest of the app.
  }
} catch (e) {}  // Private mode: the CSS default still follows the system.
if (themeChoice === 'custom' && !customTheme) themeChoice = 'auto';

function themePref() { return themeChoice; }

function themeResolved(pref) {
  if (pref === 'custom') return customTheme.colorScheme;
  if (pref !== 'auto') return pref;
  return matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
}

function applyTheme() {
  const root = document.documentElement;
  const pref = themePref(), resolved = themeResolved(pref);
  if (pref === 'auto') delete root.dataset.theme;
  else root.dataset.theme = pref;
  // Remove only properties owned here, so returning to Auto/Light/Dark restores the CSS pairs.
  for (const token of THEME_TOKENS) root.style.removeProperty('--' + token);
  root.style.removeProperty('--header-bg');
  root.style.removeProperty('color-scheme');
  if (pref === 'custom') {
    for (const token of THEME_TOKENS) root.style.setProperty('--' + token, customTheme.colors[token]);
    root.style.colorScheme = resolved;
    root.style.setProperty('--header-bg', 'color-mix(in srgb, var(--bg) 72%, transparent)');
  }
  for (const meta of document.querySelectorAll('meta[name="theme-color"]')) {
    const own = (meta.getAttribute('media') || '').includes('dark') ? 'dark' : 'light';
    meta.content = pref === 'custom' ? customTheme.colors.bg : THEME_META[pref === 'auto' ? own : resolved];
  }
  for (const btn of document.querySelectorAll('[data-theme-choice]')) {
    const checked = btn.dataset.themeChoice === pref;
    btn.setAttribute('aria-checked', String(checked));
    btn.tabIndex = checked ? 0 : -1;
    if (btn.dataset.themeChoice === 'custom') btn.disabled = !customTheme;
  }
  const note = document.getElementById('themeResolved');
  if (note) note.textContent = pref === 'auto' ? 'Now ' + resolved + '.'
    : pref === 'custom' ? 'Custom ' + resolved + ' palette.' : '';
}

function themeStatus(message, invalid = false) {
  const status = document.getElementById('themeStatus');
  if (status) {
    status.textContent = message;
    status.dataset.error = String(invalid);
  }
  const editor = document.getElementById('themePalette');
  if (editor) editor.setAttribute('aria-invalid', String(invalid));
}

function storeTheme() {
  try {
    if (customTheme) localStorage.setItem(CUSTOM_THEME_KEY, JSON.stringify(customTheme));
    else localStorage.removeItem(CUSTOM_THEME_KEY);
    if (themeChoice === 'auto') localStorage.removeItem(THEME_KEY);
    else localStorage.setItem(THEME_KEY, themeChoice);
    return true;
  } catch (e) { return false; }
}

function setTheme(pref) {
  if (!['auto', 'light', 'dark', 'custom'].includes(pref) || (pref === 'custom' && !customTheme)) return false;
  themeChoice = pref;
  const saved = storeTheme();
  applyTheme();
  themeStatus(saved ? '' : 'Applied for this tab only; browser storage is unavailable.');
  if (window.cue) cue('toggle');
  return saved;
}

function applyCustomTheme() {
  const editor = document.getElementById('themePalette');
  let palette;
  try { palette = parseCustomTheme(editor.value); }
  catch (e) { themeStatus(e.message, true); return; }
  customTheme = palette;
  editor.value = JSON.stringify(palette, null, 2);
  if (setTheme('custom')) themeStatus('Custom colors applied and saved in this browser.');
}

function currentThemePalette() {
  // Resolve the existing CSS pairs instead of maintaining a second copy of the default palettes.
  const probe = document.createElement('span');
  probe.hidden = true;
  document.body.appendChild(probe);
  const colors = {};
  for (const token of THEME_TOKENS) {
    probe.style.color = 'var(--' + token + ')';
    const rgb = getComputedStyle(probe).color.match(/[\d.]+/g).slice(0, 3);
    colors[token] = '#' + rgb.map(v => Math.round(Number(v)).toString(16).padStart(2, '0')).join('');
  }
  probe.remove();
  return {colorScheme: themeResolved(themePref()), colors};
}

function resetCustomTheme() {
  customTheme = null;
  if (themeChoice === 'custom') themeChoice = 'auto';
  const saved = storeTheme();
  applyTheme();
  document.getElementById('themePalette').value = JSON.stringify(currentThemePalette(), null, 2);
  themeStatus(saved ? 'Custom colors cleared.' : 'Reset for this tab only; browser storage is unavailable.');
}

function initThemeEditor() {
  applyTheme();
  const editor = document.getElementById('themePalette');
  // Preserve a prefilled example or a browser-restored draft until the user explicitly applies it.
  if (customTheme) editor.value = JSON.stringify(customTheme, null, 2);
  else if (!editor.value.trim()) editor.value = JSON.stringify(currentThemePalette(), null, 2);
  document.querySelector('.theme-seg').addEventListener('keydown', event => {
    if (!['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home', 'End'].includes(event.key)) return;
    const buttons = [...document.querySelectorAll('[data-theme-choice]')].filter(b => !b.disabled);
    const index = buttons.indexOf(event.target);
    if (index < 0) return;
    event.preventDefault();
    const next = event.key === 'Home' ? 0 : event.key === 'End' ? buttons.length - 1
      : (index + (['ArrowLeft', 'ArrowUp'].includes(event.key) ? -1 : 1) + buttons.length) % buttons.length;
    buttons[next].focus();
    setTheme(buttons[next].dataset.themeChoice);
  });
}

matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
  if (themePref() === 'auto') applyTheme();
});
applyTheme();
document.addEventListener('DOMContentLoaded', initThemeEditor);
