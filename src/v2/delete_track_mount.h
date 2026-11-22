#ifndef DELETE_TRACK_MOUNT_H
#define DELETE_TRACK_MOUNT_H

/* Above nanitecfw's payload RAM and below the stock reboot flag. */
#define DELETE_TRACK_OPEN_WRAPPER 0x0309ff80
#define DELETE_TRACK_MOUNT_STATE 0x0309ffe0

#ifndef __ASSEMBLER__
#include <stdint.h>
struct delete_track_mount_state {
    uint32_t current_generation;
    uint32_t opened_device;
    uint32_t opened_generation;
};
#define delete_track_mount (*(volatile struct delete_track_mount_state *)DELETE_TRACK_MOUNT_STATE)
#endif

#endif
