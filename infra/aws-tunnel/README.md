# AWS rendezvous host

Replaces the Cloudflare quick tunnel with a small EC2 host you control.
The Mac dials **out** to it over a reverse SSH tunnel (`ssh -R`) - no
inbound port is ever opened on the Mac or the home router - and a phone
browser reaches it over HTTPS. The relay itself never leaves loopback on
the Mac.

```
Phone browser ──HTTPS──▶ Caddy :443 ──▶ 127.0.0.1:9375 (EC2)
                                              ▲
                                              │ ssh -R 127.0.0.1:9375:127.0.0.1:8375
                                              │ (Mac dials out, autossh-supervised)
                                              │
                                    relay :8375 (127.0.0.1 only, on the Mac)
```

## Why CloudFormation, not CDK

`landvera/infra` uses CDK, but that's a multi-stack application with
shared constructs and a real deploy pipeline. This is one instance, one
security group, one Elastic IP, and an optional DNS record - a use case
CloudFormation covers directly, in a single file a reviewer can read
top to bottom without an `npm install` or a `cdk synth`. That fits this
repo's existing style (`web/index.html` is one file with no build step)
better than standing up a second CDK app for four resources. If this
grows more stacks or more logic, revisit.

## What this does NOT do

This PR ships the template only. Nothing is applied, no instance is
launched, no hostname is registered. That is a deliberate, separate step
the captain runs by hand (see below) - not something this change does
for you.

## Prerequisites

- AWS CLI configured with the `landerafs` profile against account
  `450730497623`, region `us-east-1`.
- A domain you control, in whatever DNS provider you like. Route 53 is
  optional (see `HostedZoneId` below) - the template works with any DNS
  host, since it only needs an A record pointing at the Elastic IP.
- An SSH keypair generated **just for this tunnel** - do not reuse an
  existing identity:
  ```bash
  ssh-keygen -t ed25519 -f ~/.ssh/herdr-remote-tunnel -C herdr-remote-tunnel -N ""
  ```
  The private key never leaves the Mac and never enters this repo. Only
  the `.pub` file's contents go into the stack, as the `TunnelPublicKey`
  parameter.
- Your current public IP, for `SshAllowedCidr` (`curl -s ifconfig.me`).
- A **default VPC** in the target region. The template omits `VpcId` and
  `SubnetId`, so EC2 places the instance and security group in the region's
  default VPC. Accounts hardened by deleting it fail at stack creation with
  the opaque `No default VPC for this request`. Check with:
  ```bash
  aws ec2 describe-vpcs --profile landerafs --region us-east-1 \
    --filters Name=isDefault,Values=true --query "Vpcs[].VpcId" --output text
  ```
  An empty result means you need to recreate a default VPC
  (`aws ec2 create-default-vpc`) or add explicit `VpcId`/`SubnetId`
  parameters to the template first.

## Deploy (run by a human, not by this PR)

```bash
aws cloudformation deploy \
  --profile landerafs --region us-east-1 \
  --stack-name herdr-remote-tunnel \
  --template-file infra/aws-tunnel/cloudformation.yaml \
  --parameter-overrides \
      HostnameFqdn=herdr-remote.example.com \
      TunnelPublicKey="$(cat ~/.ssh/herdr-remote-tunnel.pub)" \
      SshAllowedCidr="$(curl -s ifconfig.me)/32" \
  --capabilities CAPABILITY_IAM
```

Add `HostedZoneId=Z0123456789ABC` to the overrides if the domain's
hosted zone lives in this same account and you want the template to
create the A record for you. Otherwise, after deploy:

```bash
aws cloudformation describe-stacks --profile landerafs --region us-east-1 \
  --stack-name herdr-remote-tunnel \
  --query "Stacks[0].Outputs"
```

and create an A record for `HostnameFqdn` pointing at the printed
`ElasticIp` in whatever DNS provider hosts that domain.

Then restart Caddy once, after the A record actually resolves:

```bash
aws ssm start-session --profile landerafs --region us-east-1 \
  --target "$(aws cloudformation describe-stacks --profile landerafs \
    --region us-east-1 --stack-name herdr-remote-tunnel \
    --query "Stacks[0].Outputs[?OutputKey=='InstanceId'].OutputValue" --output text)"
# then, in the session:
sudo systemctl restart caddy
```

This step is not optional housekeeping.
Caddy is enabled at first boot, which is necessarily before the Elastic IP is associated and long before you have created the A record, so its first few ACME attempts fail and certmagic backs off exponentially.
Without the restart, HTTPS can stay down for many minutes after DNS is already correct, and the stack looks broken when it is not.
Restarting clears the backoff and Caddy issues the certificate on its next attempt, usually within a minute.

DNS must resolve and Caddy must hold a certificate before the Mac's
tunnel or a phone browser can reach it over HTTPS. Check with:

