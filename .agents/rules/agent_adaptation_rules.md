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
   - Do NOT run manual python execution commands or bypass the GitHub Actions deployment workflow.
