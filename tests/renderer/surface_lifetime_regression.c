/* Actual diagnostic helper, real runtime types, no renderer/GPU execution. */
#undef NDEBUG
#include <assert.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "vm.h"
#include "runner.h"
#include "renderer.h"
#include "instance.h"
#include "vm_surface_trace.h"

static const char* setting;
static unsigned envReads, logs, metadataReads;
static char lastLog[1024];
static bool existing[2048];
static char* traceGetenv(const char* key) {
    assert(!strcmp(key, "AM2R_SURFACE_TRACE_LIMIT"));
    ++envReads;
    return (char*)setting;
}
static void traceLog(const char* format, ...) {
    va_list args;
    va_start(args, format);
    int n = vsnprintf(lastLog, sizeof(lastLog), format, args);
    va_end(args);
    assert(n > 0 && (size_t)n < sizeof(lastLog));
    ++logs;
}
static bool exists(Renderer* renderer, int32_t id) {
    assert(renderer);
    ++metadataReads;
    return id >= 0 && id < 2048 && existing[id];
}
static float width(Renderer* renderer, int32_t id) {
    assert(renderer && id >= 0 && id < 2048 && existing[id]);
    ++metadataReads;
    return 512;
}
static float height(Renderer* renderer, int32_t id) {
    assert(renderer && id >= 0 && id < 2048 && existing[id]);
    ++metadataReads;
    return 256;
}
#define getenv traceGetenv
#define logInfo traceLog
#include "surface_lifetime_production.inc"
#undef getenv
#undef logInfo

