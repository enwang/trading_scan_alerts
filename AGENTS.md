# Repo Instructions

- Never push directly to `main`.
- Always create and push a feature branch first so the user can review the code before merging to `main`.
- Do not assume local cron or scheduler setup carries over to a VM; VM scheduling must be installed on the VM explicitly.
- Prefer portable helper scripts that avoid hardcoded local machine paths so the same repo can run on Ubuntu VMs and local macOS setups.
