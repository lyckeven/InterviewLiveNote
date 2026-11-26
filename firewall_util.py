# -*- coding: utf-8 -*-
"""Windows 防火墙一键放行（手机端接入用）

只做两件事，且都只碰本程序自己创建的那条规则：

1. status()  只读查询：本程序在防火墙里到底有没有入站放行、覆盖了哪些网络类型
             （公用 / 专用 / 域），以及有没有其它规则覆盖当前程序、只放了部分网络。
2. allow()   添加一条程序级入站放行规则，profile=Any（域 / 专用 / 公用全适用），
             指向当前运行的程序本体；需要管理员权限，非管理员时通过 UAC 提权，
             弹窗由用户点「是」确认，用户点「否」则原样取消、不做任何改动。
3. remove()  只删除步骤 2 自己创建的那条同名规则，不动系统里任何其它规则。

三个动作都不会修改全局防火墙开关、不会关闭防火墙、不会碰其它程序的规则。
"""

import base64
import ctypes
import json
import os
import subprocess
import sys
import tempfile
import uuid

# 规则显示名（同时作为删除时的定位键）
RULE_DISPLAY = u"面试点评器（手机端接入）"
RULE_DESC = u"允许局域网内手机访问本机面试点评器服务；由软件内“一键放行”创建，可一键撤销"

_PS = "powershell.exe"


def program_path():
    """当前实际运行的程序本体路径：打包后是 面试点评器.exe，开发态是 python.exe"""
    p = ""
    try:
        p = sys.executable or ""
    except Exception:
        p = ""
    if not p:
        p = os.path.abspath(sys.argv[0] if sys.argv else "")
    try:
        return os.path.abspath(p)
    except Exception:
        return p


def is_admin():
    """当前进程是否已是管理员权限"""
    if os.name != "nt":
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def available():
    return os.name == "nt" and bool(os.environ.get("SystemRoot"))


def _tmp_json():
    return os.path.join(tempfile.gettempdir(), "ir_fw_%s.json" % uuid.uuid4().hex[:10])


def _b64(script):
    return base64.b64encode(script.encode("utf-16-le")).decode("ascii")


def _ps_arg(script):
    return ["-NoProfile", "-ExecutionPolicy", "Bypass", "-EncodedCommand", _b64(script)]


