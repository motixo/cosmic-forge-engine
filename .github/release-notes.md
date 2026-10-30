## COSMIC Forge Engine 0.6.1

Fixes installations where a terminal finds Forge, but the desktop session still
starts the original system compositor because its login PATH misses Forge's launcher.
Renderer revision **7** and shader ABI **1** are unchanged.

### Fixed

- Check the account's actual login shell with a clean PATH, instead of inheriting
  an interactive terminal's PATH. If necessary, add the Forge launcher directory
  to the active Bash, Zsh or POSIX sh login profile, or a Fish login snippet.
- Repair existing installations too. Reinstalling an already matching engine
  runs this check without downloading, rebuilding or replacing the compositor binary.
- Back up original profile contents and permissions. Restore removes only the
  recorded Forge block and preserves user edits outside it.
- Roll back profile, launcher and installation-record changes if writing or the
  final login check fails. Symlinked profiles, modified managed blocks and
  unsupported shells are preserved with instructions for manual configuration.
- Use **Log out** consistently in installation and recovery messages.

### Install or update

Install through **COSMIC Forge → Settings → Patches**, then **log out and back in**.
If Forge was previously installed but the system compositor kept running, run
its installation again to apply the login PATH repair.

Prebuilt binaries still cover pinned COSMIC **1.10.0 and 1.11.0** on **x86_64 and
ARM64**, with **glibc 2.35+**. Shader behavior, preferences and the Settings
launcher are unchanged; no compositor is restarted during installation.

### Validation

45 installer, recovery and packaging tests pass, including real Bash, Zsh, Fish,
sh and dash login checks; migration of an existing installation; quoted paths;
preservation of profile edits; and rollback after failed writes or PATH verification.
Release CI builds and tests all four compositor binaries before publishing.

[Full source changes](https://github.com/motixo/cosmic-forge-engine/compare/engine-v0.6.0...engine-v0.6.1)
