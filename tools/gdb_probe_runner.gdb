set pagination off
set confirm off
printf "G_RUNNER_GLOBAL address=%p value=%p\n", &g_runner, g_runner
printf "RUNNER room=%d frame=%d next=%d data=%p vm=%p\n", g_runner->currentRoomIndex, g_runner->frameCount, g_runner->nextInstanceId, g_runner->dataWin, g_runner->vmContext
detach
quit
