#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#define STB_DS_IMPLEMENTATION
#include "stb_ds.h"
#define CHECK(x) do { if (!(x)) { fprintf(stderr,"FAIL line%d: %s\n",__LINE__,#x); abort(); } } while(0)
#define MAX_SURFACES 32
#define MAX_VIEWS 8
#define EVENT_OTHER 7
enum { RVALUE_UNDEFINED, RVALUE_REAL, RVALUE_INT32, RVALUE_INT64, RVALUE_BOOL, RVALUE_STRING };
typedef struct { int type; union { double real; int32_t int32; int64_t int64; }; } RValue;
typedef struct Instance { uint32_t instanceId; int32_t objectIndex; bool persistent; RValue vars[5]; } Instance;
typedef struct { const char* name; bool persistent; int32_t parentId; } GameObject;
typedef struct { int32_t surfaceId; } RuntimeView;
typedef struct { Instance** instances; RuntimeView views[MAX_VIEWS]; bool initialized; } SavedRoomState;
typedef struct DataWin {
    bool am2rLightSurfaceOwnershipVerified;
    struct { uint32_t count; GameObject* objects; } objt;
    struct { uint32_t count; } code, room;
} DataWin;
struct Renderer;
typedef struct { void (*surfaceFree)(struct Renderer*,int32_t); } Vtable;
typedef struct Renderer { Vtable* vtable; } Renderer;
typedef struct { Renderer base; int32_t currentSurface; } SWRenderer;
typedef struct Runner Runner;
typedef struct VMContext {
    Runner* runner; Instance* currentInstance; Instance* globalScopeInstance;
    int32_t currentEventType,currentEventSubtype,currentEventObjectIndex;
    const char* currentCodeName;
    struct { char* key; int32_t value; }* varNameMap;
} VMContext;
struct Runner {
    DataWin* dataWin; VMContext* vmContext; Renderer* renderer;
    int32_t applicationSurfaceId, guiPassTarget, surfaceStack[MAX_SURFACES];
    bool inGuiPass;
    RuntimeView views[MAX_VIEWS]; SavedRoomState* savedRoomStates;
    struct { int key; Instance* value; }* instancesById;
};
static RValue Instance_getSelfVar(Instance* i, int32_t field) {
    return field >= 0 && field < 5 ? i->vars[field] : (RValue){0};
}
static bool alive[4096], denyFree;
static int width[4096],height[4096],frees;
static bool Renderer_surfaceExists(Renderer* r,int32_t id) { (void)r; return id>0&&id<4096&&alive[id]; }
static float Renderer_getSurfaceWidth(Renderer* r,int32_t id) { return Renderer_surfaceExists(r,id)?(float)width[id]:0; }
static float Renderer_getSurfaceHeight(Renderer* r,int32_t id) { return Renderer_surfaceExists(r,id)?(float)height[id]:0; }
static void release(Renderer* r,int32_t id) { (void)r; ++frees; if(!denyFree)alive[id]=false; }
static const char* setting="1";
static const char* proofEnv(const char* name) { CHECK(!strcmp(name,"AM2R_LIGHT_ORPHAN_CLEANUP"));return setting; }
#define getenv proofEnv
#include "am2r_orphan_surfaces_impl.h"
#undef getenv
static Runner runner; static VMContext vm; static DataWin data;
static GameObject objects[737]; static SWRenderer sw; static Vtable vtable={release};
static Instance owner,other,globals; static SavedRoomState saved[2];
static unsigned cases;
static void resetFixture(void) {
    shfree(vm.varNameMap); hmfree(runner.instancesById);
    for(unsigned i=0;i<2;++i)arrfree(saved[i].instances);
    memset(&runner,0,sizeof(runner));memset(&vm,0,sizeof(vm));memset(&data,0,sizeof(data));
    memset(objects,0,sizeof(objects));memset(&sw,0,sizeof(sw));memset(&owner,0,sizeof(owner));
    memset(&other,0,sizeof(other));memset(&globals,0,sizeof(globals));memset(saved,0,sizeof(saved));
    memset(alive,0,sizeof(alive));memset(width,0,sizeof(width));memset(height,0,sizeof(height));
    setting="1";frees=0;denyFree=false;
    Am2rOrphanSurfaceReset();am2rOrphanConfigured=false;am2rOrphanEnabled=false;
    objects[727]=(GameObject){"oLightEngine",false,-100};
    data.am2rLightSurfaceOwnershipVerified=true;data.objt.count=737;data.objt.objects=objects;
    data.code.count=8667;data.room.count=2;
    runner.dataWin=&data;runner.vmContext=&vm;runner.renderer=&sw.base;runner.applicationSurfaceId=1;
    runner.guiPassTarget=1;runner.savedRoomStates=saved;sw.base.vtable=&vtable;sw.currentSurface=1;
    for(unsigned i=0;i<MAX_SURFACES;++i)runner.surfaceStack[i]=-1;
    for(unsigned i=0;i<MAX_VIEWS;++i)runner.views[i].surfaceId=-1;
    vm.runner=&runner;vm.currentInstance=&owner;vm.globalScopeInstance=&globals;
    vm.currentEventType=EVENT_OTHER;vm.currentEventSubtype=10;vm.currentEventObjectIndex=727;
    vm.currentCodeName="gml_Object_oLightEngine_Other_10";
    owner.instanceId=100001;owner.objectIndex=727;other.instanceId=100002;other.objectIndex=266;
    static char* names[5]={"surf","mysurf","gui_surface","screen_surface","s_map"};
    for(int i=0;i<5;++i)shput(vm.varNameMap,names[i],i);
    hmput(runner.instancesById,owner.instanceId,&owner);hmput(runner.instancesById,other.instanceId,&other);
    alive[7]=true;width[7]=512;height[7]=256;owner.vars[0]=(RValue){RVALUE_REAL,.real=7};
    ++cases;
}
static void create(void) { Am2rOrphanSurfaceCreated(&vm,7,512,256); }
static void removeOwner(void) { Am2rOrphanSurfaceRoomRemove(&runner,&owner); }
static void refused(void) {
    removeOwner();CHECK(frees==0&&alive[7]);
    for(unsigned i=0;i<AM2R_ORPHAN_RECORDS;++i)CHECK(am2rOrphanRecords[i].creator==NULL);
}
int main(void) {
    resetFixture();create();removeOwner();CHECK(frees==1&&!alive[7]);removeOwner();CHECK(frees==1);
    resetFixture();setting=NULL;create();removeOwner();CHECK(frees==1&&!alive[7]);
    resetFixture();setting=NULL;data.am2rLightSurfaceOwnershipVerified=false;create();refused();
    const char* bad[]={"","0","true","01","1 ","-1"};
    for(unsigned i=0;i<sizeof(bad)/sizeof(bad[0]);++i){resetFixture();setting=bad[i];create();refused();}
    resetFixture();data.am2rLightSurfaceOwnershipVerified=false;create();refused();
    resetFixture();create();data.am2rLightSurfaceOwnershipVerified=false;refused();
    resetFixture();create();data.code.count--;refused();
    resetFixture();create();data.objt.count--;refused();
    resetFixture();create();objects[727].name="lookalike";refused();
    resetFixture();create();objects[727].parentId=0;refused();
    resetFixture();create();objects[728].parentId=727;refused();
    resetFixture();create();objects[727].persistent=true;refused();
    resetFixture();create();owner.persistent=true;refused();
    resetFixture();create();owner.objectIndex=726;refused();
    resetFixture();vm.currentEventSubtype=11;create();refused();
    resetFixture();vm.currentEventObjectIndex=728;create();refused();
    resetFixture();vm.currentEventType=0;create();refused();
    resetFixture();vm.currentCodeName="Other_10";create();refused();
    resetFixture();create();runner.applicationSurfaceId=7;refused();
    resetFixture();create();sw.currentSurface=7;refused();
    resetFixture();create();runner.inGuiPass=true;runner.guiPassTarget=7;refused();
    resetFixture();create();runner.surfaceStack[31]=7;refused();
    resetFixture();create();runner.views[7].surfaceId=7;refused();
    resetFixture();create();saved[1].initialized=true;saved[1].views[7].surfaceId=7;refused();
    resetFixture();create();arrput(saved[1].instances,&owner);refused();
    resetFixture();create();other.instanceId=owner.instanceId;arrput(saved[1].instances,&other);refused();
    for(unsigned field=0;field<5;++field){
        resetFixture();create();other.vars[field]=owner.vars[0];refused();
        resetFixture();create();globals.vars[field]=owner.vars[0];refused();
        resetFixture();create();hmfree(runner.instancesById);other.vars[field]=owner.vars[0];
        arrput(saved[1].instances,&other);refused();
    }
    for(unsigned field=1;field<5;++field){resetFixture();create();owner.vars[field]=owner.vars[0];refused();}
    const double invalid[]={NAN,INFINITY,-INFINITY,0,-1,7.5,2147483648.0};
    for(unsigned i=0;i<sizeof(invalid)/sizeof(invalid[0]);++i){
        resetFixture();create();owner.vars[0]=(RValue){RVALUE_REAL,.real=invalid[i]};refused();
    }
    resetFixture();create();owner.vars[0]=(RValue){RVALUE_INT64,.int64=INT64_MAX};refused();
    resetFixture();create();owner.vars[0]=(RValue){RVALUE_BOOL,.int32=7};refused();
    resetFixture();create();owner.vars[0]=(RValue){0};refused();
    resetFixture();create();owner.vars[0]=(RValue){RVALUE_INT32,.int32=8};refused();
    resetFixture();create();width[7]=320;refused();
    resetFixture();create();height[7]=240;refused();
    resetFixture();create();alive[7]=false;removeOwner();CHECK(frees==0);
    resetFixture();create();Am2rOrphanSurfaceForgotten(7);refused();
    resetFixture();create();Am2rOrphanSurfaceReset();refused(); // logical restore/import is untracked
    resetFixture();create();vm.currentInstance=&other;Am2rOrphanSurfaceCreated(&vm,7,512,256);refused();
    resetFixture();create();Am2rOrphanSurfaceCreated(&vm,7,320,240);refused();
    resetFixture();create();other.vars[0]=owner.vars[0];refused();
    other.vars[0]=(RValue){0};removeOwner();CHECK(frees==0); // refusal retired provenance (ABA)
    resetFixture();create();denyFree=true;removeOwner();CHECK(frees==1&&alive[7]);
    removeOwner();CHECK(frees==1); // even unexpected renderer refusal cannot retry stale ownership
    resetFixture();create();alive[8]=true;width[8]=512;height[8]=256;
    Am2rOrphanSurfaceCreated(&vm,8,512,256);owner.vars[0]=(RValue){RVALUE_INT32,.int32=8};
    removeOwner();CHECK(frees==1&&alive[7]&&!alive[8]); // never guesses how to reclaim older overwritten value
    resetFixture();
    Instance many[AM2R_ORPHAN_RECORDS+1];memset(many,0,sizeof(many));
    for(unsigned i=0;i<AM2R_ORPHAN_RECORDS+1;++i){
        many[i].objectIndex=727;many[i].instanceId=110000+i;many[i].vars[0]=(RValue){RVALUE_INT32,.int32=(int32_t)i+10};
        vm.currentInstance=&many[i];alive[i+10]=true;width[i+10]=512;height[i+10]=256;
        Am2rOrphanSurfaceCreated(&vm,(int32_t)i+10,512,256);
    }
    Am2rOrphanSurfaceRoomRemove(&runner,&many[AM2R_ORPHAN_RECORDS]);CHECK(frees==0);
    for(unsigned i=0;i<AM2R_ORPHAN_RECORDS;++i)CHECK(am2rOrphanRecords[i].creator==&many[i]);
    printf("Orphan ownership helper PASS: %u scenarios, exact-data default-on, explicit opt-out, strong proof, provenance, aliases, persistence, restore/ABA and bounded refusal\n",cases);
    resetFixture();
    shfree(vm.varNameMap);hmfree(runner.instancesById);
    return 0;
}
