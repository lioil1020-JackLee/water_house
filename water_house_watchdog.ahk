#Requires AutoHotkey v2.0
#SingleInstance Off
;@Ahk2Exe-SetVersion 4.1.1
;@Ahk2Exe-SetDescription Water House session-scoped health watchdog

; Only this mode is used by automated tests. No monitored-process discovery,
; launches, window messages, or termination occur in validation mode.
if A_Args.Length && (A_Args[1] = "--validate-only" || A_Args[1] = "--self-test") {
    RunSelfTests(A_Args.Length >= 2 ? A_Args[2] : "")
    ExitApp(0)
}

global Watch := 0
InitializeWatchdog()

InitializeWatchdog() {
    global Watch
    session := 0
    if !DllCall("Kernel32\ProcessIdToSessionId", "uint", DllCall("GetCurrentProcessId"), "uint*", &session)
        ExitApp(2)
    target := NormalizePath(A_ScriptDir "\water_house.exe")
    mutex := DllCall("CreateMutexW", "ptr", 0, "int", 0,
        "str", "Local\WaterHouseWatchdog-" session "-" PathHash(target), "ptr")
    if !mutex || A_LastError = 183
        ExitApp(0)
    logDir := EnvGet("LOCALAPPDATA") "\WaterHouse\logs"
    DirCreate(logDir)
    Watch := {
        target: target, session: session, mutex: mutex,
        logDir: logDir, logPath: logDir "\water_house_watchdog.log",
        wmi: ComObjGet("winmgmts:{impersonationLevel=impersonate}!\\.\root\cimv2"),
        family: Map(), rootPid: 0, startedAt: 0, faultSince: 0,
        lastHeartbeat: false, stoppingAt: 0, nextLaunchAt: 0,
        state: "", recoveryTimes: [], rapidFailures: 0, healthySince: 0, busy: false,
    }
    OnExit(CleanupWatchdog)
    SetWatchdogIcon()
    A_TrayMenu.Delete()
    A_TrayMenu.Add("Open Water House", OpenWaterHouse)
    A_TrayMenu.Add("Open diagnostic logs", (*) => Run('explorer.exe "' Watch.logDir '"'))
    A_TrayMenu.Add()
    A_TrayMenu.Add("Exit watchdog", (*) => ExitApp(0))
    A_IconTip := "Water House watchdog 4.1.1"
    Log("START version=4.1.1 session=" session " target=" target)
    if !FileExist(target) {
        Log("ERROR target executable is missing")
        ExitApp(2)
    }
    CheckWatchdog()
    SetTimer(CheckWatchdog, 5000)
}

CheckWatchdog() {
    global Watch
    if Watch.busy
        return
    Watch.busy := true
    try {
        CheckWatchdogInner()
    } catch Error as err {
        ; Discovery/permission errors never authorize killing or starting copies.
        LogState("check_error", "ERROR check failed; no process action: " err.Message)
    } finally {
        Watch.busy := false
    }
}

