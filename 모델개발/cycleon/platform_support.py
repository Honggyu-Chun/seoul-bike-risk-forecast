"""Small explicit platform layer for the same sequential training queue."""
import os
import subprocess

NO_WINDOW=getattr(subprocess,'CREATE_NO_WINDOW',0)

if os.name=='nt':
    import msvcrt
else:
    import fcntl
    class FileLockCompatibility:
        LK_NBLCK=1
        LK_UNLCK=0
        @staticmethod
        def locking(fd,mode,count):
            if mode==FileLockCompatibility.LK_NBLCK:
                fcntl.lockf(fd,fcntl.LOCK_EX|fcntl.LOCK_NB,count)
            elif mode==FileLockCompatibility.LK_UNLCK:
                fcntl.lockf(fd,fcntl.LOCK_UN,count)
            else:raise ValueError('Only explicit nonblocking lock/unlock supported')
    msvcrt=FileLockCompatibility
