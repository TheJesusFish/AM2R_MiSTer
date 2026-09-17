set pagination off
set confirm off
printf "BEFORE room=%d pending=%d frame=%d\n", g_runner->currentRoomIndex, g_runner->pendingRoom, g_runner->frameCount
set g_runner->pendingRoom=122
detach
quit
