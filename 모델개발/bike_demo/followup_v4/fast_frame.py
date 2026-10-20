"""Bound memory copies while reading the same frozen feature matrix."""
import numpy as np
import pandas as pd

def matrix_frame(matrix,all_columns,index,columns,block_rows=50000):
    positions=np.array([all_columns.index(c) for c in columns],dtype=np.intp)
    index=np.asarray(index,dtype=np.intp)
    values=np.empty((len(index),len(columns)),dtype=np.float32)
    # Read complete contiguous row blocks once instead of revisiting a large
    # disk-backed row layout separately for every individual feature column.
    for start in range(0,len(index),block_rows):
        end=min(len(index),start+block_rows)
        rows=matrix[index[start:end]]
        values[start:end]=rows[:,positions]
    return pd.DataFrame(values,columns=columns,copy=False)
