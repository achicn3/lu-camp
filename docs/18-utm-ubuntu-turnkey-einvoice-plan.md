# 18 — UTM / Ubuntu 24.04 / Turnkey / E-Invoice Integration Plan

> Date: 2026-06-18
>
> Scope: plan only. This file does not implement e-invoice. It adapts the existing project
> Turnkey/MIG research in `docs/14-einvoice-mig-mapping.md` to the proposed Mac UTM +
> Ubuntu 24.04 deployment.
>
> Rule for future implementation: do not hard-code e-invoice rules from memory. Before T13
> implementation, download and verify the current official Turnkey manual, MIG PDF/XSD, and
> platform application documents again.

## 1. Current Project State

The current app is not ready to issue electronic invoices.

- `docs/current-status.md` marks E-invoice / Turnkey as deferred and points to `docs/14` as the
  canonical research source.
- `docs/01-requirements.md` requires MIG 4.0/4.1, Turnkey, local XML generation, upload queue
  tracking, and ProcessResult/SummaryResult reconciliation.
- `docs/04-api-spec.md` only plans invoice endpoints:
  `/api/v1/invoices/{id}`, `/api/v1/einvoice/queue`, `/retry`, and `/process-results`.
- Current code has `settings.einvoice_enabled`, `sales.invoice_status`, and POS UI text for
  "this period does not issue invoice", but there is no backend `einvoice` module, no invoice
  table, no XML generator, no queue table, and no Turnkey result importer.

Conclusion: the next e-invoice work should start as a new T13 implementation track, not as a
small POS UI patch.

## 2. Version And Message Decisions Already Known

Use `docs/14-einvoice-mig-mapping.md` as the local project baseline. The critical decisions are:

- Do not use old `C0401` / `C0501` / `C0701` examples.
- For this store as a self-hosted Turnkey preservation seller, issue with `F0401`.
- Use `F0501` for cancel invoice, `F0701` for void invoice, `G0401` for allowance, and `G0501`
  for cancel allowance.
- Turnkey package / manual / MIG version numbers are different concepts:
  - package observed in project docs: Turnkey installer 3.2.1;
  - manual: Turnkey User Manual Ver 3.9;
  - MIG: Ver 4.1.
- Turnkey configuration is XML, especially `einvTurnkeyConfig.xml` and `einvUserConfig.xml`;
  do not design around a `turnkey.ini`.
- The drop directory observed in project research is
  `EINVTurnkey/UpCast/B2SSTORAGE/<message-type>/SRC/`, for example
  `.../B2SSTORAGE/F0401/SRC/`.

Official checks performed for this document:

- The official MIG PDF URL `5380.pdf` opened as Message Implementation Guideline Ver 4.1,
  dated 2025-10-29, and its revision history includes the MIG 4.0 consolidation that added
  `F0401`, `F0501`, `F0701`, `G0401`, and `G0501`.
- The official Turnkey manual URL is `321.pdf`; the search result identifies it as Turnkey
  User Manual Ver 3.9, dated 2025-02-17. Direct opening may hit the government site's
  Cloudflare waiting room.
- The official Turnkey pre-launch self-check document says testing is done through the
  verification platform, ProcessResult/SummaryResult must be checked, Turnkey outbound access
  needs platform firewall registration by the company's external fixed IP, and testing data
  should not use real personal/business data.

## 3. Architecture Choice For Your Mac UTM Plan

### Recommended Shape

Run Turnkey and the POS backend in the same Ubuntu VM if possible.

Reason: the project integration model is file handoff plus result polling. If backend and
Turnkey are on the same Linux host, the backend can atomically write XML into the local
Turnkey `SRC/` directory and read results/logs locally or through the same PostgreSQL instance.
This avoids SMB/NFS locking, partial file visibility, and cross-machine retry ambiguity.

Recommended host layout:

```text
Mac host
└─ UTM VM: Ubuntu 24.04 x86_64
   ├─ POS backend service
   ├─ PostgreSQL for POS app
   ├─ PostgreSQL or H2/other DB for Turnkey
   ├─ EINVTurnkey/
   └─ hardware/POS browser clients reach backend by LAN IP
```