def _run_ps(script, timeout=25, elevate=False):
    """执行 PowerShell；脚本统一把 JSON 结果写到文件，避免控制台编码问题

    返回 (ok, message)；elevate=True 且当前非管理员时走 UAC 提权。
    """
    try:
        if elevate and not is_admin():
            outer = (
                "$ErrorActionPreference='Stop';"
                "try{"
                "$p=Start-Process -FilePath 'powershell.exe' -Verb RunAs -WindowStyle Hidden -PassThru -Wait "
                "-ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-EncodedCommand','%s');"
                "exit $p.ExitCode"
                "}catch{exit 5}" % _b64(script)
            )
            cmd = [_PS] + _ps_arg(outer)
        else:
            cmd = [_PS] + _ps_arg(script)
        cp = subprocess.run(cmd, capture_output=True, timeout=timeout,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return cp.returncode, "".join([
            (cp.stdout or b"").decode("mbcs", "replace"),
            (cp.stderr or b"").decode("mbcs", "replace"),
        ]).strip()
    except subprocess.TimeoutExpired:
        return -1, "执行超时（%ss）" % timeout
    except Exception as e:
        return -1, str(e)


def _load(path):
    for enc in ("utf-8-sig", "utf-8", "mbcs"):
        try:
            with open(path, "r", encoding=enc) as f:
                return json.load(f)
        except Exception:
            continue
    return None


def _cleanup(path):
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except Exception:
        pass


_STATUS_PS = r"""
$ErrorActionPreference='SilentlyContinue'
$out='%(out)s'
$prog='%(prog)s'
$dn='%(dn)s'
$mine=@(Get-NetFirewallRule -DisplayName $dn)
$prof=@(); $en=@(); $act=@(); $rp=@(); $myids=@()
foreach($r in $mine){
  $prof += [string]$r.Profile
  $en   += [string]$r.Enabled
  $act  += [string]$r.Action
  $myids += [string]$r.InstanceID
  $af = ($r | Get-NetFirewallApplicationFilter)
  if($af){ $rp += [string]$af.Program } else { $rp += '' }
}
$others=@()
$seen=@{}
$afs=@(Get-NetFirewallApplicationFilter -Program $prog)
foreach($f in $afs){
  $mineHit=$false
  foreach($m in $mine){ if([string]$m.InstanceID -eq [string]$f.InstanceID){ $mineHit=$true } }
  if($mineHit){ continue }
  $r = ($f | Get-NetFirewallRule)
  if($r -and ([string]$r.Direction -eq 'Inbound') -and ([string]$r.Action -eq 'Allow')){
    $k = ([string]$r.DisplayName) + '|' + ([string]$r.Profile) + '|' + ([string]$r.Enabled)
    if(-not $seen.ContainsKey($k)){
      $seen[$k]=$true
      $others += [pscustomobject]@{ name=[string]$r.DisplayName; profile=[string]$r.Profile; enabled=[string]$r.Enabled }
    }
  }
}
$profilesNow=@()
try{ $profilesNow=@((Get-NetConnectionProfile | ForEach-Object { "$($_.Name)|$([string]$_.NetworkCategory)" })) }catch{}
$o=@{
  ok=$true;
  program=$prog;
  exists=($mine.Count -gt 0);
  count=$mine.Count;
  profiles=$prof;
  enabled=$en;
  actions=$act;
  rule_programs=$rp;
  others=$others;
  category_now=$profilesNow
}
($o | ConvertTo-Json -Compress -Depth 6) | Out-File -FilePath $out -Encoding UTF8
"""


def _summarize(st):
    """把原始查询结果折算成一句话结论

    level: all 已全放行 / partial 只在部分网络类型放行 / none 未放行
    """
    if not st:
        return {"level": "unknown", "text": "防火墙状态读取失败"}
    other_allow = [o for o in (st.get("others") or []) if str(o.get("enabled")) == "True"]
    if st.get("exists"):
        profs = [p for p in (st.get("profiles") or []) if p]
        enabled = [e for e in (st.get("enabled") or []) if e]
        if any(str(e) != "True" for e in enabled) or not enabled:
            return {"level": "none", "text": u"放行规则存在但未启用，手机可能连不上"}
        if any(str(p) == "Any" for p in profs):
            return {"level": "all", "text": u"已放行（域 / 专用 / 公用网络全部适用）"}
        got = set()
        for p in profs:
            for x in str(p).split(","):
                got.add(x.strip())
        miss = [n for k, n in (("Domain", u"域"), ("Private", u"专用"), ("Public", u"公用")) if k not in got]
        return {"level": "partial",
                "text": u"已放行但只覆盖部分网络类型（缺%s），换到其它 Wi-Fi 仍可能被拦" % ("、".join(miss) or u"若干")}
    if not other_allow:
        return {"level": "none", "text": u"未放行：防火墙没有允许本程序入站，手机连不上"}
    got = set()
    for o in other_allow:
        for x in str(o.get("profile") or "").split(","):
            got.add(x.strip())
    if "Any" in got:
        return {"level": "all", "text": u"已放行（由系统里其它规则覆盖，全部网络类型）"}
    names = u"、".join(sorted({str(o.get("name") or "") for o in other_allow})) or u"其它规则"
    got_cn = [n for k, n in (("Domain", u"域"), ("Private", u"专用"), ("Public", u"公用")) if k in got]
    return {"level": "partial",
            "text": u"仅部分网络类型放行（%s）：%s；电脑切到其它 Wi-Fi 可能连不上" % (u"、".join(got_cn) or u"未知", names)}


def status():
    """只读查询防火墙放行状态（不需要管理员）"""
    if not available():
        return {"ok": False, "msg": u"仅支持 Windows", "level": "unknown",
                "text": u"仅支持 Windows"}
    out = _tmp_json()
    prog = program_path()
    script = _STATUS_PS % {"out": out.replace("'", "''"),
                           "prog": prog.replace("'", "''"),
                           "dn": RULE_DISPLAY.replace("'", "''")}
    rc, msg = _run_ps(script, timeout=25)
    st = _load(out)
    _cleanup(out)
    if not st:
        return {"ok": False, "msg": msg or u"防火墙状态读取失败", "program": prog,
                "admin": is_admin(), "level": "unknown",
                "text": u"防火墙状态读取失败（%s）" % (msg or "unknown")}
    st.update(_summarize(st))
    st["ok"] = True
    st["admin"] = is_admin()
    st["program"] = prog
    st["rule_name"] = RULE_DISPLAY
    return st


_ALLOW_PS = r"""
$ErrorActionPreference='Stop'
$out='%(out)s'
try{
  $dn='%(dn)s'
  $prog='%(prog)s'
  $desc='%(desc)s'
  Get-NetFirewallRule -DisplayName $dn -ErrorAction SilentlyContinue | Remove-NetFirewallRule -ErrorAction SilentlyContinue
  New-NetFirewallRule -DisplayName $dn -Description $desc -Direction Inbound -Action Allow -Program $prog -Profile Any -Enabled True -ErrorAction Stop | Out-Null
  $m=@(Get-NetFirewallRule -DisplayName $dn -ErrorAction SilentlyContinue)
  @{ ok=$true; count=$m.Count; profiles=@($m | ForEach-Object { [string]$_.Profile }); enabled=@($m | ForEach-Object { [string]$_.Enabled }) } |
    ConvertTo-Json -Compress -Depth 4 | Out-File -FilePath $out -Encoding UTF8
}catch{
  @{ ok=$false; msg=(''+$_.Exception.Message) } | ConvertTo-Json -Compress -Depth 4 | Out-File -FilePath $out -Encoding UTF8
}
"""


def allow():
    """一键添加“全部网络类型”的程序级入站放行规则（必要时 UAC 提权）"""
    if not available():
        return {"ok": False, "msg": u"仅支持 Windows"}
    out = _tmp_json()
    prog = program_path()
    script = _ALLOW_PS % {"out": out.replace("'", "''"),
                          "prog": prog.replace("'", "''"),
                          "dn": RULE_DISPLAY.replace("'", "''"),
                          "desc": RULE_DESC.replace("'", "''")}
    elevated = not is_admin()
    rc, msg = _run_ps(script, timeout=90, elevate=True)
    res = _load(out)
    _cleanup(out)
    if res and res.get("ok"):
        return {"ok": True, "elevated": elevated, "program": prog,
                "msg": u"已放行：%s 的入站连接已对域 / 专用 / 公用网络全部放开" % os.path.basename(prog),
                "result": res}
    reason = (res or {}).get("msg") or msg or u"未知错误"
    if rc == 5 or u"canceled" in reason.lower() or u"取消" in reason:
        reason = u"已取消（需要在弹出的系统提示里点「是」授权管理员权限）"
    return {"ok": False, "elevated": elevated, "program": prog,
            "msg": u"放行失败：%s" % reason, "result": res}


_REMOVE_PS = r"""
$ErrorActionPreference='Stop'
$out='%(out)s'
try{
  $dn='%(dn)s'
  $before=@(Get-NetFirewallRule -DisplayName $dn -ErrorAction SilentlyContinue)
  if($before.Count -gt 0){ $before | Remove-NetFirewallRule -ErrorAction SilentlyContinue }
  $after=@(Get-NetFirewallRule -DisplayName $dn -ErrorAction SilentlyContinue)
  @{ ok=$true; removed=($before.Count - $after.Count); left=$after.Count } |
    ConvertTo-Json -Compress -Depth 4 | Out-File -FilePath $out -Encoding UTF8
}catch{
  @{ ok=$false; msg=(''+$_.Exception.Message) } | ConvertTo-Json -Compress -Depth 4 | Out-File -FilePath $out -Encoding UTF8
}
"""


def remove():
    """撤销本模块创建的放行规则（只删同名规则）"""
    if not available():
        return {"ok": False, "msg": u"仅支持 Windows"}
    out = _tmp_json()
    script = _REMOVE_PS % {"out": out.replace("'", "''"), "dn": RULE_DISPLAY.replace("'", "''")}
    elevated = not is_admin()
    rc, msg = _run_ps(script, timeout=90, elevate=True)
    res = _load(out)
    _cleanup(out)
    if res and res.get("ok"):
        return {"ok": True, "elevated": elevated, "result": res,
                "msg": (u"已撤销放行规则（删除 %s 条）" % res.get("removed", 0)) if res.get("removed")
                       else u"没有找到本程序创建的放行规则，无需撤销"}
    reason = (res or {}).get("msg") or msg or u"未知错误"
    if rc == 5 or u"取消" in reason:
        reason = u"已取消（需要在弹出的系统提示里点「是」授权管理员权限）"
    return {"ok": False, "elevated": elevated, "msg": u"撤销失败：%s" % reason}


if __name__ == "__main__":
    import pprint
    action = (sys.argv[1] if len(sys.argv) > 1 else "status").lower()
    fn = {"status": status, "allow": allow, "remove": remove}.get(action)
    if not fn:
        print("usage: python firewall_util.py [status|allow|remove]")
        sys.exit(2)
    pprint.pprint(fn())