CheckWatchdogInner() {
    global Watch
    now := UnixNow()
    scan := DiscoverProcesses()
    if scan.unknown {
        LogState("unknown_identity", "ERROR same-name process has unreadable path; no process action")
        return
    }
    if !scan.records.Length {
        if Watch.rootPid {
            exitCode := -1
            if Watch.family.Has(Watch.rootPid) {
                heldCode := 0
                if DllCall("GetExitCodeProcess", "ptr", Watch.family[Watch.rootPid], "uint*", &heldCode)
                    exitCode := heldCode
            }
            restart := EvaluateExitRestart(now, Watch.startedAt, exitCode,
                Watch.rapidFailures, Watch.stoppingAt != 0)
            Watch.rapidFailures := restart.rapidFailures
            Watch.nextLaunchAt := now + restart.delay
            Log("EXIT root_pid=" Watch.rootPid " code=" exitCode " runtime=" (now - Watch.startedAt)
                "s restart_delay=" restart.delay "s rapid_failures=" Watch.rapidFailures)
        }
        CloseFamilyHandles()
        if now >= Watch.nextLaunchAt {
            try {
                Run('"' Watch.target '"', A_ScriptDir, , &pid)
                Watch.nextLaunchAt := 0
                Watch.rootPid := pid, Watch.startedAt := now
                ; Retain a handle immediately, including processes that exit
                ; before the next discovery tick. Exit code 0 never earns delay.
                handle := OpenOwnedProcess(pid)
                if handle
                    Watch.family[pid] := handle
                Log("LAUNCH pid=" pid " session=" Watch.session)
            } catch Error as err {
                Watch.rapidFailures += 1
                delay := RapidFailureDelay(Watch.rapidFailures)
                Watch.nextLaunchAt := now + delay
                Log("ERROR launch failed; retry_delay=" delay "s reason=" err.Message)
            }
        } else {
            LogState("launch_backoff", "WAIT repeated rapid failures; retry_at=" Watch.nextLaunchAt)
        }
        return
    }
    rootPid := SingleFamilyRoot(scan.records)
    if !rootPid {
        CloseFamilyHandles()
        LogState("multiple_instances", "ERROR multiple independent instances match path/session; no termination")
        return
    }
    if rootPid != Watch.rootPid || !Watch.family.Has(rootPid) || !HandleAlive(Watch.family[rootPid]) {
        CloseFamilyHandles()
        Watch.rootPid := rootPid
        Watch.startedAt := now
        Log("ADOPT root_pid=" rootPid " session=" Watch.session)
    }
    for record in scan.records {
        if Watch.family.Has(record.pid) && !HandleAlive(Watch.family[record.pid]) {
            DllCall("CloseHandle", "ptr", Watch.family[record.pid])
            Watch.family.Delete(record.pid)
        }
        if !Watch.family.Has(record.pid) {
            handle := OpenOwnedProcess(record.pid)
            if !handle {
                LogState("access_denied", "ERROR cannot open/validate monitored instance; no termination")
                return
            }
            Watch.family[record.pid] := handle
        }
    }
    if Watch.stoppingAt {
        if now - Watch.stoppingAt >= 8 {
            ; Held handles refer to the original kernel process objects, even
            ; if a PID is later reused. Recheck path/session before terminating.
            for pid, handle in Watch.family {
                if HandleAlive(handle) && HandleMatchesTarget(handle, pid) {
                    if DllCall("TerminateProcess", "ptr", handle, "uint", 1)
                        Log("TERMINATE owned_pid=" pid)
                    else
                        Log("ERROR termination denied owned_pid=" pid)
                }
            }
        }
        return
    }
    observed := ReadOwnedHeartbeat()
    if observed.heartbeat
        Watch.lastHeartbeat := observed.heartbeat
    if observed.collision && !Watch.lastHeartbeat {
        LogState("heartbeat_collision", "ERROR shared heartbeat belongs to another live session; process-only supervision")
        return
    }
    decision := EvaluateHealth(now, Watch.startedAt, Watch.lastHeartbeat, Watch.faultSince)
    Watch.faultSince := decision.faultSince
    if decision.action = "recover" {
        if !RestartBudgetAvailable(now, Watch.recoveryTimes) {
            LogState("recovery_backoff", "ERROR recovery deferred by restart budget")
            return
        }
        Log("RECOVER reason=" decision.reason " root_pid=" Watch.rootPid)
        Watch.recoveryTimes.Push(now)
        Watch.healthySince := 0
        Watch.stoppingAt := now
        ; Request a normal close first. A confirmation dialog may intercept it;
        ; the bounded grace above then terminates only these validated handles.
        for pid, handle in Watch.family {
            if HandleAlive(handle) && HandleMatchesTarget(handle, pid) {
                for hwnd in WinGetList("ahk_pid " pid) {
                    if DllCall("IsWindowVisible", "ptr", hwnd)
                        try PostMessage(0x0010, 0, 0, , "ahk_id " hwnd)
                }
            }
        }
        return
    }
    if decision.action = "healthy" {
        if !Watch.healthySince
            Watch.healthySince := now
        if now - Watch.healthySince >= 60 {
            Watch.rapidFailures := 0
            Watch.recoveryTimes := []
        }
    } else {
        Watch.healthySince := 0
    }
    LogState(decision.reason, "HEALTH " decision.reason " root_pid=" Watch.rootPid)
}

