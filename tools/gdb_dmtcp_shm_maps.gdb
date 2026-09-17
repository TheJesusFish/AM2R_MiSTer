set pagination off
set confirm off
break ipc/file/fileconnlist.cpp:574
condition 1 i == 4
set restartPauseLevel=0
continue
stepi 10
info registers r0 r1 r2 r3 r7 r9 r10 sp pc
printf "DMTCP_SHM i=%u addr=%p size=%u prot=0x%x flags=0x%x fd=%d offset=%u name=%s\n", $r9, $r0, $r1, $r2, $r3, $r10, *(unsigned int*)($sp+4), $r7+88
kill
quit
