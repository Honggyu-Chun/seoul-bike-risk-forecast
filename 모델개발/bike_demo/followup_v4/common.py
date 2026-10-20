from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import os

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "artifacts" / "followup_v4_20261020"
RECORD = ROOT / "experiments" / "20261020_후속실험_v4"

def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(8 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()

def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + f".{os.getpid()}.tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    temp.replace(path)

def now():
    return datetime.now(timezone.utc).isoformat()

def process_memory():
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        class Counters(ctypes.Structure):
            _fields_=[('cb',wintypes.DWORD),('PageFaultCount',wintypes.DWORD)]+[(name,ctypes.c_size_t) for name in ('PeakWorkingSetSize','WorkingSetSize','QuotaPeakPagedPoolUsage','QuotaPagedPoolUsage','QuotaPeakNonPagedPoolUsage','QuotaNonPagedPoolUsage','PagefileUsage','PeakPagefileUsage')]
        state=Counters();state.cb=ctypes.sizeof(state)
        ok=ctypes.windll.psapi.GetProcessMemoryInfo(wintypes.HANDLE(-1),ctypes.byref(state),state.cb)
        if not ok:raise OSError('GetProcessMemoryInfo failed')
        return dict(rss_gib=state.WorkingSetSize/2**30,peak_rss_gib=state.PeakWorkingSetSize/2**30)
    import resource
    return dict(peak_rss_gib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/2**20)

def protocol():
    return json.loads((RECORD / "protocol.json").read_text(encoding="utf-8"))