SetWatchdogIcon() {
    try {
        if A_IsCompiled {
            ; Ahk2Exe embeds the project's icon as the first EXE icon group.
            TraySetIcon(A_ScriptFullPath, 1)
        } else {
            iconPath := A_ScriptDir "\享溫泉.ico"
            if !FileExist(iconPath)
                iconPath := A_ScriptDir "\img\享溫泉.ico"
            TraySetIcon(iconPath)
        }
    } catch Error as err {
        Log("WARNING tray icon could not be set: " err.Message)
    }
}

DiscoverProcesses() {
    global Watch
    records := [], unknown := false
    for process in Watch.wmi.ExecQuery("SELECT ProcessId,ParentProcessId,ExecutablePath,SessionId FROM Win32_Process WHERE Name='water_house.exe'") {
        if process.SessionId != Watch.session
            continue
        path := process.ExecutablePath ""
        if path = "" {
            ; WMI can retain a just-exited PID with an empty image path.
            ; Check its kernel object before treating a stale row as an
            ; unidentifiable live process that blocks every restart.
            handle := DllCall("OpenProcess", "uint", 0x101000, "int", false,
                "uint", process.ProcessId, "ptr")
            if !handle {
                if A_LastError = 87 ; ERROR_INVALID_PARAMETER: PID is gone.
                    continue
                unknown := true
                continue
            }
            try {
                if !HandleAlive(handle)
                    continue
                identity := InspectProcessHandle(handle, process.ProcessId)
                if !identity {
                    unknown := true
                    continue
                }
                path := identity.path
            } finally {
                DllCall("CloseHandle", "ptr", handle)
            }
        }
        if IsExpectedIdentity(path, process.SessionId, Watch.target, Watch.session)
            records.Push({pid: process.ProcessId + 0, parent: process.ParentProcessId + 0})
    }
    return {records: records, unknown: unknown}
}

SingleFamilyRoot(records) {
    pids := Map()
    for record in records
        pids[record.pid] := true
    roots := []
    for record in records {
        if !pids.Has(record.parent)
            roots.Push(record.pid)
    }
    return roots.Length = 1 ? roots[1] : 0
}

OpenOwnedProcess(pid) {
    ; PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE | PROCESS_TERMINATE
    handle := DllCall("OpenProcess", "uint", 0x101001, "int", false, "uint", pid, "ptr")
    if handle && HandleMatchesTarget(handle, pid)
        return handle
    if handle
        DllCall("CloseHandle", "ptr", handle)
    return 0
}

HandleMatchesTarget(handle, pid) {
    global Watch
    identity := InspectProcessHandle(handle, pid)
    return identity && IsExpectedIdentity(identity.path, identity.session, Watch.target, Watch.session)
}

InspectProcessHandle(handle, pid) {
    if DllCall("GetProcessId", "ptr", handle, "uint") != pid
        return false
    session := 0
    if !DllCall("Kernel32\ProcessIdToSessionId", "uint", pid, "uint*", &session)
        return false
    pathBuffer := Buffer(65536, 0), chars := 32768
    if !DllCall("QueryFullProcessImageNameW", "ptr", handle, "uint", 0, "ptr", pathBuffer, "uint*", &chars)
        return false
    return {path: StrGet(pathBuffer, chars, "UTF-16"), session: session}
}

HandleAlive(handle) {
    return DllCall("WaitForSingleObject", "ptr", handle, "uint", 0, "uint") = 258
}

