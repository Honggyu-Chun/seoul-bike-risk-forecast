"""Recommendation rules shared by offline evaluation and the web service."""
import numpy as np


def distances_m(lat,lon,latitudes,longitudes):
    a,b=np.radians(lat),np.radians(lon)
    c,d=np.radians(np.asarray(latitudes)),np.radians(np.asarray(longitudes))
    value=np.sin((c-a)/2)**2+np.cos(a)*np.cos(c)*np.sin((d-b)/2)**2
    return 6_371_000*2*np.arcsin(np.sqrt(np.clip(value,0,1)))


def choose_station(station_ids,quantity,distances,probabilities=None,threshold=None):
    """Closest currently positive eligible station; optional zero-risk gate."""
    eligible=np.isfinite(quantity)&(np.asarray(quantity)>0)&np.isfinite(distances)
    if probabilities is not None:
        eligible &= np.isfinite(probabilities)&(np.asarray(probabilities)<threshold)
    idx=np.flatnonzero(eligible)
    if not len(idx):return None
    order=np.lexsort((np.asarray(station_ids)[idx],np.asarray(distances)[idx]))
    return int(idx[order[0]])