```bash
curl -sS -o /dev/null -w '%{http_code}\n' https://herdr-remote.example.com/
```

## Updating the SSH-allowed CIDR

Home IPs on residential ISPs are not always static. If `SshAllowedCidr`
goes stale, the reverse tunnel will fail to connect. Update the security
group directly rather than re-running the whole stack:

```bash
aws ec2 update-security-group-rule-descriptions-ingress ...  # or:
aws cloudformation deploy ... --parameter-overrides SshAllowedCidr="$(curl -s ifconfig.me)/32" ...
```

The second form is simplest - CloudFormation only touches the changed
rule.

## Cost

All figures are `us-east-1` on-demand, August 2026 pricing, for the
resources this template creates:

| Resource | Cost |
|---|---|
| `t4g.nano` (2 vCPU burstable, 0.5 GiB), running 24/7 | ~$3.00/mo |
| Elastic IP, attached to a running instance | $0.00/mo (free while associated) |
| EBS gp3 8 GiB root volume | ~$0.65/mo |
| Data transfer out (phone/browser traffic; tiny for this use case) | ~$0.10-0.50/mo |
| Route 53 hosted zone (only if you opt in via `HostedZoneId` and don't already have one) | $0.50/mo |

**Total: roughly $4/mo**, or ~$4.50/mo if you also stand up a fresh
Route 53 zone for this. No per-request or bandwidth surprises at this
traffic level - this is a single phone dashboard polling one relay, not
a public service.

## Security model, in one place

- The relay stays bound to `127.0.0.1` on the Mac. This stack cannot
  reach it directly - only the tunnel the Mac opens can.
- SSH accepts exactly one identity: the forwarding-only `herdr-tunnel`
  user, and the restriction is split across two layers because OpenSSH
  cannot express all of it in one.
  Its authorized_keys options are
  `restrict,port-forwarding,permitlisten="127.0.0.1:<TunnelPort>"` - no shell,
  no command execution, no agent/X11 forwarding, and `permitlisten` narrows
  the remote side so the only bind this key may request is the single
  `127.0.0.1:<TunnelPort>` the relay tunnel exists for, not an arbitrary port
  of the holder's choosing.
  `port-forwarding` is required there for `-R` to work at all, but it also
  re-enables `-L`/`-D`, and authorized_keys has no option that denies them
  (`permitopen` accepts only `host:port`). So the local-forward block lives in
  sshd_config instead, in a `Match User herdr-tunnel` stanza that sets
  `AllowTcpForwarding remote` and `PermitOpen none`. Without that stanza the
  key would let its holder use this host as a general TCP proxy for anything
  its egress reaches, the instance metadata service among them.
- The instance requires IMDSv2 (`HttpTokens: required`, hop limit 1), so
  even a future forwarding mistake cannot be turned into a simple
  unauthenticated read of the instance role's credentials.
- `GatewayPorts no` (the default, set explicitly) means the tunnel's
  remote port binds to the EC2 host's loopback only - nothing but Caddy,
  running on that same host, can reach it.
- The security group opens only 443 (public) and 22 (restricted to
  `SshAllowedCidr`). Port 80 is never opened; Caddy issues its
  Let's-Encrypt certificate over TLS-ALPN-01 on 443 alone.
- Admin access to the host itself is via AWS Systems Manager Session
  Manager (`aws ssm start-session`), not a general SSH login - the IAM
  instance profile grants only `AmazonSSMManagedInstanceCore`.
- `HERDR_RELAY_TOKEN` is required for this path, but be precise about where
  that is enforced.
  The relay process itself does not demand a token: bound to loopback it
  accepts an empty `HERDR_RELAY_TOKEN` and then skips auth on every request,
  and its own hard guard only fires when `HERDR_RELAY_HOST` is moved off
  loopback - which this design deliberately never does.
  What makes the public HTTPS endpoint require a token is a refusal at the
  three entry points that can raise this tunnel: `install-service.sh` will
  not install the tunnel service, and `start.sh` and `tunnel-aws.sh` will not
  start the forward, when `HERDR_RELAY_TOKEN` is unset or empty.
  There is no flag or config knob to bypass that refusal.
  So do not assume the relay would reject an unauthenticated request on its
  own - if you ever expose port 8375 by some other route, nothing behind
  these three checks protects it.
- Stopping is verified, not assumed. `herdr-remote stop` tells launchd/systemd
  to stop the supervised relay and tunnel services (a plain kill is undone by
  `KeepAlive`/`Restart=always` within seconds), then re-checks past the restart
  delay and only reports "Stopped" once it has confirmed the relay port is not
  listening and no tunnel process is running. If it cannot confirm that, it says
  so and exits non-zero - treat that as "still publicly reachable".