IsExpectedIdentity(path, session, expectedPath, expectedSession) {
    return session = expectedSession && NormalizePath(path) = NormalizePath(expectedPath)
}

ReadOwnedHeartbeat() {
    global Watch
    newest := false, collision := false
    for pid, handle in Watch.family {
        if !HandleAlive(handle)
            continue
        heartbeat := TryReadHeartbeat(Watch.logDir "\water_house." pid ".heartbeat")
        if heartbeat && heartbeat.pid = pid && (!newest || heartbeat.updatedAt > newest.updatedAt)
            newest := heartbeat
    }
    common := TryReadHeartbeat(Watch.logDir "\water_house.heartbeat")
    if common {
        if Watch.family.Has(common.pid) && HandleAlive(Watch.family[common.pid]) {
            if !newest || common.updatedAt > newest.updatedAt
                newest := common
        } else {
            session := 0
            if DllCall("Kernel32\ProcessIdToSessionId", "uint", common.pid, "uint*", &session) && session != Watch.session
                collision := true
        }
    }
    return {heartbeat: newest, collision: collision}
}

TryReadHeartbeat(path) {
    try return ParseHeartbeat(FileRead(path, "UTF-8"))
    catch
        return false
}

ParseHeartbeat(text) {
    if !RegExMatch(text, '^\s*\{[\s\S]*\}\s*$')
        return false
    if !RegExMatch(text, '"pid"\s*:\s*([1-9]\d*)\s*[,}]', &pid)
        return false
    if !RegExMatch(text, '"updated_at"\s*:\s*(\d+(?:\.\d+)?)\s*[,}]', &updated)
        return false
    if !RegExMatch(text, '"worker_alive"\s*:\s*(true|false)\s*[,}]', &worker)
        return false
    if !RegExMatch(text, '"monitoring_healthy"\s*:\s*(true|false)\s*[,}]', &healthy)
        return false
    ; executable_path is deliberately not interpreted with regex/unescaping.
    ; Windows kernel process handles + WMI establish path/session ownership.
    return {pid: pid[1] + 0, updatedAt: updated[1] + 0,
        workerAlive: worker[1] = "true", monitoringHealthy: healthy[1] = "true"}
}

EvaluateHealth(now, startedAt, heartbeat, faultSince) {
    startup := now - startedAt < 90
    if !heartbeat {
        return {action: startup ? "wait" : "recover", reason: "heartbeat_missing", faultSince: 0}
    }
    if now - heartbeat.updatedAt > 45 || heartbeat.updatedAt - now > 30 {
        return {action: startup ? "wait" : "recover", reason: "heartbeat_stale", faultSince: 0}
    }
    if !heartbeat.workerAlive {
        since := faultSince ? faultSince : now
        return {action: !startup && now - since >= 45 ? "recover" : "wait",
            reason: "worker_not_running", faultSince: since}
    }
    ; A connected worker must be healthy even if tag values never change.
    ; A live reconnecting worker is never restarted solely for no network data.
    return {action: "healthy", reason: heartbeat.monitoringHealthy ? "healthy" : "network_reconnecting", faultSince: 0}
}

RestartBudgetAvailable(now, times) {
    recent := 0
    for timestamp in times {
        if now - timestamp < 600
            recent += 1
    }
    return recent < 3
}

EvaluateExitRestart(now, startedAt, exitCode, rapidFailures, recovering := false) {
    ; Human-confirmed normal exits and isolated failures after stable runtime
    ; restart at the next 5-second tick. Only a repeated rapid crash loop backs off.
    if exitCode = 0 || recovering || (startedAt && now - startedAt >= 30)
        return {delay: 0, rapidFailures: 0}
    failures := rapidFailures + 1
    return {delay: RapidFailureDelay(failures), rapidFailures: failures}
}

