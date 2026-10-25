# Windows / two-PC acceptance gate

**Status at delivery: NOT RUN on Windows or two real PCs in the authoring environment.**
The portable unit/Mock/UI results are recorded separately in VALIDATION_REPORT.md. A test being
present below does not mean it passed. Use disposable Windows 10/11 VMs or dedicated lab PCs,
authorized test accounts, a dedicated source tree and an isolated writable destination share.
Back up anything you cannot replace. Never run disruptive policy/network tests against a production machine.

## Build and exact executable

1. Install Python 3.12 x64 and Inno Setup 6.3+ on a Windows build machine.
2. Run build.ps1. Require exit 0, compiled-selftest.json ok=true and a build report.
3. Retain the complete build bundle and its dependency versions; sign the installer/executable for release.
4. Install the **same setup file** on both PCs. The installed app path is Program Files/LANBridgePro.
5. Compare `Get-FileHash 'C:\Program Files\LANBridgePro\LANBridgePro.exe' -Algorithm SHA256` on both.
6. Launch normally, verify the GUI does not prompt UAC until a local system change is requested.

The build script really executes the frozen application; it does not mark source tests as an EXE test.
Inno Setup is optional for compiling portable transfer tests, but protected installation is required for
privileged operations in the frozen app. The GUI, helper and transfer process must be tested together.

## Automated tests available on Windows

```powershell
.\.venv-build\Scripts\python.exe -m pytest -q -m 'not lan'
```

This includes real local Robocopy/SHA/skip, a Windows exclusive-lock check and local verified source cleanup.
The test suite creates temporary files only; it does not automatically change adapters, ACLs or Firewall.

For authorized two-PC file-transfer tests, create a dedicated local source base and a dedicated remote
writable share, connect using the app/Windows first, then set:

```powershell
$env:LANBRIDGE_LAN_SOURCE = 'D:\LANBridgeLabSource'
$env:LANBRIDGE_LAN_DEST = '\\PC-B\LANBridge_labtest'
.\.venv-build\Scripts\python.exe -m pytest -q -m lan
```

The test writes a unique LANBridge-test-UUID child, 2,000 small files, Unicode, an empty folder and a
64 MiB binary file, hashes both copies, repeats the task and verifies that destination extras remain.
A second case checks literal single-file selection. Fixtures are left for inspection; the suite never
recursively deletes the remote share. Review and remove only these unique test folders yourself afterward.
No passwords are accepted as test command-line arguments.

## Required acceptance matrix

Fill a result record with machine builds, EXE hashes, account type, topology, filesystem, link speed,
Robocopy codes, source/destination checksums and session log paths for **each** case.

