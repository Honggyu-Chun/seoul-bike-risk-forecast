"""Equivalent baseline runner with bounded block reads and explicit provenance."""
import json
from . import runner
from .common import OUT,save,digest
from .fast_frame import matrix_frame

class Inputs(runner.Inputs):
    def frame(self,index,columns):return matrix_frame(self.matrix,self.columns,index,columns)

def main():
    runner.Inputs=Inputs
    original=runner.run_one
    def run(data,fold,spec):
        path=OUT/fold/(spec['name']+'.json');was_complete=path.exists()
        original(data,fold,spec)
        if not was_complete:
            result=json.loads(path.read_text(encoding='utf-8'))
            result['frame_reader_sha256']=digest(__import__('bike_demo.followup_v4.fast_frame',fromlist=['x']).__file__)
            result['runner_extension_sha256']=digest(__file__);save(path,result)
    runner.run_one=run;runner.main()

if __name__=='__main__':main()
