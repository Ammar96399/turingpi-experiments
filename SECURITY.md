# Security policy

This is experimental lab automation, not a hardened or production-supported platform. There is no supported-version matrix or security-response SLA.

## Reporting a vulnerability

Do not put credentials, kubeconfigs, exploitable infrastructure addresses, or sensitive logs in public issues or pull requests. Use the hosting platform's **private vulnerability reporting** feature if the owner has enabled it. Before publication, the owner must enable a private reporting route; no dedicated security mailbox is configured here. Do not assume a public issue is a private channel.

## Deployment boundaries

- Use isolated, dedicated lab hardware and a trusted management network. Do not expose the BMC, SSH, RabbitMQ, Prometheus, or experiment HTTP services directly to the Internet.
- The time proxy has **no authentication**, exposes data-modification/reset endpoints, and interpolates request values into database queries. Loopback is the default bind address. An explicit `time_proxy_host`/`TIME_PROXY_HOST` override is required for remote clients; firewall the service and use trusted clients only. It is not safe for untrusted users without further authentication and query hardening.
- Other services still have lab-specific endpoints, all-interface listeners, or weak/default RabbitMQ credentials. Review them before deployment. Prometheus is exposed by NodePort. A Grafana template also contains a weak password default, although the monitoring playbook does not currently deploy Grafana.
- Several roles execute privileged installers, manipulate firewall/network state, delete resources, or control physical power. Downloaded executable integrity verification is incomplete. Review roles before running them.

## Secrets

- Keep kubeconfigs, private keys, inventories, environment files, and Vault passwords out of Git. `.gitignore` is only a convenience: it does not protect files already tracked or erase history.
- Set `influxdb_token` in protected inventory (preferably Ansible Vault) when starting the time proxy. The role injects it as `INFLUXDB_TOKEN`; direct Python invocation also requires this variable. Never pass actual token values in an issue, shell command shared publicly, or example configuration.
- Additional time-proxy settings: `INFLUXDB_URL`, `INFLUXDB_ORG`, `INFLUXDB_BUCKET`, and `TIME_PROXY_HOST`, mapped from `influxdb_url`, `influxdb_org`, `influxdb_bucket`, and `time_proxy_host`. Python defaults to `http://localhost:8086`, organization `IRISA`, bucket `test`, and host `127.0.0.1`; the sample inventory uses neutral organization/bucket examples. Ansible readiness/reset requests use `time_proxy_connect_host`, derived from the bind address (wildcards map to loopback), and `time_proxy_url`; override these only if the connection path differs.
- Give database tokens the narrowest scopes practical. Environment injection avoids a source-code secret, but does not protect against other privileged users, process inspection, or leaked backups. Protect the control machine and target accounts.
- Do not use Ansible debug output or persistent fact caches for join tokens. Restrict administrator kubeconfigs and token files to their owner.

## Preparing a public snapshot

Create any public snapshot in a separate directory without copying the private repository's `.git` directory. Exclude administrator kubeconfigs, `artifacts/`, private inventories, environment files, keys, logs, and local virtual environments—even if they are tracked in the private repository. Scan the exact snapshot for secrets before its first commit; a single-commit history does not make the copied files safe automatically.

Previously exposed credentials must still be revoked or rotated. Issuing a new kubeconfig does not invalidate old client certificates; invalidate the affected trust or replace the disposable cluster as appropriate. Keeping the original history private is not a substitute for credential revocation. Preserve required third-party notices and verify source/data redistribution rights before publishing.
