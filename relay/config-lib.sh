# shellcheck shell=bash
# herdr-remote shared config loading. Source this; do not execute it.
#
# load_config_file FILE
#   Sources FILE with allexport on, so the values reach child processes - the
#   relay only sees HERDR_RELAY_TOKEN if it is exported, and an unexported one
#   means it starts with auth disabled.
#   An explicitly exported non-empty value always beats the persisted one, so a
#   stale or empty line in config.env/secrets.env can never silently override
#   what the caller asked for on the command line.

load_config_file() {
  local file=$1
  [ -f "$file" ] || return 0
  local keys key i
  local -a names=() values=()
  keys=$(sed -nE 's/^[[:space:]]*(export[[:space:]]+)?([A-Za-z_][A-Za-z0-9_]*)=.*/\2/p' "$file")
  for key in $keys; do
    if [ -n "${!key:+set}" ]; then
      names[${#names[@]}]="$key"
      values[${#values[@]}]="${!key}"
    fi
  done
  local had_allexport=no
  case "$-" in *a*) had_allexport=yes ;; esac
  set -a
  # shellcheck disable=SC1090
  source "$file"
  [ "$had_allexport" = yes ] || set +a
  i=0
  while [ "$i" -lt "${#names[@]}" ]; do
    printf -v "${names[$i]}" '%s' "${values[$i]}"
    export "${names[$i]}"
    i=$((i + 1))
  done
  return 0
}
