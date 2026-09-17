#include <assert.h>
#include <math.h>
#include <stdint.h>
#include <stdio.h>

#include "real_type.h"

static GMLReal referenceBankersRound(GMLReal v) {
    if (isnan(v) || isinf(v)) return v;
    GMLReal floorValue = GMLReal_floor(v);
    GMLReal fraction = v - floorValue;
    if (fraction < 0.5) return floorValue;
    if (fraction > 0.5) return floorValue + 1.0;
    int64_t integer = (int64_t)floorValue;
    return (integer & 1) == 0 ? floorValue : floorValue + 1.0;
}

static void check(GMLReal value) {
    GMLReal expected = referenceBankersRound(value);
    GMLReal actual = GMLReal_bankersRound(value);
    if (isnan(expected)) assert(isnan(actual));
    else assert(actual == expected);
}

int main(void) {
    // Dense positive/negative coverage includes every half-way parity case.
    for (int32_t eighths = -800000; eighths <= 800000; ++eighths)
        check((GMLReal)eighths / 8.0);

    static const GMLReal boundaries[] = {
        (GMLReal)INT32_MIN,
        (GMLReal)INT32_MIN - 0.5,
        (GMLReal)INT32_MAX,
        (GMLReal)INT32_MAX + 0.5,
        -2147483648.75,
        2147483648.75,
        -9007199254740990.0,
        9007199254740990.0,
        -INFINITY,
        INFINITY,
        NAN,
    };
    for (size_t i = 0; i < sizeof(boundaries) / sizeof(boundaries[0]); ++i)
        check(boundaries[i]);

    puts("real-type banker's-rounding regression: PASS");
    return 0;
}
