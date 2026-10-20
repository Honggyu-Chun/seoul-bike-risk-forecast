"""Finite, explicit live collection. No daemon or automatic background polling."""
import argparse
import sys
import time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from bike_demo.live import fetch_live

parser=argparse.ArgumentParser()
parser.add_argument('--iterations',type=int,default=1)
parser.add_argument('--interval-seconds',type=int,default=60)
args=parser.parse_args()
if not 1<=args.iterations<=10000 or args.interval_seconds<30:
    parser.error('iterations must be 1..10000 and interval at least 30 seconds; verify your own API allowance first')
for index in range(args.iterations):
    packet=fetch_live()
    print(f"{packet['received_kst']} rows={packet['count']} scope={packet['scope']}",flush=True)
    if index+1<args.iterations:time.sleep(args.interval_seconds)
