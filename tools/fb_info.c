#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <linux/fb.h>
#include <stdio.h>
#include <string.h>
#include <sys/ioctl.h>
#include <unistd.h>

int main(void)
{
    const int fd = open("/dev/fb0", O_RDWR | O_CLOEXEC);
    if (fd < 0) {
        fprintf(stderr, "open /dev/fb0: %s\n", strerror(errno));
        return 1;
    }

    struct fb_fix_screeninfo fix = {0};
    struct fb_var_screeninfo var = {0};
    if (ioctl(fd, FBIOGET_FSCREENINFO, &fix) < 0) {
        fprintf(stderr, "FBIOGET_FSCREENINFO: %s\n", strerror(errno));
        close(fd);
        return 1;
    }
    if (ioctl(fd, FBIOGET_VSCREENINFO, &var) < 0) {
        fprintf(stderr, "FBIOGET_VSCREENINFO: %s\n", strerror(errno));
        close(fd);
        return 1;
    }

    printf("id=%s\n", fix.id);
    printf("smem_start=0x%" PRIx64 "\n", (uint64_t)fix.smem_start);
    printf("smem_len=%u\n", fix.smem_len);
    printf("line_length=%u\n", fix.line_length);
    printf("xres=%u yres=%u xres_virtual=%u yres_virtual=%u bpp=%u\n",
           var.xres, var.yres, var.xres_virtual, var.yres_virtual,
           var.bits_per_pixel);
    close(fd);
    return 0;
}
