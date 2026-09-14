set pagination off
set confirm off
break executeCollisionEvent
commands
  silent
  if self->objectIndex == 437 || other->objectIndex == 437
    printf "FIRST_BEAM_COLLISION self_id=%d self_object=%d other_id=%d other_object=%d target=%d code=%d owner=%d\n", self->instanceId, self->objectIndex, other->instanceId, other->objectIndex, targetObjectIndex, codeId, ownerObjectIndex
    detach
    quit
  end
  continue
end
continue