int main(int argc, char** argv) {
    assert(argc == 3);
    setting = !strcmp(argv[1], "unset") ? NULL : argv[1];
    RendererVtable vtable = {.surfaceExists=exists, .getSurfaceWidth=width,
                             .getSurfaceHeight=height};
    Renderer renderer = {.vtable=&vtable};
    Runner runner = {.renderer=&renderer, .currentRoomIndex=158, .frameCount=200};
    Instance instance = {.instanceId=100999, .objectIndex=727};
    VMContext ctx = {.runner=&runner, .currentInstance=&instance,
                     .currentCodeName="gml_Object_Example_Other_10",
                     .currentEventType=7, .currentEventSubtype=10,
                     .currentEventObjectIndex=727};
    existing[1] = true;
    const unsigned configured = vmSurfaceTraceParseLimit(setting);
    if (!strcmp(argv[2], "disabled")) {
        assert(!configured);
        for (unsigned i=0; i<8; ++i) {
            VM_surfaceTrace(&ctx,"create",1,512,256);
            VM_surfaceTraceRoomRemove(&ctx,&instance);
        }
        assert(!logs && !metadataReads && envReads==1);
    } else if (!strcmp(argv[2], "lifetime")) {
        VM_surfaceTrace(&ctx,"create",1,512,256);
        assert(strstr(lastLog,"surface=1 requested=512,256 actual=512,256 exists=1 tracked=1"));
        assert(strstr(lastLog,"creator=100999 creator_object=727 created_room=158 created_frame=200"));
        Renderer oldRenderer=renderer;
        Runner oldRunner=runner;
        Instance oldInstance=instance;
        VMContext oldCtx=ctx;
        VM_surfaceTraceRoomRemove(&ctx,&instance);
        assert(strstr(lastLog,"op=creator_removed") && strstr(lastLog,"exists=1"));
        assert(strstr(lastLog,"code=runner_room_remove event=-1,-1 event_object=-1"));
        assert(existing[1] && !memcmp(&oldRenderer,&renderer,sizeof(renderer)) &&
               !memcmp(&oldRunner,&runner,sizeof(runner)) &&
               !memcmp(&oldInstance,&instance,sizeof(instance)) && !memcmp(&oldCtx,&ctx,sizeof(ctx)));
        unsigned n=logs;
        VM_surfaceTraceRoomRemove(&ctx,&instance);
        assert(logs==n); /* one removal observation even if placed ID reused */
        existing[2]=true;
        runner.currentRoomIndex=160;
        runner.frameCount=400;
        VM_surfaceTrace(&ctx,"create",2,512,256);
        VM_surfaceTraceRoomRemove(&ctx,&instance);
        assert(logs==n+2 && strstr(lastLog,"surface=2") && strstr(lastLog,"created_room=160"));
        VM_surfaceTrace(&ctx,"free",2,0,0); /* ignored free remains live */
        assert(strstr(lastLog,"exists=1 tracked=1"));
        existing[2]=false;
        VM_surfaceTrace(&ctx,"free",2,0,0);
        assert(strstr(lastLog,"exists=0 tracked=1"));
        existing[2]=true;
        instance.instanceId=101000;
        VM_surfaceTrace(&ctx,"create",2,512,256);
        assert(strstr(lastLog,"creator=101000") && strstr(lastLog,"creator_removed=0"));
        VM_surfaceTrace(&ctx,"resize",2,320,240);
        assert(strstr(lastLog,"requested=320,240 actual=512,256"));
    } else if (!strcmp(argv[2], "restore")) {
        VM_surfaceTrace(&ctx,"create",1,512,256);
        unsigned count=logs;
        unsigned reads=metadataReads;
        VM_surfaceTraceResetRecords();
        assert(logs==count && metadataReads==reads && vmSurfaceTraceLimit==1024);
        for(unsigned i=0;i<VM_SURFACE_TRACE_RECORDS;++i) assert(!vmSurfaceTraceRecords[i].used);
        VM_surfaceTraceRoomRemove(&ctx,&instance); /* same restored IDs, no old attribution */
        assert(logs==count);
        VM_surfaceTrace(&ctx,"free",1,0,0);
        assert(strstr(lastLog,"tracked=0") && strstr(lastLog,"creator=0"));
        VM_surfaceTrace(&ctx,"create",1,512,256);
        assert(strstr(lastLog,"tracked=1") && strstr(lastLog,"sample=3/1024"));
        VM_surfaceTraceRoomRemove(&ctx,&instance);
        assert(logs==4 && strstr(lastLog,"creator_removed=1"));
    } else if (!strcmp(argv[2], "capacity")) {
        for(int i=1;i<=129;++i) {
            existing[i]=true;
            VM_surfaceTrace(&ctx,"create",i,512,256);
        }
        assert(strstr(lastLog,"tracked=0"));
        existing[64]=false;
        VM_surfaceTrace(&ctx,"free",64,0,0);
        VM_surfaceTrace(&ctx,"create",129,512,256);
        assert(strstr(lastLog,"tracked=1"));
        unsigned n=logs;
        VM_surfaceTraceRoomRemove(&ctx,&instance);
        assert(logs==n+128);
        VM_surfaceTraceRoomRemove(&ctx,&instance);
        assert(logs==n+128);
    } else if (!strcmp(argv[2], "bounded")) {
        assert(configured==1024);
        for(unsigned i=0;i<1024;++i) VM_surfaceTrace(&ctx,"create",1,512,256);
        assert(logs==1024 && strstr(lastLog,"sample=1024/1024"));
        unsigned reads=metadataReads;
        for(unsigned i=0;i<100;++i) {
            VM_surfaceTrace(&ctx,"create",1,512,256);
            VM_surfaceTraceRoomRemove(&ctx,&instance);
        }
        assert(logs==1024 && metadataReads==reads && envReads==1);
    } else if (!strcmp(argv[2], "sanitize")) {
        VM_surfaceTrace(NULL,"create",1,512,256);
        assert(!logs);
        ctx.currentInstance=NULL;
        ctx.currentCodeName="name\nsecret/path=%s";
        VM_surfaceTrace(&ctx,"create",1,512,256);
        assert(strstr(lastLog,"code=name?secret?path??s ") && strstr(lastLog,"instance=0 object=-1"));
        assert(!strstr(lastLog,"\nsecret") && !strstr(lastLog,"%s"));
        char longCode[512]; memset(longCode,'a',511);longCode[511]=0;
        ctx.currentCodeName=longCode;
        VM_surfaceTrace(&ctx,"create",1,512,256);
        const char* code=strstr(lastLog,"code=")+5;
        assert(strchr(code,' ')-code==95);
    } else assert(!"unknown test scenario");
    printf("QA_SURFACE_TRACE scenario=%s logs=%u metadata_reads=%u environment_reads=%u PASS\n",
           argv[2],logs,metadataReads,envReads);
    return 0;
}
