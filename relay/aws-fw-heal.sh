#!/usr/bin/env bash
# relay/aws-fw-heal.sh - verify (and, in heal mode, repair) the AWS reverse
# tunnel's SSH security-group rule for the current public IP.
#
# Home IPs on residential ISPs rotate. When the rotated IP is not in the
# security group's port-22 ingress, the reverse tunnel's ssh/autossh cannot
# connect - the relay and tunnel processes look "running" locally, but the
# public URL answers 502 (Caddy up, nothing on the far end). See
# infra/aws-tunnel/README.md "Updating the SSH-allowed CIDR".
#
# Usage: aws-fw-heal.sh check|heal
#   check - read-only. Reports whether the current IP is allowed.
#   heal  - like check, and if the current IP is missing, ADDS it.
#           Never removes or replaces an existing rule (see the ticket this
#           shipped with: a rule this script did not create is not this
#           script's to delete).
#
# Discovery is by the CloudFormation stack's own tags (project=herdr-remote,
# Name=herdr-remote-tunnel) on the running instance, not a hard-coded
# instance/security-group id - a hard-coded id would just be a second thing
# that can go stale.
#
# Output contract:
#   check mode prints exactly one line to stdout:
#     RESULT=<ok|absent|error> IP=<ip|-> GROUP=<sgid|-> RULE=<ruleid|-> REASON=<text>
#   heal mode prints a human-readable line only when it added a rule or
#   could not verify/repair; it is silent (no stdout) for the no-op case.
# Exit codes (both modes): 0 ok/added, 10 absent (check only), 20 could not
# verify/repair.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=relay/config-lib.sh
source "$SCRIPT_DIR/config-lib.sh"

CONFIG_FILE="$HOME/.config/herdr-remote/config.env"
SECRETS_FILE="$HOME/.config/herdr-remote/secrets.env"
load_config_file "$CONFIG_FILE"
load_config_file "$SECRETS_FILE"

MODE="${1:-check}"
case "$MODE" in
  check|heal) ;;
  *) echo "usage: aws-fw-heal.sh [check|heal]" >&2; exit 20 ;;
esac

AWS_ARGS=(--cli-connect-timeout 5 --cli-read-timeout 15)
[ -n "${HERDR_AWS_PROFILE:-}" ] && AWS_ARGS+=(--profile "$HERDR_AWS_PROFILE")
[ -n "${HERDR_AWS_REGION:-}" ] && AWS_ARGS+=(--region "$HERDR_AWS_REGION")

# report RESULT IP GROUP RULE REASON - prints per the output contract above,
# then exits with the code that matches RESULT. Every exit from this script
# goes through here, so the contract cannot drift between call sites.
report() {
  local result="$1" ip="${2:--}" group="${3:--}" rule="${4:--}" reason="${5:-}"
  if [ "$MODE" = check ]; then
    printf 'RESULT=%s IP=%s GROUP=%s RULE=%s REASON=%s\n' "$result" "$ip" "$group" "$rule" "$reason"
  else
    case "$result" in
      added) printf 'herdr-remote: added %s/32 to security group %s for SSH ingress (rule %s)\n' "$ip" "$group" "$rule" ;;
      error) printf 'herdr-remote: could not verify SSH firewall rule: %s\n' "$reason" >&2 ;;
      ok) : ;; # already covered - silence is correct here
    esac
  fi
  case "$result" in
    ok|added) exit 0 ;;
    absent)   exit 10 ;;
    *)        exit 20 ;;
  esac
}

# ip_to_int DOTTED_IP
ip_to_int() {
  local IFS=.
  local -a o
  read -r -a o <<< "$1"
  [ "${#o[@]}" -eq 4 ] || { echo -1; return 1; }
  echo $(( (o[0] << 24) + (o[1] << 16) + (o[2] << 8) + o[3] ))
}

# ip_in_cidr IP CIDR - true if IP falls inside CIDR (bare IP treated as /32).
ip_in_cidr() {
  local ip="$1" cidr="$2" net bits ipi neti mask
  net="${cidr%%/*}"
  bits="${cidr#*/}"
  [ "$bits" = "$cidr" ] && bits=32
  case "$bits" in ''|*[!0-9]*) return 1 ;; esac
  [ "$bits" -ge 0 ] && [ "$bits" -le 32 ] || return 1
  ipi=$(ip_to_int "$ip") || return 1
  neti=$(ip_to_int "$net") || return 1
  if [ "$bits" -eq 0 ]; then mask=0; else mask=$(( (0xFFFFFFFF << (32 - bits)) & 0xFFFFFFFF )); fi
  [ $(( ipi & mask )) -eq $(( neti & mask )) ]
}

command -v aws  >/dev/null 2>&1 || report error - - - "aws CLI not found on PATH"
command -v curl >/dev/null 2>&1 || report error - - - "curl not found (needed to detect the current public IP)"

CURRENT_IP=""
for ip_svc in https://checkip.amazonaws.com https://ifconfig.me/ip; do
  ip=$(curl -fsS --max-time 5 "$ip_svc" 2>/dev/null | tr -d ' \t\r\n')
  if [[ "$ip" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]]; then CURRENT_IP="$ip"; break; fi
