#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>

#define AM2R_STATE_MAGIC 0x53523241u
#define AM2R_STATE_WRAPPER_SOCKET "/tmp/am2r-state-wrapper.sock"
#define AM2R_STATE_RUNNER_SOCKET "/tmp/am2r-state-runner.sock"

typedef struct {
	uint32_t magic;
	uint32_t type;
	int32_t slot;
	int32_t pid;
	int32_t frame;
	int32_t error;
} Am2rStateMessage;

int main(int argc, char **argv)
{
	if (argc != 3 || (strcmp(argv[1], "load") && strcmp(argv[1], "save"))) {
		fprintf(stderr, "usage: %s load|save slot-number\n", argv[0]);
		return 2;
	}

	char *end = NULL;
	long slot = strtol(argv[2], &end, 10);
	if (!end || *end || slot < 1 || slot > 4) {
		fprintf(stderr, "slot must be 1 through 4\n");
		return 2;
	}

	int fd = socket(AF_UNIX, SOCK_DGRAM | SOCK_CLOEXEC, 0);
	if (fd < 0) {
		perror("socket");
		return 1;
	}

	struct sockaddr_un address = {0};
	address.sun_family = AF_UNIX;
	const char *socket_path = !strcmp(argv[1], "load") ?
	                          AM2R_STATE_WRAPPER_SOCKET :
	                          AM2R_STATE_RUNNER_SOCKET;
	strncpy(address.sun_path, socket_path,
	        sizeof(address.sun_path) - 1);
	Am2rStateMessage message = {
		.magic = AM2R_STATE_MAGIC,
		.type = !strcmp(argv[1], "load") ? 2u : 1u,
		.slot = (int32_t)(slot - 1),
		.pid = (int32_t)getpid(),
	};
	ssize_t count = sendto(fd, &message, sizeof(message), MSG_NOSIGNAL,
	                       (struct sockaddr *)&address, sizeof(address));
	int saved_errno = errno;
	close(fd);
	if (count != (ssize_t)sizeof(message)) {
		errno = saved_errno;
		perror("sendto");
		return 1;
	}
	return 0;
}
