"""Run with python -m team_model; defaults to a separate localhost port."""
import argparse
import json
import os
from pathlib import Path
from .runtime import install_bundle, Runtime

def main():
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest='command',required=True)
    sub.add_parser('install');sub.add_parser('check')
    demo=sub.add_parser('demo', help='Run real final models on synthetic inputs over local HTTP')
    demo.add_argument('--write-snapshot',type=Path,help='Write a new current-hour example JSON and exit')
    serve=sub.add_parser('serve');serve.add_argument('--host',default='127.0.0.1');serve.add_argument('--port',type=int,default=18101)
    serve.add_argument('--weather-directory',type=Path,help='Trusted directory of hash-checked hourly model receipts')
    export=sub.add_parser('export-openapi');export.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    if args.command=='demo':
        if args.write_snapshot:
            from .demo import snapshot
            args.write_snapshot.write_text(json.dumps(snapshot(),ensure_ascii=False),encoding='utf-8')
        else:
            from .smoke_http import main as smoke
            smoke(final=True)
        return
    if args.command=='install':
        print(json.dumps({'installed':str(install_bundle()),'new_fit':0}));return
    if args.command=='check':
        rt=Runtime()
        for h in (1,2,3):
            risk,threshold=rt.predict_c([[102,2,1.0986123,14,1,11,0,h]],h)
            assert len(risk)==1 and 0<=risk[0]<=1
        print(json.dumps({'real_model_loaded':True,'real_routes':['C_h1','C_h2','C_h3'],'new_fit':0,'provider_calls':0,'operating_model_replaced':False}));return
    from .service import create_app
    if args.command=='export-openapi':
        app=create_app('schema-export-only-not-an-access-token',runtime=Runtime())
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(app.openapi(),ensure_ascii=False,indent=2),encoding='utf-8',newline='\n');return
    token=os.environ.get('CYCLEON_SERVICE_TOKEN','')
    app=create_app(token,weather_directory=args.weather_directory)
    import uvicorn
    uvicorn.run(app,host=args.host,port=args.port,workers=1)
if __name__=='__main__':main()