done
[ -n "$CURRENT_IP" ] || report error - - - "could not determine the current public IP (checkip.amazonaws.com and ifconfig.me both failed)"

ERRFILE=$(mktemp)
trap 'rm -f "$ERRFILE"' EXIT

if ! INSTANCE_IDS=$(aws ec2 describe-instances "${AWS_ARGS[@]}" \
  --filters "Name=tag:project,Values=herdr-remote" "Name=tag:Name,Values=herdr-remote-tunnel" \
            "Name=instance-state-name,Values=running,pending" \
  --query 'Reservations[].Instances[].InstanceId' --output text 2>"$ERRFILE"); then
  report error "$CURRENT_IP" - - "aws ec2 describe-instances failed: $(tr '\n' ' ' < "$ERRFILE" | cut -c1-300)"
fi
# shellcheck disable=SC2206
INSTANCE_ARR=($INSTANCE_IDS)
case "${#INSTANCE_ARR[@]}" in
  0) report error "$CURRENT_IP" - - "no running EC2 instance tagged project=herdr-remote,Name=herdr-remote-tunnel found (wrong profile/region, or the stack is not deployed here)" ;;
  1) ;;
  *) report error "$CURRENT_IP" - - "multiple EC2 instances match tag Name=herdr-remote-tunnel (${INSTANCE_ARR[*]}) - ambiguous, not touching any of them" ;;
esac
INSTANCE_ID="${INSTANCE_ARR[0]}"

if ! SG_IDS=$(aws ec2 describe-instances "${AWS_ARGS[@]}" --instance-ids "$INSTANCE_ID" \
  --query 'Reservations[0].Instances[0].SecurityGroups[].GroupId' --output text 2>"$ERRFILE") || [ -z "$SG_IDS" ]; then
  report error "$CURRENT_IP" - - "could not read security groups for instance $INSTANCE_ID: $(tr '\n' ' ' < "$ERRFILE" | cut -c1-300)"
fi

SSH_GROUPS=()
for sg in $SG_IDS; do
  if ! has22=$(aws ec2 describe-security-groups "${AWS_ARGS[@]}" --group-ids "$sg" \
    --query "SecurityGroups[0].IpPermissions[?ToPort==\`22\` && FromPort==\`22\` && IpProtocol=='tcp']" \
    --output text 2>"$ERRFILE"); then
    report error "$CURRENT_IP" - - "could not read security group $sg: $(tr '\n' ' ' < "$ERRFILE" | cut -c1-300)"
  fi
  [ -n "$has22" ] && SSH_GROUPS+=("$sg")
done
case "${#SSH_GROUPS[@]}" in
  0) report error "$CURRENT_IP" - - "instance $INSTANCE_ID has no security group with a port-22 ingress rule" ;;
  1) ;;
  *) report error "$CURRENT_IP" - - "instance $INSTANCE_ID has multiple security groups with a port-22 rule (${SSH_GROUPS[*]}) - ambiguous, not touching any of them" ;;
esac
GROUP="${SSH_GROUPS[0]}"

if ! CIDRS=$(aws ec2 describe-security-groups "${AWS_ARGS[@]}" --group-ids "$GROUP" \
  --query "SecurityGroups[0].IpPermissions[?ToPort==\`22\` && FromPort==\`22\` && IpProtocol=='tcp'].IpRanges[].CidrIp" \
  --output text 2>"$ERRFILE"); then
  report error "$CURRENT_IP" "$GROUP" - "could not read ingress rules for $GROUP: $(tr '\n' ' ' < "$ERRFILE" | cut -c1-300)"
fi

for cidr in $CIDRS; do
  if ip_in_cidr "$CURRENT_IP" "$cidr"; then
    report ok "$CURRENT_IP" "$GROUP" -
  fi
done

if [ "$MODE" = check ]; then
  report absent "$CURRENT_IP" "$GROUP" - "current IP is not in the allowed SSH CIDRs (${CIDRS:-none})"
fi

# heal mode, and the current IP is missing: ADD ONLY. Never revoke or
# replace an existing rule - see the header comment.
PERM_JSON=$(printf '[{"IpProtocol":"tcp","FromPort":22,"ToPort":22,"IpRanges":[{"CidrIp":"%s/32","Description":"herdr-remote self-heal %s"}]}]' \
  "$CURRENT_IP" "$(date -u +%Y-%m-%d)")
if ! RULE_ID=$(aws ec2 authorize-security-group-ingress "${AWS_ARGS[@]}" --group-id "$GROUP" \
  --ip-permissions "$PERM_JSON" \
  --query 'SecurityGroupRules[0].SecurityGroupRuleId' --output text 2>"$ERRFILE") \
  || [ -z "$RULE_ID" ] || [ "$RULE_ID" = "None" ]; then
  report error "$CURRENT_IP" "$GROUP" - "authorize-security-group-ingress failed: $(tr '\n' ' ' < "$ERRFILE" | cut -c1-300)"
fi
report added "$CURRENT_IP" "$GROUP" "$RULE_ID"
