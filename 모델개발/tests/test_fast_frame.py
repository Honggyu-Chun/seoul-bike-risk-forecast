import numpy as np
import pandas as pd
from bike_demo.followup_v4.fast_frame import matrix_frame

def test_block_reader_preserves_order_duplicates_nan_and_column_subset(tmp_path):
    path=tmp_path/'features.npy'
    matrix=np.lib.format.open_memmap(path,mode='w+',dtype=np.float32,shape=(37,5))
    matrix[:]=np.arange(185,dtype=np.float32).reshape(37,5);matrix[13,3]=np.nan;matrix.flush()
    names=['a','b','c','d','e'];indices=np.array([36,13,0,13,4,22,9])
    actual=matrix_frame(matrix,names,indices,['d','b'],block_rows=3)
    expected=pd.DataFrame(np.column_stack([matrix[indices,3],matrix[indices,1]]),columns=['d','b'])
    pd.testing.assert_frame_equal(actual,expected)
    empty=matrix_frame(matrix,names,np.array([],dtype=int),['b'],block_rows=3)
    assert empty.shape==(0,1)