If the backend must remain outside the VM, do not let it write directly to a network share as
the first design. Prefer one of these controlled handoff options:

- A small internal e-invoice agent on the VM exposes an authenticated LAN API and writes files
  locally to Turnkey.
- Backend uploads XML to the VM by SFTP into a staging directory, then a VM-side process
  validates and atomically renames into Turnkey `SRC/`.

Both options are more complex than same-VM deployment and should be documented before coding.

### CPU Architecture Risk

This is the biggest deployment risk.

- If your Mac is Intel, an Ubuntu x86_64 VM is the natural target.
- If your Mac is Apple Silicon, an Ubuntu ARM64 VM is fast, but the current Turnkey Linux
  package researched in `docs/14` is x86-64 oriented. UTM's own site says ARM64 guests on
  Apple Silicon run near native speed, while x86/x64 on Apple Silicon uses lower-performance
  emulation.
- Therefore, on Apple Silicon, do not assume a normal ARM64 Ubuntu 24.04 VM can run Turnkey.
  Choose one:
  - UTM x86_64 emulation, then test Turnkey performance and stability;
  - an Intel Mac / x86_64 mini PC / x86_64 Linux server;
  - a cloud or on-prem x86_64 host with stable network and backup.

Do not proceed to production until the exact Turnkey installer runs successfully on the chosen
CPU architecture.

## 4. UTM VM Setup

### VM Creation

Use Ubuntu 24.04 Desktop if you want built-in GNOME RDP configuration. Use Server only if you
are comfortable managing without desktop or will install a desktop/xrdp stack yourself.

Minimum project-aligned choices:

- Architecture: x86_64 for Turnkey, unless official Turnkey package later supports ARM64.
- Disk: at least the official Turnkey planning baseline from `docs/14` says 80GB+ available
  space; allocate more if POS database, logs, backups, and screenshots live there.
- RAM/CPU: follow the official Turnkey manual baseline captured in `docs/14` for production;
  if testing below that, document it as a test-only exception.
- Snapshot before Turnkey install and before certificate import.

### Network Mode

UTM offers Shared Network and Bridged. UTM docs say Shared Network is recommended for new VMs,
while Bridged creates a layer-2 bridge and is for advanced users; bridging over Wi-Fi may need
additional configuration.

For your stated RDP goal:

- Bridged is acceptable if the VM must appear as a normal LAN host.
- Reserve the VM's LAN IP in the router DHCP table, or configure a static IP inside Ubuntu.
- Bridge mode does not by itself provide the external fixed public IP that Turnkey/platform
  firewall registration may require. Confirm ISP/router public IP separately.
- Keep RDP LAN-only or VPN-only. Do not port-forward RDP to the public Internet.

Ubuntu static IP can be configured with Netplan. The Ubuntu Server docs show persistent static
configuration in `/etc/netplan/99_config.yaml`, then `sudo netplan apply`.

Example shape only; replace interface/IP/gateway/DNS with your LAN values:

```yaml
network:
  version: 2
  renderer: networkd
  ethernets:
    enp0s1:
      addresses:
        - 192.168.1.50/24
      routes:
        - to: default
          via: 192.168.1.1
      nameservers:
        addresses: [192.168.1.1, 1.1.1.1]
```

### Remote Desktop

Ubuntu Desktop 24.04 supports RDP through Settings -> System -> Remote Desktop. Ubuntu docs
separate Desktop Sharing and Remote Login:

- Desktop Sharing requires the user session to be logged in.
- Remote Login allows login from another computer.
- Default ports are 3389/3390 depending on mode.

Suggested security baseline:

```bash
sudo ufw allow from <your-lan-cidr> to any port 3389 proto tcp
sudo ufw allow from <your-lan-cidr> to any port 3390 proto tcp
sudo ufw enable
```

Use VPN instead of public port forwarding.

## 5. Ubuntu Base Packages

Install only what is needed and keep Turnkey separate from the POS app.

