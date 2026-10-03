# Targeted security review — 2026-10-03

Reviewed the installed GUI and daemon source and prepared a separate hardened snapshot. The running installation has not been modified. No actual Proton files were uploaded, deleted, or synchronized during review. No credentials, cookies, logs, runtime state or upstream executables were copied into this project.

## Findings addressed in this snapshot

| Finding | Impact | Change |
|---|---|---|
| rclone account/mailbox passwords and OTP were subprocess arguments; obscured passwords are reversible | Credentials exposed through process inspection; misleading stdin-only claim | Removed GUI credential collection; use interactive rclone configuration |
| Custom sharing password was a CLI argument | Link password exposed through process inspection | Use Proton web app for password-protected links |
| Cloud captureTime contributed unchecked path segments | A malformed value could place photos outside the intended month folder | Validate real YYYY-MM values, reject symlink destinations |
| Cloud filename joined into file-open cache path | Traversal could open an unrelated local file | Reject separators, dot components and NUL; reject symlink cache files |
| Predictable JSON .tmp paths and non-private default modes | Symlink overwrite and exposure of private metadata/cookies/cache | Unique temporary files, atomic replacement, private directories and restrictive umask |
| Fixed temporary folder was recursively deleted | Existing user files under that name could be removed | Unique temporary download directory; never remove a preexisting fixed folder |
| Local photo upload followed file symlinks | Upload could include files outside the selected folder | Skip symlink files/directories |
| Web view accepted all schemes and all proton.me subdomains | Unnecessary navigation/protocol-handler surface | HTTPS allowlist for Proton, external HTTPS links in browser, reject other navigation |
| Automatic --resync after lost bisync state | Unreviewed reconciliation could overwrite conflict data | Require manual recovery; bind initialized state to remote folder |

## Remaining limitations and release work

- This is a source review and six regression tests, not a penetration test or security certification. Proton CLI, rclone, GTK, WebKit and image decoders were not independently audited, and a full vulnerability scan of their installed builds has not been performed.
- Download paths are also controlled by upstream CLI code. The app validates names it handles, but cannot sandbox all filesystem writes performed by the CLI. A disposable-account integration test is required for malformed names and CLI download behavior.
- Logout affects only the CLI. Embedded WebKit authentication and rclone sessions are separate; downloaded plaintext/cache survives. Implement complete account removal before claiming a single logout revokes everything.
- rclone stores recoverable credentials in its config. Existing config permissions and encrypted storage setup need user/environment validation. The project does not ship that configuration.
- Existing files from previous versions can retain old permissions. This snapshot protects newly created files/directories; it does not migrate all installed state.
- Bidirectional sync propagates deletions and uses size/modtime comparison. The first run still uses --resync. Test initial conflicts, deletion limits, interrupted runs, state loss and simultaneous edits with backups/test folders. No real-account sync test was run.
- Remote CLI names with escaped slashes/backslashes cannot be mapped faithfully into rclone paths and are rejected. Other malformed CLI output may still cause denial of service/errors.
- Failed photo download batches can leave a private temporary directory for recovery/inspection; cleanup after crashes remains future work.
- The GUI uses the existing user profile by default. Do not launch it for tests without isolating XDG directories if you want to avoid affecting normal settings/sessions.
- Choose a source license before public distribution. The original About dialog says MIT, but the original files did not include an actual license grant.

Primary references: [rclone config create](https://rclone.org/commands/rclone_config_create/), [rclone Proton Drive](https://rclone.org/protondrive/), [official Proton Drive CLI](https://github.com/ProtonDriveApps/sdk/tree/main/cli).
