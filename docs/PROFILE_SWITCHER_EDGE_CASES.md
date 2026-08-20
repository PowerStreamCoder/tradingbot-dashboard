# Profile Switcher - Edge Case Analysis & Mitigation

**Date:** August 16, 2026  
**Status:** Comprehensive Review  
**Purpose:** Identify and handle ALL edge cases

---

## Edge Case Categories

1. **Network & Connectivity Issues**
2. **Concurrency & Race Conditions**
3. **File System Issues**
4. **Service & Process Issues**
5. **Data Integrity Issues**
6. **Security & Authentication Issues**
7. **Timing & Synchronization Issues**
8. **User Behavior Issues**

---

## 1. Network & Connectivity Issues

### Edge Case 1.1: Dashboard → VM API Connection Lost Mid-Switch

**Scenario:** User clicks switch, dashboard calls VM API, network drops before response returns.

**Current Behavior:**
- Dashboard shows error: "Failed to communicate with bot control API"
- VM may have already switched profile (operation completed but response lost)
- User sees error but profile actually switched

**Impact:** 🔴 HIGH - User confusion, dashboard shows wrong mode

**Mitigation Added:**
```python
# In dashboard main.py - Add idempotency check
async def switch_profile(request: Request):
    # Before switching, check if already at target profile
    current_profile_response = await client.get('http://136.115.134.1:8080/current-profile')
    current = current_profile_response.json().get('profile')
    
    if current == profile:
        logger.info(f"[SWITCH-PROFILE] Already at target profile: {profile}")
        return {
            'status': 'success',
            'profile': profile,
            'message': f'Already in {profile.upper()} mode (no switch needed)',
            'profile_confirmed': True
        }
```

**Status:** ⚠️ NEEDS IMPLEMENTATION

---

### Edge Case 1.2: VM Cannot Reach Firestore During Audit Logging

**Scenario:** Profile switch succeeds but Firestore audit write fails.

**Current Behavior:**
```python
except Exception as audit_error:
    logger.error(f"Failed to write audit trail: {audit_error}")
```

**Impact:** 🟡 MEDIUM - Audit trail incomplete

**Mitigation:** ✅ ALREADY HANDLED
- Uses `finally` block so audit attempt always happens
- Logs error but doesn't fail the request
- Can manually reconstruct from journalctl logs

---

### Edge Case 1.3: Multiple Dashboard Instances Call Switch Simultaneously

**Scenario:** User opens dashboard in 2 tabs, clicks switch in both within 5 seconds.

**Current Behavior:**
- First request: Passes rate limit, starts switch
- Second request: Hits 5-second rate limit, returns HTTP 429

**Impact:** 🟢 LOW - Second request properly rejected

**Mitigation:** ✅ ALREADY HANDLED (VM API rate limiting)

---

## 2. Concurrency & Race Conditions

### Edge Case 2.1: Switch Called While Previous Switch Still In Progress

**Scenario:** Switch takes 10 seconds, user clicks again after 6 seconds.

**Current Behavior:**
- VM API rate limiter blocks second request (5-second window)
- But if exactly 5.1 seconds, could slip through

**Impact:** 🔴 HIGH - Two switches competing, systemd confusion

**Mitigation Added:**
```python
# Add lock-based mutual exclusion
from threading import Lock
switch_operation_lock = Lock()

@app.route('/switch-profile', methods=['POST', 'OPTIONS'])
def switch_profile():
    # Try to acquire lock (non-blocking)
    if not switch_operation_lock.acquire(blocking=False):
        logger.warning("[SWITCH-PROFILE] Switch already in progress")
        return jsonify({
            'status': 'error',
            'message': 'Profile switch already in progress. Please wait.'
        }), 409
    
    try:
        # ... switch logic ...
    finally:
        switch_operation_lock.release()
```

**Status:** ⚠️ NEEDS IMPLEMENTATION

---

### Edge Case 2.2: Position Created Between Check and Switch

**Scenario:**
1. Dashboard checks positions (0 found) ✅
2. Bot opens position (100 shares)
3. Dashboard forwards to VM API
4. VM switches profile
5. Now have position in wrong mode!

