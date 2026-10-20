"""Disk-backed features equivalent to v3, with full causal state across chunks."""
import gc
import json
import time
import numpy as np
import pandas as pd
from .common import ROOT, OUT, digest, save, now
from ..research_features import build_features, transition_history, runs

def chunk_features(times, stations, q, start, end, history, durations):
    context = max(0, start-336)
    frame, groups = build_features(times[context:end], stations, q[context:end])
    n = len(stations)
    # The original builder has unbounded run/transition memory. Do not reset it
    # at a disk chunk boundary while retaining the same saved-model feature names.
    for name, values in history.items():
        frame["h_"+name] = values[context:end].reshape(-1)
    for name, values in durations.items():
        frame[name] = values[context:end].reshape(-1)
    return frame.iloc[(start-context)*n:].reset_index(drop=True), groups

def prepare():
    dest = OUT / "data/features_2025.npy"
    meta = OUT / "data/features_2025.json"
    if dest.exists() and meta.exists():
        print("FEATURES_READY", flush=True)
        return
    start_clock = time.perf_counter()
    with np.load(OUT/"data/panel_2025.npz") as p:
        q=p["quantity"]; times=pd.DatetimeIndex(p["times"])
    stations=pd.read_parquet(ROOT/"data/processed/rider_stations.parquet")
    history=transition_history(times,q)
    known=np.isfinite(q)
    durations={"t_zero_run":runs(q==0,known),"t_low3_run":runs(q<=3,known),"t_positive_run":runs(q>0,known)}
    n=len(stations);columns=None;groups=None
    for start in range(0,len(times),24*14):
        end=min(len(times),start+24*14)
        frame,current_groups=chunk_features(times,stations,q,start,end,history,durations)
        if columns is None:
            columns=list(frame.columns);groups=current_groups
            matrix=np.lib.format.open_memmap(dest,mode="w+",dtype=np.float32,shape=(q.size,len(columns)))
        else:
            assert columns==list(frame.columns) and groups==current_groups
            matrix=np.lib.format.open_memmap(dest,mode="r+")
        matrix[start*n:end*n]=frame.to_numpy(dtype=np.float32,copy=False)
        matrix.flush();del matrix,frame;gc.collect()
        save(OUT/"feature_progress.json",dict(pid=__import__('os').getpid(),completed_hours=end,total_hours=len(times),updated_at_utc=now()))
        print("FEATURE_CHUNK",end,"/",len(times),"elapsed",round(time.perf_counter()-start_clock,1),flush=True)
    save(meta,dict(columns=columns,groups=groups,shape=[q.size,len(columns)],dtype="float32",timezone="Asia/Seoul",
                   input_start=str(times[0]),input_end=str(times[-1]),panel_sha256=digest(OUT/"data/panel_2025.npz"),
                   builder_sha256=digest(__file__),reference_builder_sha256=digest(ROOT/"bike_demo/research_features.py"),
                   full_history_overrides=list(history)+list(durations),test_quarters_used=False,seconds=time.perf_counter()-start_clock))
    print("FEATURES_COMPLETE",flush=True)

if __name__=="__main__":prepare()
