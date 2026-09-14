#include <errno.h>
#include <fcntl.h>
#include <linux/fb.h>
#include <stdio.h>
#include <string.h>
#include <sys/ioctl.h>
#include <unistd.h>

int main(void) {
    int fd = open("/dev/fb0", O_RDWR | O_CLOEXEC);
    if (fd < 0) {
        fprintf(stderr, "open: %s\n", strerror(errno));
        return 1;
    }

    struct fb_fix_screeninfo fix = {0};
    struct fb_var_screeninfo var = {0};
    if (ioctl(fd, FBIOGET_FSCREENINFO, &fix) != 0 ||
        ioctl(fd, FBIOGET_VSCREENINFO, &var) != 0) {
        fprintf(stderr, "ioctl: %s\n", strerror(errno));
        close(fd);
        return 2;
    }

    printf("id=%s smem_start=0x%llx smem_len=%u line_length=%u "
           "xres=%u yres=%u xvirt=%u yvirt=%u bpp=%u\n",
           fix.id, (unsigned long long)fix.smem_start, fix.smem_len,
           fix.line_length, var.xres, var.yres, var.xres_virtual,
           var.yres_virtual, var.bits_per_pixel);
    close(fd);
    return 0;
}