| Case | Procedure in the dedicated lab | Required observable result | Delivery status |
|---|---|---|---|
| Same EXE on both PCs | Compare installed SHA-256 and version info | Exact same app; each can run either direction | Not run |
| Direct Ethernet | Quick A/B on selected physical Ethernet; no gateway/DNS | Up link, compatible subnet, authorized SMB copy both directions | Not run |
| Switch/Router | DHCP on both, or valid selected static addresses | Correct adapter retained; routed topology supported | Not run |
| Wrong Subnet | Give B a different /24 only in the VM lab | Subnet warning; Ping failure alone not definitive; no guessed fix applied | Not run |
| Cable unplugged | Unplug before preflight, then reconnect | Link issue distinguished; retry succeeds after reconnection | Not run |
| Firewall block | With admin consent create a lab-only TCP445 peer block; remove it afterward | TCP test fails independently of Ping; no global firewall disable | Not run |
| Wrong credentials | Enter a deliberately invalid test-account password once | Correct error; no password in argv, logs, DB or report; no account guessing loop | Not run |
| Domain policies | Use domain VM with required signing and your policy baseline | Signing preserved; denied policy reported; no insecure guest/NTLM workaround | Not run |
| Share missing | Enter a non-existing LANBridge_ share | Share-not-found/path error distinct from successful TCP | Not run |
| Read/Write ACL | Read share as source, write share as destination; then deny-write on a disposable subfolder | Correct failure and failed entry; no Everyone Full Control grant | Not run |
| Empty folder | Transfer an empty nested tree | Folders exist; no fake file counts | Automated case provided; not run |
| Single file | Select only one file beside another | Only selected file copied | Automated case provided; not run |
| Arabic and spaces | Arabic folder/file paths on both endpoints | Open and SHA-256 match; Unicode logs readable | Automated case provided; not run |
| Large file | Use >10 GiB test file on NTFS with enough room | Stable memory, truthful estimated progress, final SHA-256 match | Not run |
| Many small files | 2,000 automated, then >100,000 for load acceptance | GUI responsive, bounded batches, exact final manifest counters | 2,000 case provided; not run |
| Disconnect mid-file | Pull Ethernet while copying large test data; reconnect | No success claim on failure; resume rechecks /Z data; final hashes match | Not run |
| Safe stop | Pause while Robocopy is actively copying a large file | BREAK confined to copy console; wait for exit; completed files remain | Not run |
| Force stop | In lab force stop after confirmed wait | Warning, retained completed files, partial data rechecked on resume | Not run |
| Normal shutdown | Pause, await Paused, close both PCs; start next day | Same saved task; matching complete files skipped; hashes match | Not run |
| Unexpected restart | Restart disposable receiving/sending VM during active transfer | Interrupted task detected; no orphan copy; later verification required | Not run |
| Low disk space | Dedicated small virtual disk with insufficient free capacity | Preflight blocks before Robocopy; no source deletions | Mock covered; hardware not run |
| Extra destination files | Create sentinel destination-only files | Sentinels retained before/after copy and resume | Automated case provided; not run |
| Locked files | Open a test source with share-deny Windows handle | Clear preflight lock/read failure; retry after close works | Native test provided; not run |
| Failed permissions | Deny read/write on disposable test folders | Access error, failed task/file; policies/ACLs not bypassed | Not run |
| Restore network | Save DHCP/static/DNS and an IPv6 DNS sentinel before apply; rollback from saved operation | Same GUID targeted; manual IP/default routes/DNS mode restored; IPv6 address and DNS untouched | Not run |
| IP conflict | Assign an already-used lab address with positive ARP/DAD | Local/ARP conflict blocked; DAD failure attempts snapshot rollback | Not run |
| Share rollback | Add optional NTFS permission then remove app share | Root DACL restored if unchanged; outside ACL changes cause refusal | Not run |
| UAC denial / pipe loss | Cancel UAC, deny elevated confirmation, close parent before ACK | No mutation before durable ACK; clear cancellation state | Pre-ACK Mock covered; UAC not run |
| Concurrent operation | Try editing source/network/share during active task | Controls/service lock block conflicting local mutation | Unit covered; Windows not run |
| FAT/exFAT | Separate test volumes, enable FFT, test FAT32 oversize file | Limit/timestamp checks; no claim NTFS metadata is preserved | Not run |
| Verified move | Completed SHA task; explicit confirmation; modify/lock a copy before cleanup | Fresh locked comparison stops on mismatch; only verified local files deleted; folders remain | Native test provided; not run |
| DPI / languages | 100%, 150%, 200%; 1366×768 and 1920×1080; Arabic and English | Scrollable UI, readable dialogs, IP/path order retained | Offscreen UI covered; Windows not run |

## Evidence and release decision

Create a lab result JSON/Markdown outside the transferred folders. Record **PASS / FAIL / NOT RUN** per
case with log paths and hashes. Reject the release if native self-test, stop/exit supervision, UAC identity,
rollback, Unicode, source preservation, destination-extra retention or restart/hash recovery fails. A local
unit test or successful build is not evidence of actual two-PC shutdown recovery or corporate policy compatibility.