```bash
sudo apt update
sudo apt upgrade
sudo timedatectl set-timezone Asia/Taipei
sudo apt install openssh-server ufw ca-certificates unzip
sudo apt install openjdk-17-jre
```

PostgreSQL:

```bash
sudo apt install postgresql
```

Ubuntu's PostgreSQL docs note that local connections default to peer auth and host connections
use `scram-sha-256`. If Turnkey uses PostgreSQL, create a separate database/user for Turnkey;
do not reuse the POS app database.

Example database split:

```text
lucamp_pos        POS app DB
einv_turnkey      Turnkey DB
```

Back up both separately.

## 6. Turnkey Application And Certificate Setup

### Before Installing

Collect:

- Business tax ID and final store legal name/address.
- MOEACA business certificate IC card and PIN.
- Card reader, if using card-based certificate operations.
- Decision: card certificate vs software certificate/PFX for unattended operation.
- Fixed external public IP or platform-approved outbound IP plan.
- Turnkey test and production platform access.

Official application flow from the platform materials:

- Use business certificate to register platform account.
- Register main certificate.
- Apply for Turnkey transmission for verification and production.
- Application is reviewed by the platform; official slides indicate about three working days.
- Application must be from non-China IP per platform instructions.

### Install / Configure

Do not script this from memory. Use the current official manual and package at installation
time. The project research currently points to:

- Turnkey package: `5420.zip`
- Turnkey manual: `321.pdf`
- MIG 4.1: `5380.pdf`

Configuration items to capture in an installation record:

- Turnkey home path, e.g. `/opt/EINVTurnkey` or another chosen path.
- External Java path or Turnkey bundled `jre/` path.
- Turnkey DB connection.
- `einvUserConfig.xml`:
  - working path / `def-path`;
  - inbox/result path / `erp-in-box-path`;
  - test vs production setting, e.g. `execute-environment=T` for test;
  - retention days;
  - database settings.
- Certificate registration:
  - card certificate or software PFX;
  - certificate alias/name used by Turnkey;
  - expiry date;
  - storage path and backup procedure.
- Sender management / Turnkey account settings from the platform approval email.

Start in the verification environment. Do not connect production until:

- self-check scenarios pass;
- F0401/F0501/F0701/G0401/G0501 XML samples validate against current XSD;
- ProcessResult and SummaryResult are imported and matched by the POS backend;
- invoice number allocation and duplicate-number checks are implemented.

## 7. POS Backend Integration Design

### New Backend Components Needed

Likely modules/tables:

- `invoices`: invoice number, sale ID, buyer fields, carrier/donation fields, print mark,
  random number, MIG action/status.
- `invoice_allowances`: allowance records for returns/discount certificates.
- `einvoice_upload_queue`: one row per XML file/action with state `PENDING`, `DROPPED`,
  `UPLOADED`, `FAILED`, `RETRYING`.
- `einvoice_result_events`: parsed ProcessResult/SummaryResult entries.
- XML serializers for `F0401`, `F0501`, `F0701`, `G0401`, `G0501`.
- A result importer that reads Turnkey result files or read-only Turnkey DB log tables.

### File Handoff

If backend and Turnkey are on the same VM:

1. Create invoice DB row and queue row inside the sale transaction or an immediately following
   durable transaction.
2. Generate XML from DB state, not from frontend payload.
3. Validate with official XSD.
4. Write to a temporary file on the same filesystem.
5. Flush/fsync where practical.
6. Atomic rename into `EINVTurnkey/UpCast/B2SSTORAGE/<msg>/SRC/`.
7. Mark queue as dropped with filename/checksum.
8. Let Turnkey upload.
9. Import ProcessResult/SummaryResult and update queue/invoice status.

Never let Turnkey see a partially written XML file.

### Result Matching

Use two reconciliation paths:

- ProcessResult: per-message result and error code.
- SummaryResult: batch/count reconciliation to detect missed uploads.

Real operational checks:

- every issued invoice has a queue row;
- every queue row has a file checksum and Turnkey handoff timestamp;
- every handoff gets a final result or a timed-out alert;
- SummaryResult count agrees with local expected count;
- retry never creates a second invoice number for the same sale.

