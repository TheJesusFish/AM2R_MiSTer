set pagination off
set confirm off
break builtin_room_goto
commands
silent
source /tmp/gdb_inspect_room_transition.py
detach
quit
end
continue
