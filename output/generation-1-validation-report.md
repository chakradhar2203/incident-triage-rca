# Generation 1 validation report

Date reviewed: 2026-10-09

Source: Generation 1, 20 candidate server-incident knowledge cases.

## Decision

Generation 1 is a complete candidate batch, but it is not safe to load unchanged. The review result is:

- Valid after the corrections in the accompanying overlay: 19
- Replace before loading: 1 (`KC-CANDIDATE-007`)
- Exact duplicates inside Generation 1: 0
- Related cases that must remain differentiated by scope: `006`/`019` and `009`/`020`

The cases are accepted only when the correction overlay is applied. A service restart, time step, trust repair, security exclusion, log deletion, filesystem edit, storage change, partition operation, or workload migration always requires the applicable production approval. Temporary recovery must not be recorded as the permanent root-cause fix.

## Case-level review

| Case | Decision | Required correction |
|---|---|---|
| 001 | Valid with corrections | Add inode and deleted-open-file checks. `logrotate` by itself is not a resolution; stop the log source, preserve required evidence, test rotation, and reclaim space only with approval. |
| 002 | Valid with corrections | Prove whether CPU is workload demand, a thread loop, I/O wait, or steal time. Capture evidence before a restart and treat restart as recovery only. |
| 003 | Valid with corrections | Do not infer pressure from low `free` memory alone. Require sustained RSS/cgroup growth, paging or OOM evidence, and workload-normalized recurrence. |
| 004 | Valid with corrections | Replace the unsupported claim that an LV path simply changed. Verify PV/VG/LV, multipath and filesystem identity, back up `fstab`, run `findmnt --verify`, and test only the target mount. |
| 005 | Valid with corrections | “Expired trust credentials” is too specific. Verify DNS, time, DC reachability and the join using `adcli testjoin` before repairing trust. A domain rejoin is a last resort. |
| 006 | Valid with corrections | Test the configured resolver directly and separate DNS service failure from routing/firewall failure. Make persistent changes through NetworkManager, not by hand-editing generated files. |
| 007 | Replace | The case mixes OpenSSH certificates with X.509/TLS. `openssl x509` cannot validate an OpenSSH user/host certificate. Replace it with the corrected gateway TLS certificate case described in the overlay. |
| 008 | Valid with corrections | Distinguish client cache, entitlement, proxy/TLS, repository reachability and centrally published metadata. Do not clear caches across multiple hosts until scope is proven. |
| 009 | Valid with corrections | Verify `chronyd` source state and offset. Avoid stepping production time until application and clustering impact is assessed; restore the approved source first. |
| 010 | Valid with corrections | Make the case vendor-neutral and prohibit ad-hoc deletion of restore points. Retention changes require backup-owner approval and a restore test. |
| 011 | Valid with corrections | Include `crond` status, journal evidence, execution identity, environment and the different syntax of user crontabs versus `/etc/cron.d`. |
| 012 | Valid with corrections | Preserve logs according to audit/retention requirements before removal. Correct rollover/retention and validate that logging continues after recovery. |
| 013 | Valid with corrections | Use Service Control Manager evidence and enumerate dependencies. Correct the failed dependency rather than repeatedly starting the dependent service. |
| 014 | Valid with corrections | Confirm console access, network path, firewall, listener, policy and `TermService`. Warn that restarting Remote Desktop Services can disconnect users. |
| 015 | Valid with corrections | Require the exact update error and proof of low SRP/EFI free space. Partition operations are high risk and require backup plus boot-recovery readiness. |
| 016 | Valid with corrections | Measure antivirus contribution with the vendor performance analyzer. Never disable protection as a diagnostic shortcut; use narrowly scoped, security-approved exclusions. |
| 017 | Valid with corrections | Remove VMware terminology such as “CPU ready.” Use Hyper-V logical/virtual processor counters and confirm host-wide contention before migration or resizing. |
| 018 | Valid with corrections | Validate with `Test-ComputerSecureChannel`. Repair is preferred over remove/rejoin and requires verified local/console access and domain credentials. |
| 019 | Valid with corrections | Keep separate from 006: this is an enterprise-wide major incident, while 006 is a single-host resolver/path case. Require multi-host, multi-platform evidence. |
| 020 | Valid with corrections | Keep separate from 009: this is failure of shared time infrastructure. Establish the authoritative source hierarchy and avoid force-stepping many hosts without impact review. |

## Loading requirements

Before importing a case into production, replace every placeholder (`<service>`, `<hostname>`, `<domain>`, `<resolver>`, `<target>`) with a runtime parameter definition. Commands must be stored separately as read-only diagnostics, approved recovery actions, or high-risk changes. Do not allow the retrieval model to execute remediation automatically.

Each case still requires environment-specific confirmation of monitoring thresholds, service names, backup product, directory domain, DNS servers, time hierarchy, certificate type, retention policy and escalation owner.

## Authoritative references used

- Red Hat: [Monitoring and managing system status and performance](https://docs.redhat.com/en/documentation/red_hat_enterprise_linux/9/htmlsingle/monitoring_and_managing_system_status_and_performance/index)
- Red Hat: [Persistently mounting file systems](https://docs.redhat.com/en/documentation/red_hat_enterprise_linux/9/html/managing_file_systems/assembly_persistently-mounting-file-systems_managing-file-systems)
- Red Hat: [Connecting RHEL directly to Active Directory using SSSD](https://docs.redhat.com/en/documentation/red_hat_enterprise_linux/9/html/integrating_rhel_systems_directly_with_windows_active_directory/connecting-rhel-systems-directly-to-ad-using-sssd_integrating-rhel-systems-directly-with-active-directory)
- Microsoft: [Kerberos authentication troubleshooting guidance](https://learn.microsoft.com/en-us/troubleshoot/windows-server/windows-security/kerberos-authentication-troubleshooting-guidance)
- Microsoft: [RDS session-connectivity troubleshooting](https://learn.microsoft.com/en-us/troubleshoot/windows-server/remote/troubleshoot-rds-session-connectivity-guidance)
- Microsoft: [Test-ComputerSecureChannel](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.management/test-computersecurechannel?view=powershell-5.1)
- Microsoft: [Hyper-V configuration and performance counters](https://learn.microsoft.com/en-us/windows-server/administration/performance-tuning/role/hyper-v-server/configuration)
- Microsoft: [Microsoft Defender Antivirus performance analyzer](https://learn.microsoft.com/en-us/defender-endpoint/tune-performance-defender-antivirus)
- Microsoft: [Managing IIS log file storage](https://learn.microsoft.com/en-us/iis/manage/provisioning-and-managing-iis/managing-iis-log-file-storage)

