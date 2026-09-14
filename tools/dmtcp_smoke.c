#define _GNU_SOURCE

#include <signal.h>
#include <stdio.h>
#include <unistd.h>

static volatile sig_atomic_t running = 1;

static void stop(int signal)
{
    (void)signal;
    running = 0;
}

int main(void)
{
    signal(SIGINT, stop);
    signal(SIGTERM, stop);
    setvbuf(stdout, NULL, _IOLBF, 0);

    unsigned counter = 0;
    while (running) {
        printf("pid=%ld counter=%u\n", (long)getpid(), counter++);
        sleep(1);
    }
    printf("pid=%ld stopped counter=%u\n", (long)getpid(), counter);
    return 0;
}