RapidFailureDelay(failures) {
    if failures < 3
        return 0
    if failures = 3
        return 5
    if failures = 4
        return 15
    return failures = 5 ? 30 : 60
}

NormalizePath(path) {
    pathBuf := Buffer(65536, 0)
    if !DllCall("GetFullPathNameW", "str", path, "uint", 32768, "ptr", pathBuf, "ptr", 0)
        return ""
    return StrLower(RTrim(StrGet(pathBuf, "UTF-16"), "\/"))
}

PathHash(path) {
    hash := 2166136261
    Loop Parse path
        hash := ((hash ^ Ord(A_LoopField)) * 16777619) & 0xFFFFFFFF
    return Format("{:08X}", hash)
}

UnixNow() {
    return DateDiff(A_NowUTC, "19700101000000", "Seconds")
}

CloseFamilyHandles() {
    global Watch
    for pid, handle in Watch.family
        DllCall("CloseHandle", "ptr", handle)
    Watch.family := Map()
    Watch.rootPid := 0, Watch.startedAt := 0, Watch.faultSince := 0
    Watch.lastHeartbeat := false, Watch.stoppingAt := 0, Watch.healthySince := 0
}

CleanupWatchdog(*) {
    global Watch
    if !IsObject(Watch)
        return
    CloseFamilyHandles()
    if Watch.mutex
        DllCall("CloseHandle", "ptr", Watch.mutex)
}

OpenWaterHouse(*) {
    global Watch
    for pid, handle in Watch.family {
        if HandleAlive(handle) {
            for hwnd in WinGetList("ahk_pid " pid) {
                if DllCall("IsWindowVisible", "ptr", hwnd) {
                    try WinActivate("ahk_id " hwnd)
                    return
                }
            }
        }
    }
    CheckWatchdog()
}

Log(message) {
    global Watch
    try FileAppend(FormatTime(, "yyyy-MM-dd HH:mm:ss") " " message "`r`n", Watch.logPath, "UTF-8")
}

LogState(key, message) {
    global Watch
    if Watch.state != key {
        Watch.state := key
        Log(message)
    }
}

