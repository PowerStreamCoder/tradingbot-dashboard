# Agent Adaptation & Self-Healing Guidelines

These rules optimize agent behavior for high-efficiency diagnosis, token reduction, and automatic learning persistence.

---

## 1. Fast Diagnostic Workflow (Token Reduction)

Before running open-ended repository searches or reading full files:
1. **Check Error Fingerprints First**: Search `.agents/knowledge/error_fingerprints.json` or run `python3 tradingbot-tools/scripts/diagnose_incident.py --query "<error stack or symptom>"`.
2. **Targeted Line Range Views**: When inspecting identified files, use `view_file` with precise `StartLine` and `EndLine` parameters rather than pulling entire 1000+ line modules.
3. **Targeted Code Search**: Use `grep_search` with specific `Includes` filters (e.g. `Includes: ["*.py"]`) and non-regex literal matches before resorting to broader scans.

---

## 2. Error Fingerprint Catalog Protocol

When encountering runtime exceptions, build failures, or logic bugs:
1. Consult `.agents/knowledge/error_fingerprints.json` to find existing root-cause signatures and resolution recipes.
2. If a match is found, apply the resolution recipe immediately without redundant investigation steps.

---

## 3. Automatic Incident Logging Protocol (No User Prompt Needed)

When a new bug or unexpected failure is identified and fixed:
1. Automatically update `.agents/knowledge/error_fingerprints.json` with the new error signature, root cause, and fix summary.
2. Run `python3 tradingbot-tools/scripts/sync_agent_rules.py` to propagate updated rules and fingerprints across all workspace repositories.
3. Automatically append an entry to `tradingbot-documentation/bug-resolution/BUG_RESOLUTION_LOG.md` detailing:
   - Date & Symptom
   - Root Cause & Exact Line Numbers
   - Fix Applied & Verification Result
   - Defensive measures added to prevent recurrence
4. Do not prompt the user for confirmation prior to logging; update the incident memory autonomously.

---

## 4. Mandatory Deployment Workflow & Strict Prohibitions

1. **STRICT PROHIBITION — NO DIRECT SCP / FILE COPY TO VM**:
   - Direct file transfers (`gcloud compute scp`, `rsync`, or manual edits) of code files to `trading-bot-vm` are **STRICTLY PROHIBITED AT ALL COSTS**.
   - NEVER copy Python code files, modules, or configuration directly to the VM.

2. **MANDATORY GIT & GITHUB ACTIONS DEPLOYMENT PIPELINE**:
   - All code fixes, enhancements, and configuration changes **MUST** follow this exact flow:
     1. Local code modification & validation.
     2. Commit changes to Git repository.
     3. Push commits to trigger automated deployment via **GitHub Actions**.
   - The deployment to `trading-bot-vm` MUST occur exclusively through the GitHub Actions CI/CD pipeline.

3. **VM INTERACTION RESTRICTIONS**:
   - `gcloud compute ssh` may ONLY be used for non-mutating log inspection (`journalctl`) or system health status verification.
   - Direct SSH with the local deploy key is allowed for exactly the same read-only purposes: `ssh -i ~/.ssh/tradingbot_deploy i030983@<VM_IP> '<read-only command>'`.
   - Do NOT run manual python execution commands or bypass the GitHub Actions deployment workflow.
   - **The VM's external IP is not fixed** — it changes whenever the instance is recreated or has its address deleted and re-added. Always resolve it first with `gcloud compute instances list` (instance `trading-bot-vm`, zone `us-central1-a`) instead of trusting an IP written in a note, a doc, or memory. A stale IP produces a connect timeout that looks exactly like "the VM is unreachable" while the VM is perfectly healthy — that false conclusion has already caused unnecessary deploys.

4. **DEPLOYMENT TRIGGER DISCIPLINE — DO NOT TRIGGER THE VM DEPLOY MANUALLY**:
   - **Do NOT dispatch `deploy-to-vm.yml` (or any VM-deploying workflow) manually** to land a change, to "make sure" a commit is live, or to test the deploy path. A `workflow_dispatch` on the deploy workflow **restarts the whole trading fleet** and, because `workflow_dispatch` bypasses the "VM is already up-to-date" check, it deploys even when the VM is already current.
   - **Let the daily scheduled deploy (`cron: '0 3 * * *'`) deliver the code.** Merging to `main` is enough; the scheduled run picks the commit up.
   - A manual deploy is permitted **only when it is urgent and mandatory** — i.e. a live production incident or a broken state on the VM that cannot wait for the next scheduled run. If the schedule is close enough, wait for it.
   - When a manual deploy is genuinely required, state the reason explicitly before triggering it, and prefer the pre-market window (the fleet is stopped from 16:39 ET to 08:51 ET) so the fleet bounce is harmless. Never trigger one mid-session without saying why.
   - A successful merge is **not** a reason to deploy. Report "merged, the scheduled deploy will pick it up" instead.

5. **VERIFY THE FLEET WITHOUT DEPLOYING**:
   - The VM is reachable read-only from the workstation. **Never dispatch a deploy just to obtain a status reading** — that is a fleet restart disguised as a health check.
   - Resolve the host: `gcloud compute instances list` → `trading-bot-vm` / `us-central1-a` → NAT IP (34.46.214.78 as of 2026-10-21; re-resolve, never hardcode).
   - Read-only probes (`IP=<nat ip>`):
     | Question | Command |
     |---|---|
     | Is the manager up? | `ssh -i ~/.ssh/tradingbot_deploy i030983@$IP 'systemctl is-active trading-bot-manager'` |
     | How many bots are actually trading? | `ssh -i ~/.ssh/tradingbot_deploy i030983@$IP 'ps -eo cmd \| grep "[l]auncher.py" \| wc -l'` |
     | When does the fleet start/stop? | `ssh -i ~/.ssh/tradingbot_deploy i030983@$IP 'systemctl list-timers --all \| grep trading-bot-manager'` |
     | Which commit is live? | `ssh -i ~/.ssh/tradingbot_deploy i030983@$IP 'cat $HOME/backups/bots/latest_deployed_commit.txt'` |
     | What did the manager just do? | `ssh -i ~/.ssh/tradingbot_deploy i030983@$IP 'journalctl -u trading-bot-manager -n 40 --no-pager'` |
     | Is the command API alive? | `curl -s http://$IP:8080/health` (unauthenticated, returns `{"status":"healthy"}`) |
   - **Do not judge fleet state by the manager unit alone.** Outside market hours the manager unit is `active` but deliberately idle ("Outside market hours: supervisor idle, N bot(s) NOT started") and zero bot processes exist — that is the healthy off-hours state. The fleet runs 08:51 → 16:39 ET (weekday timers `trading-bot-manager-start.timer` / `-stop.timer`).
   - `gh run view --log` on an existing run remains the way to read deploy history.