**Current Behavior:** NOT HANDLED - Race condition exists

**Impact:** 🔴 CRITICAL - Position in wrong trading mode

**Mitigation Added:**
```python
# Dashboard: Double-check positions right before VM call
@app.post("/api/bot-control/switch-profile")
async def switch_profile(request: Request):
    # First position check
    position_count_1 = check_positions()
    if position_count_1 > 0:
        raise HTTPException(400, "...")
    
    # Forward to VM API
    response = await client.post('http://136.115.134.1:8080/switch-profile', ...)
    
    # VM API: Also check bot is stopped (ensures no trading activity)
    # This is already implemented:
    if bot_status == 'active':
        return error  # Bots must be stopped
```

**Status:** ✅ MITIGATED (Bot must be stopped = no new positions possible)

---

## 3. File System Issues

### Edge Case 3.1: Profile File Deleted During Switch

**Scenario:** Someone SSHs into VM and deletes `/home/i030983/.trading_profile` during switch.

**Current Behavior:**
- Backup fails (file doesn't exist)
- Write succeeds (creates new file)
- Restart succeeds
- Rollback succeeds (uses backup from previous switch)

**Impact:** 🟡 MEDIUM - Backup of non-existent file

**Mitigation Added:**
```python
# Check file exists before backup
if os.path.exists(profile_file):
    shutil.copy2(profile_file, backup_file)
    logger.info(f"Backed up to: {backup_file}")
else:
    logger.warning(f"Profile file doesn't exist, creating new one")
    # Still create backup of default value
    with open(backup_file, 'w') as f:
        f.write('TRADING_PROFILE=paper\n')
```

**Status:** ⚠️ NEEDS ENHANCEMENT

---

### Edge Case 3.2: Disk Full - Cannot Write Profile File

**Scenario:** Disk is full, cannot write new profile file.

**Current Behavior:**
```python
with open(profile_file, 'w') as f:
    f.write(f'TRADING_PROFILE={new_profile}\n')
```
- Raises `IOError` or `OSError`
- Caught by generic `except Exception`
- Returns 500 error

**Impact:** 🔴 HIGH - Switch fails but hard to diagnose

**Mitigation Added:**
```python
try:
    with open(profile_file, 'w') as f:
        f.write(f'TRADING_PROFILE={new_profile}\n')
except IOError as io_error:
    if 'No space left on device' in str(io_error):
        logger.error("[SWITCH-PROFILE] DISK FULL - cannot write profile")
        return jsonify({
            'status': 'error',
            'message': 'CRITICAL: Disk full on VM. Cannot write profile file. Free up space and retry.'
        }), 500
    raise
```

**Status:** ⚠️ NEEDS IMPLEMENTATION

---

### Edge Case 3.3: Profile File Corrupted (Invalid Syntax)

**Scenario:** Profile file contains garbage data.

**Current Behavior:**
- `get_profile_from_env()` reads file
- Tries to parse `TRADING_PROFILE=<value>`
- Returns "paper" if invalid (fail-safe default)

**Impact:** 🟢 LOW - Defaults to safe mode

**Mitigation:** ✅ ALREADY HANDLED (Defensive parsing + default to paper)

---

### Edge Case 3.4: Backup Directory Doesn't Exist

**Scenario:** `/home/i030983/backups` deleted or never created.

**Current Behavior:**
```python
os.makedirs(backup_dir, exist_ok=True)
```

**Impact:** 🟢 LOW - Automatically creates directory

**Mitigation:** ✅ ALREADY HANDLED

---

### Edge Case 3.5: Backup Directory Full (No Space for Backup)

**Scenario:** Hundreds of old backups fill disk.

**Current Behavior:** Creates new backup, may fail if disk full.

**Impact:** 🟡 MEDIUM - Old backups accumulate forever

**Mitigation Added:**
```python
# Clean up old backups (keep last 30 days)
def cleanup_old_backups(backup_dir, days=30):
    cutoff = datetime.now() - timedelta(days=days)
    for backup_file in glob.glob(f'{backup_dir}/trading_profile_backup_*.txt'):
        file_time = datetime.fromtimestamp(os.path.getmtime(backup_file))
        if file_time < cutoff:
            os.remove(backup_file)
            logger.info(f"Deleted old backup: {backup_file}")

# Call before creating new backup
cleanup_old_backups(backup_dir)
```

**Status:** ⚠️ NEEDS IMPLEMENTATION

---

## 4. Service & Process Issues

### Edge Case 4.1: systemd Service File Missing or Corrupted

**Scenario:** Someone deletes or corrupts `trading-bot-manager.service` file.

**Current Behavior:**
- `systemctl restart` fails with error
- Caught by subprocess error handling
- Returns 500 error

**Impact:** 🔴 HIGH - Cannot restart bots

**Mitigation Added:**
```python
# Verify service file exists before restart
service_file = '/home/i030983/tradingbots/services/trading-bot-manager.service'
if not os.path.exists(service_file):
    logger.error(f"[SWITCH-PROFILE] Service file missing: {service_file}")
    return jsonify({
        'status': 'error',
        'message': 'CRITICAL: Bot service file missing. Restore from git and retry.'
    }), 500
```

**Status:** ⚠️ NEEDS IMPLEMENTATION

---

### Edge Case 4.2: systemd Daemon Not Running

**Scenario:** systemd itself is down or unresponsive.

**Current Behavior:**
- `systemctl` commands hang or fail
- Subprocess timeout (15 seconds)
- Returns timeout error

**Impact:** 🔴 CRITICAL - Entire system broken

**Mitigation:** ✅ ALREADY HANDLED (subprocess timeout)

---

### Edge Case 4.3: Bot Process Crashes Immediately After Start

**Scenario:** Bot starts but crashes within 5 seconds (before verification).

**Current Behavior:**
- Wait 5 seconds
- Check status = "failed" or "inactive"
- Triggers automatic rollback ✅

**Impact:** 🟢 LOW - Rollback handles this

**Mitigation:** ✅ ALREADY HANDLED (Automatic rollback)

---

### Edge Case 4.4: Bot Starts Then Crashes After 10 Seconds

**Scenario:** Bot starts successfully, verification passes, then crashes.

**Current Behavior:**
- Switch returns success
- User sees "✓ Switched to LIVE"
- 10 seconds later, bot crashes
- Dashboard shows bot as stopped

**Impact:** 🟡 MEDIUM - False success reported

**Mitigation Added:**
```python
# Extend verification wait time
time.sleep(10)  # Increased from 5 to 10 seconds

# Also check for crash indicators in logs
log_result = subprocess.run([...])
if 'CRITICAL' in log_result.stdout or 'Traceback' in log_result.stdout:
    logger.error("[SWITCH-PROFILE] Bot logs show errors")
    # Trigger rollback
```

**Status:** ⚠️ NEEDS IMPLEMENTATION

---

### Edge Case 4.5: EnvironmentFile Directive Missing from Service File

**Scenario:** Someone removes `EnvironmentFile=/home/i030983/.trading_profile` from service file.

**Current Behavior:**
- Profile file updated
- Service restarted
- But bots don't read new profile!
- Uses `TRADING_PROFILE` env var if set, else defaults to "paper"

**Impact:** 🔴 HIGH - Silent failure (profile doesn't actually switch)

**Mitigation Added:**
```python
# Verify service file has EnvironmentFile directive
def verify_service_config():
    with open('/home/i030983/tradingbots/services/trading-bot-manager.service') as f:
        content = f.read()
        if 'EnvironmentFile=/home/i030983/.trading_profile' not in content:
            return False
    return True

# Check before switch
if not verify_service_config():
    logger.error("[SWITCH-PROFILE] Service file missing EnvironmentFile directive")
    return jsonify({
        'status': 'error',
        'message': 'CRITICAL: Service configuration invalid. EnvironmentFile directive missing.'
    }), 500
```

**Status:** ⚠️ NEEDS IMPLEMENTATION

---

## 5. Data Integrity Issues

### Edge Case 5.1: Firestore bot_overview Document Corrupted

**Scenario:** bot_overview has malformed data (missing fields, wrong types).

**Current Behavior:**
```python
for bot in bots:
    for bucket in bot.get('buckets', []):
        shares = bucket.get('shares', 0)  # Defaults to 0 if missing
```

**Impact:** 🟢 LOW - Defensive `get()` with defaults

**Mitigation:** ✅ ALREADY HANDLED

---

### Edge Case 5.2: Firestore Connection Timeout During Position Check

**Scenario:** Firestore query hangs or times out.

**Current Behavior:** No explicit timeout set, uses default httpx timeout (5 seconds).

**Impact:** 🟡 MEDIUM - Slow response, user waits

**Mitigation Added:**
```python
# Set explicit Firestore timeout
from google.cloud.firestore_v1.types import TransactionOptions
from google.api_core import retry, timeout

db = firestore.Client()
bot_overview = db.collection('bot_overview').document('overview').get(
    timeout=5.0  # 5-second timeout
)
```

**Status:** ⚠️ NEEDS IMPLEMENTATION

---

### Edge Case 5.3: Audit Trail Write Fails Silently

**Scenario:** Firestore write succeeds but data doesn't appear (eventual consistency).

**Current Behavior:**
- Write appears successful
- Data may take seconds to appear in queries

**Impact:** 🟢 LOW - Expected behavior for Firestore

**Mitigation:** ✅ ACCEPTABLE (Firestore eventually consistent)

---

## 6. Security & Authentication Issues

### Edge Case 6.1: Dashboard Session Expired During Switch

**Scenario:** User's session expires mid-switch request.

**Current Behavior:**
- Dashboard middleware checks session before route handler
- Returns 401 if expired
- Switch never called

**Impact:** 🟢 LOW - Properly blocked

**Mitigation:** ✅ ALREADY HANDLED (Authentication middleware)

---

### Edge Case 6.2: Attacker Spoofs User IP in Audit Trail

**Scenario:** Attacker sets X-Forwarded-For header to fake IP.

**Current Behavior:**
```python
'user_ip': request.client.host
```
- Uses direct connection IP (not header)
- Cloud Run provides real client IP

**Impact:** 🟢 LOW - Hard to spoof

**Mitigation:** ✅ ALREADY HANDLED (request.client.host is trustworthy from Cloud Run)

---

### Edge Case 6.3: SQL Injection in Profile Parameter

**Scenario:** Attacker sends `{"profile": "'; DROP TABLE users; --"}`.

**Current Behavior:**
```python
if profile not in ['paper', 'live']:
    raise HTTPException(400, "Invalid profile")
```

**Impact:** 🟢 LOW - Input validation blocks this

**Mitigation:** ✅ ALREADY HANDLED (Whitelist validation)

---

## 7. Timing & Synchronization Issues

### Edge Case 7.1: Clock Skew Between Dashboard and VM

**Scenario:** VM clock is 5 minutes behind dashboard clock.

**Current Behavior:**
- Timestamps in audit trail may appear out of order
- Rate limiting uses VM clock (consistent within VM)

**Impact:** 🟢 LOW - Functionally works, timestamps may be confusing

**Mitigation:** Monitor with NTP sync, no code change needed.

---

### Edge Case 7.2: Daylight Saving Time Transition

**Scenario:** System switches DST during profile switch operation.

**Current Behavior:**
- Python `datetime.now()` uses system timezone
- May cause timestamp to jump forward/backward 1 hour

**Impact:** 🟢 LOW - Cosmetic issue only

**Mitigation:** Use UTC for audit timestamps (recommended):
```python
'timestamp': datetime.utcnow()
```

**Status:** ⚠️ RECOMMENDED

---

### Edge Case 7.3: Rate Limit Window Boundary

**Scenario:** User switches at T=0, then at T=4.99, then T=9.99 (just under 5-second windows).

**Current Behavior:**
- T=0: Allowed
- T=4.99: Blocked (< 5 seconds)
- T=9.99: Allowed (>= 5 seconds from T=0)

**Impact:** 🟢 LOW - Expected behavior

**Mitigation:** ✅ ALREADY HANDLED

---

## 8. User Behavior Issues

### Edge Case 8.1: User Clicks Switch Then Immediately Closes Browser

**Scenario:** User clicks switch, sees loading state, closes tab before completion.

**Current Behavior:**
- Switch continues on server (fire-and-forget)
- User doesn't see result
- Next page load shows new profile

**Impact:** 🟢 LOW - Operation completes successfully

**Mitigation:** ✅ ACCEPTABLE (User can see result by refreshing)

---

### Edge Case 8.2: User Switches Rapidly Back and Forth

**Scenario:** Switch paper→live→paper→live in quick succession.

**Current Behavior:**
- First: Succeeds
- Second: Rate limited (5-second cooldown)
- Third: Rate limited
- Fourth: Rate limited

**Impact:** 🟢 LOW - Rate limiting prevents abuse

**Mitigation:** ✅ ALREADY HANDLED

---

### Edge Case 8.3: User Clicks Switch While Bots Are Trading

**Scenario:** User tries to switch while bots have active orders (not positions yet).

**Current Behavior:**
- Position check passes (no positions yet)
- Bot status check: Bots are "active"
- VM API blocks: "Bots must be stopped first"

**Impact:** 🟢 LOW - Properly blocked

**Mitigation:** ✅ ALREADY HANDLED

---

### Edge Case 8.4: User Forces Browser Refresh During Switch

**Scenario:** User clicks switch, hits F5 before completion.

**Current Behavior:**
- Browser cancels request (network abort)
- Switch continues on server
- Page reloads, shows loading state
- Eventually shows new profile when switch completes

**Impact:** 🟢 LOW - No data corruption

**Mitigation:** ✅ ACCEPTABLE

---

## 9. Configuration Issues

### Edge Case 9.1: Profile JSON Files Have Syntax Errors

**Scenario:** `paper.json` or `live.json` has invalid JSON.

**Current Behavior:**
- Bot startup fails when loading profile
- Service status = "failed"
- Automatic rollback triggered ✅

**Impact:** 🟢 LOW - Rollback handles this

**Mitigation:** ✅ ALREADY HANDLED

---

### Edge Case 9.2: Profile JSON Missing Required Fields

**Scenario:** `live.json` missing `tws_port` field.

**Current Behavior:**
- `config_loader.py` has defaults for missing fields
- Bot starts with default values

**Impact:** 🟡 MEDIUM - May connect to wrong port

**Mitigation Added:**
```python
# Validate critical fields exist
required_fields = ['tws_port', 'market_data_type', 'capital_per_bucket']
for field in required_fields:
    if field not in profile_data:
        raise ValueError(f"Profile missing required field: {field}")
```

**Status:** ⚠️ NEEDS IMPLEMENTATION (in config_loader.py)

---

### Edge Case 9.3: Both Profiles Point to Same TWS Port

**Scenario:** Someone configures both paper and live to use port 7496.

**Current Behavior:**
- Both modes connect to live IBKR
- Paper mode thinks it's paper but actually trades real money! 🚨

**Impact:** 🔴 CRITICAL - Massive risk

**Mitigation Added:**
```python
# Validate profile configuration
def validate_profile_config(profile_data, profile_name):
    if profile_name == 'paper':
        if profile_data.get('tws_port') not in [4002, 7497]:  # 7497 = paper live
            logger.error(f"Paper profile using wrong port: {profile_data.get('tws_port')}")
            raise ValueError("Paper profile must use port 4002 or 7497")
    elif profile_name == 'live':
        if profile_data.get('tws_port') != 7496:
            logger.error(f"Live profile using wrong port: {profile_data.get('tws_port')}")
            raise ValueError("Live profile must use port 7496")
```

**Status:** 🔴 CRITICAL - NEEDS IMMEDIATE IMPLEMENTATION

---

## 10. Rollback Edge Cases

### Edge Case 10.1: All Backups Corrupted

**Scenario:** Every backup file in `/home/i030983/backups/` is corrupted.

**Current Behavior:**
- Rollback finds latest backup
- Tries to copy corrupted file
- Restart fails
- Returns CRITICAL error

**Impact:** 🔴 HIGH - Cannot roll back

**Mitigation Added:**
```python
# Validate backup before using
def validate_backup_file(backup_path):
    try:
        with open(backup_path, 'r') as f:
            content = f.read()
            # Check format: TRADING_PROFILE=paper or TRADING_PROFILE=live
            if not re.match(r'^TRADING_PROFILE=(paper|live)\s*$', content):
                return False
        return True
    except:
        return False

# Find first valid backup
for backup_file in backup_files:
    if validate_backup_file(backup_file):
        latest_backup = backup_file
        break
else:
    # No valid backups found
    logger.error("No valid backups available")
    return error
```

**Status:** ⚠️ NEEDS IMPLEMENTATION

---

### Edge Case 10.2: Rollback Succeeds But Bot Connects to Wrong Port

**Scenario:**
- Switch to live fails
- Rollback restores paper profile
- Bot restarts but still connects to port 7496 (live)

**Current Behavior:**
- Bot would fail to connect (port mismatch)
- Service status = "failed"
- Rollback verification catches this

**Impact:** 🟢 LOW - Rollback verification handles this

**Mitigation:** ✅ ALREADY HANDLED

---

### Edge Case 10.3: Rollback During Disk Full Condition

**Scenario:** Disk fills up during switch, rollback also fails to write.

**Current Behavior:**
- Rollback copy fails with IOError
- Returns CRITICAL error
- Manual intervention required

**Impact:** 🔴 HIGH - System stuck

**Mitigation:**
```python
# Check disk space before rollback
import shutil
stat = shutil.disk_usage('/home/i030983')
free_mb = stat.free / (1024 * 1024)
if free_mb < 10:  # Less than 10MB free
    logger.error(f"[SWITCH-PROFILE] Insufficient disk space for rollback: {free_mb}MB")
    return jsonify({
        'status': 'error',
        'message': f'CRITICAL: Disk nearly full ({free_mb:.1f}MB free). Cannot rollback safely. Free up space immediately.'
    }), 500
```

**Status:** ⚠️ NEEDS IMPLEMENTATION

---

## Summary of Required Implementations

### 🔴 CRITICAL (Must Implement Before Production)

1. **Edge Case 9.3** - Validate TWS port matches profile type
   - RISK: Paper mode could connect to live TWS
   - IMPACT: Real money trades in test mode

### 🟡 HIGH PRIORITY (Implement Soon)

2. **Edge Case 1.1** - Idempotency check (already at target profile)
3. **Edge Case 2.1** - Lock-based mutual exclusion for concurrent switches
4. **Edge Case 3.2** - Better disk full error messages
5. **Edge Case 3.5** - Clean up old backups (retention policy)
6. **Edge Case 4.5** - Verify EnvironmentFile directive exists
7. **Edge Case 10.1** - Validate backup files before using

### 🟢 MEDIUM PRIORITY (Nice to Have)

8. **Edge Case 3.1** - Better handling of missing profile file
9. **Edge Case 4.4** - Extend verification wait time to 10 seconds
10. **Edge Case 5.2** - Explicit Firestore timeout
11. **Edge Case 7.2** - Use UTC for audit timestamps
12. **Edge Case 9.2** - Validate profile JSON has required fields
13. **Edge Case 10.3** - Check disk space before rollback

---

## Testing Matrix

| Edge Case | Test Method | Expected Result |
|-----------|-------------|-----------------|
| 1.1 Network loss | Disconnect network mid-switch | Error shown, idempotent retry works |
| 2.1 Concurrent switches | Two tabs, both click switch | Second blocked with HTTP 409 |
| 3.2 Disk full | `dd if=/dev/zero of=bigfile` | Error: "Disk full" |
| 3.5 Backup accumulation | Create 100 old backups | Only last 30 days kept |
| 4.3 Bot immediate crash | Corrupt config to cause crash | Rollback triggered |
| 9.3 Wrong port in profile | Edit paper.json port to 7496 | Validation error on load |
| 10.1 Corrupted backups | `echo "garbage" > backups/*` | Finds valid backup or errors |

---

**Status:** 13 edge cases need implementation  
**Priority:** 1 CRITICAL, 6 HIGH, 6 MEDIUM  
**Next Step:** Implement CRITICAL edge case 9.3 first

