## Engine 0.6.2

- Support the Pop!_OS 24.04 compositor snapshot `ec674cd`, alongside COSMIC 1.10/1.11.
- Report the installed package and commit when a build is not supported.
- Fall back to the system compositor after package updates; preserve backups and Restore.
- Check official upstream builds daily and open compatibility PRs with build tests.

Shader ABI **1** and renderer revision **7** are unchanged. Update through **Settings → Patches**, then log out and back in.

Fixes [cosmic-forge#2](https://github.com/motixo/cosmic-forge/issues/2).
