"""Versioned hourly weather contract for conditional archives and live grids.

Use the preceding fully completed source hour, with a whole hour of release
headroom. Keep the portal label, API requested vintage and actual receipt distinct.
No inventory rows or model horizons are discarded by this contract.
"""
from __future__ import annotations

from datetime import datetime, timedelta
import numpy as np

from .common import KST, stamp
from .history_weather import parse_grid

CONTRACT = 'kma_completed_hour_v2'
CURRENT_TEN_MINUTE_START = datetime(2024,3,4,10,tzinfo=KST)
VARIABLES = ('T1H','RN1','REH','WSD','PTY')


def hourly_job(base, variable, horizon=None):
    base=base.astimezone(KST)
    if base.minute or base.second or base.microsecond or variable not in VARIABLES or horizon not in (None,1,2,3):
        raise ValueError('Exact base hour, supported variable and horizon required')
    cycle=base-timedelta(hours=2)
    if horizon is None:
        label=cycle
        vintage=cycle+timedelta(minutes=50) if cycle>=CURRENT_TEN_MINUTE_START else cycle
        endpoint='nph-dfs_odam_grd'
        valid=vintage
        parameters={'tmfc':vintage.strftime('%Y%m%d%H%M'),'vars':variable}
        lead=0
    else:
        label=cycle+timedelta(minutes=30)
        vintage=cycle+timedelta(minutes=50)
        endpoint='nph-dfs_vsrt_grd'
        valid=base+timedelta(hours=horizon)
        parameters={'tmfc':vintage.strftime('%Y%m%d%H%M'),'tmef':valid.strftime('%Y%m%d%H'),'vars':variable}
        lead=horizon+2
    return {'endpoint':endpoint,'parameters':parameters,'variable':variable,'horizon':horizon,
        'base_at':stamp(base),'requested_issue_at':stamp(vintage),'requested_valid_at':stamp(valid),
        'portal_label_at':stamp(label),'portal_lead':lead,'contract':CONTRACT,
        'source_vintage_age_hours':(base-vintage).total_seconds()/3600,
        'historical_received_at':None}


def canonical_grid(raw, variable, *, current):
    """Match the archived product's declared display precision, retaining raw bytes.

    Current REH is integer percent; the other continuous current fields have one
    decimal. Decode numeric ASCII in float64 before rounding, since conversion to
    float32 would move decimal half ties. Forecast fields are already canonical.
    These rules are frozen before the separate holdout source audit.
    """
    validated=parse_grid(raw,variable)
    if not current:
        return validated
    values=np.fromstring(raw.decode('ascii').replace(',',' '),sep=' ')
    if variable=='REH':
        values=np.floor(values+0.5)
    elif variable!='PTY':
        values=np.round(values,1)
    values[~np.isfinite(validated)]=np.nan
    return values.astype(np.float32)
