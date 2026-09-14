#include <assert.h>
#include <stdint.h>
#include <stdio.h>

#include "../../third_party/Butterscotch/src/crt_ui_inset.h"

int main(void) {
    assert(CrtUi_verticalOffset(240, 0, 8, 0) == 0);
    assert(CrtUi_verticalOffset(240, 0, 8, 6) == 6);
    assert(CrtUi_verticalOffset(240, 0, 32, 6) == 6);
    assert(CrtUi_verticalOffset(240, 100, 112, 6) == 0);
    assert(CrtUi_verticalOffset(240, 128, 140, 6) == 0);
    assert(CrtUi_verticalOffset(240, 232, 240, 6) == -6);
    assert(CrtUi_verticalOffset(240, 0, 240, 6) == 0);
    assert(CrtUi_verticalOffset(240, 20, 20, 6) == 0);
    assert(CrtUi_verticalOffset(0, 0, 8, 6) == 0);
    assert(CrtUi_am2rTitleBackgroundOffset(1, 164, 6) == 6);
    assert(CrtUi_am2rTitleBackgroundOffset(1, 165, 6) == -6);
    assert(CrtUi_am2rTitleBackgroundOffset(1, 166, 6) == 0);
    assert(CrtUi_am2rTitleBackgroundOffset(2, 164, 6) == 0);
    assert(CrtUi_am2rTitleBackgroundOffset(1, 164, 0) == 0);
    puts("PASS: CRT UI inset keeps full-frame art native and moves edge UI inward");
    return 0;
}