RunSelfTests(outputPath) {
    count := 0
    try {
        good := '{"pid":123,"executable_path":"C:\\Water House\\water_house.exe","updated_at":1000.5,"worker_alive":true,"monitoring_healthy":true}'
        hb := ParseHeartbeat(good)
        Check(hb && hb.pid = 123 && hb.updatedAt = 1000.5 && hb.workerAlive, "parse native heartbeat JSON"), count += 1
        Check(!ParseHeartbeat("1000.5"), "reject legacy heartbeat"), count += 1
        Check(!ParseHeartbeat('{"pid":123}'), "reject incomplete JSON"), count += 1
        Check(!ParseHeartbeat(StrReplace(good, '"pid":123', '"pid":0')), "reject invalid PID"), count += 1
        Check(!ParseHeartbeat(StrReplace(good, '"worker_alive":true', '"worker_alive":"true"')), "reject string worker flag"), count += 1
        Check(EvaluateHealth(1000, 950, false, 0).action = "wait", "startup without heartbeat is tolerated"), count += 1
        Check(EvaluateHealth(1000, 800, false, 0).action = "recover", "missing heartbeat after grace recovers"), count += 1
        Check(EvaluateHealth(1000, 800, hb, 0).action = "healthy", "fresh unchanged values remain healthy"), count += 1
        hb.monitoringHealthy := false
        Check(EvaluateHealth(1000, 800, hb, 0).action = "healthy", "network reconnect must not restart"), count += 1
        hb.workerAlive := false
        Check(EvaluateHealth(1000, 800, hb, 0).action = "wait", "worker recovery gets grace"), count += 1
        Check(EvaluateHealth(1000, 800, hb, 950).action = "recover", "persistent dead worker recovers"), count += 1
        hb.workerAlive := true
        Check(EvaluateHealth(1000, 800, hb, 950).faultSince = 0, "worker recovery clears fault timer"), count += 1
        hb.updatedAt := 940
        Check(EvaluateHealth(1000, 800, hb, 0).action = "recover", "stale UI heartbeat recovers"), count += 1
        hb.updatedAt := 2000
        Check(EvaluateHealth(1000, 800, hb, 0).action = "recover", "future heartbeat cannot mask a hang"), count += 1
        Check(IsExpectedIdentity("C:\Water House\water_house.exe", 2, "c:\water house\WATER_HOUSE.EXE", 2), "matching path/session accepts casing"), count += 1
        Check(!IsExpectedIdentity("D:\Other\water_house.exe", 2, "C:\Water House\water_house.exe", 2), "same name other path excluded"), count += 1
        Check(!IsExpectedIdentity("C:\Water House\water_house.exe", 3, "C:\Water House\water_house.exe", 2), "cross session excluded"), count += 1
        Check(SingleFamilyRoot([{pid: 100, parent: 9}, {pid: 101, parent: 100}]) = 100, "onefile parent-child is one family"), count += 1
        Check(SingleFamilyRoot([{pid: 100, parent: 9}, {pid: 101, parent: 8}]) = 0, "independent instances disable termination"), count += 1
        Check(!RestartBudgetAvailable(1000, [500, 600, 900]), "restart storm is bounded"), count += 1
        Check(RestartBudgetAvailable(2000, [500, 600, 900]), "restart budget expires"), count += 1
        Check(EvaluateExitRestart(1000, 995, 0, 8).delay = 0, "normal exit bypasses all crash backoff"), count += 1
        Check(EvaluateExitRestart(1000, 995, 0, 8).rapidFailures = 0, "normal exit clears rapid failure count"), count += 1
        Check(EvaluateExitRestart(1000, 900, 1, 8).delay = 0, "single failure after stable runtime restarts immediately"), count += 1
        Check(EvaluateExitRestart(1000, 995, 1, 0).delay = 0, "first rapid crash restarts next tick"), count += 1
        Check(EvaluateExitRestart(1000, 995, 1, 1).delay = 0, "second rapid crash restarts next tick"), count += 1
        Check(EvaluateExitRestart(1000, 995, 1, 2).delay = 5, "third rapid crash starts bounded backoff"), count += 1
        Check(EvaluateExitRestart(1000, 995, 1, 5).delay = 60, "rapid crash backoff is capped at one minute"), count += 1
        Check(EvaluateExitRestart(1000, 995, 1, 5, true).delay = 0, "health recovery does not add launch delay"), count += 1
        ; Query only this test interpreter, using query/synchronize rights.
        ; The self-test handle has no PROCESS_TERMINATE permission.
        ownPid := DllCall("GetCurrentProcessId", "uint")
        ownHandle := DllCall("OpenProcess", "uint", 0x101000, "int", false, "uint", ownPid, "ptr")
        try {
            identity := InspectProcessHandle(ownHandle, ownPid)
            Check(identity && NormalizePath(identity.path) = NormalizePath(A_AhkPath), "native handle identity resolves this interpreter"), count += 1
            Check(HandleAlive(ownHandle), "native held handle detects this live process"), count += 1
        } finally {
            if ownHandle
                DllCall("CloseHandle", "ptr", ownHandle)
        }
        result := '{"passed":true,"test_count":' count ',"process_actions":0}'
        if outputPath != ""
            FileAppend(result "`n", outputPath, "UTF-8")
        else
            FileAppend(result "`n", "*", "UTF-8")
    } catch Error as err {
        result := '{"passed":false,"test_count":' count ',"process_actions":0}'
        if outputPath != ""
            FileAppend(result "`n", outputPath, "UTF-8")
        FileAppend("Self-test failed: " err.Message " line=" err.Line " what=" err.What "`n" err.Stack "`n", "**", "UTF-8")
        ExitApp(1)
    }
}

Check(condition, description) {
    if !condition
        throw Error(description)
}