### POS Flow Impact

When `einvoice_enabled=false`, current behavior can remain: complete sale and keep
`invoice_status=NOT_ISSUED`.

When `einvoice_enabled=true`, do not simply enable the existing UI switch. Required behavior:

- invoice number assignment before/at sale completion;
- buyer/carrier/donation validation;
- print mark decision;
- proof print workflow if required;
- XML queue and durable retry;
- void/allowance workflow for return and cancellation;
- manager-visible failed upload queue.

The POS sale must stay atomic for money/inventory. E-invoice upload itself can be asynchronous,
but the invoice number and local invoice record must be durable before the cashier treats the
sale as invoiced.

## 8. Production Operation Checklist

Daily:

- Check Turnkey scheduler/process health.
- Check `einvoice_upload_queue` for stuck `PENDING/FAILED`.
- Check Turnkey errors and ProcessResult error codes.
- Compare local invoice count with SummaryResult.
- Check disk space for Turnkey BAK/ERR/result directories.

Monthly/periodic:

- Verify certificate expiry.
- Verify official MIG/Turnkey announcements.
- Test restore of POS DB, Turnkey DB, config XML, certificates/PFX, and queue files.
- Confirm invoice track/number allocation and unused-number handling with accountant.

Backups:

- POS database.
- Turnkey database.
- `einvUserConfig.xml` and relevant Turnkey config files.
- Certificate/PFX and secure password escrow.
- Handoff/result archive.
- Application logs.

Security:

- RDP is admin-only, LAN/VPN-only.
- SSH key auth preferred.
- Do not expose PostgreSQL, backend admin endpoints, or Turnkey UI directly to the Internet.
- Keep certificate/PFX outside repo and outside normal user-download folders.

## 9. Open Questions Before Implementation

1. Is the Mac Intel or Apple Silicon?
2. Will the POS backend run inside the Ubuntu VM, or on another host?
3. Can the shop obtain a fixed public outbound IP for Turnkey platform firewall registration?
4. Will certificate signing use IC card directly or software PFX?
5. Is the first release B2C only, or also B2B buyer-tax-ID invoices?
6. Does the accountant require any special tax type, zero-tax, exempt-tax, or invoice-track policy?
7. What is the official source for invoice number allocation in this shop: manual import, platform
   download, or Turnkey E0501 process?
8. What is the required proof-printing hardware and exact e-invoice proof format?
9. Who owns daily Turnkey error review in store operations?

## Sources

Project docs:

- `docs/01-requirements.md`
- `docs/04-api-spec.md`
- `docs/14-einvoice-mig-mapping.md`
- `docs/current-status.md`

Official / primary references checked:

- Taiwan E-Invoice MIG 4.1 PDF: https://www.einvoice.nat.gov.tw/static/ptl/ein_upload/download/5380.pdf
- Taiwan Turnkey manual Ver 3.9 URL: https://www.einvoice.nat.gov.tw/static/ptl/ein_upload/download/321.pdf
- Taiwan Turnkey package URL: https://www.einvoice.nat.gov.tw/static/ptl/ein_upload/download/5420.zip
- Taiwan Turnkey online application slides: https://www.einvoice.nat.gov.tw/static/ptl/ein_upload/attachments/1582162828025_0.pdf
- Taiwan Turnkey pre-launch self-check PDF: https://www.einvoice.nat.gov.tw/static/ptl/ein_upload/download/9.pdf
- Taiwan certificate / receiving-method slides: https://www.einvoice.nat.gov.tw/static/ptl/ein_upload/attachments/1595573317749_0.pdf
- UTM networking docs: https://docs.getutm.app/settings-qemu/devices/network/network/
- UTM macOS / architecture overview: https://mac.getutm.app/
- Ubuntu static IP / Netplan docs: https://ubuntu.com/server/docs/explanation/networking/configuring-networks/
- Ubuntu 24.04 RDP docs: https://ubuntu.com/desktop/docs/en/latest/how-to/share-your-desktop-remotely/
- Ubuntu PostgreSQL docs: https://ubuntu.com/server/docs/how-to/databases/install-postgresql/
