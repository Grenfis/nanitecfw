#ifndef DELETE_TRACK_TARGET_H
#define DELETE_TRACK_TARGET_H

#include <stdint.h>

struct delete_track_target {
    uint32_t mount_generation;
    uint32_t device;
    uint32_t directory;
    uint32_t entry;
    uint32_t first_cluster;
    uint32_t size;
    uint8_t file_system;
    uint8_t handle;
    uint8_t name[11];
    uint8_t exfat_directory[42];
};

/* Call under the playback/filesystem lock. opened_device is recorded when the
 * decoder opens the file. The generation changes whenever that mount is
 * invalidated, including when a different card appears on the same device. */
int delete_track_capture(struct delete_track_target *target, uint32_t mount_generation,
                         uint32_t opened_device);
int delete_track_matches(const struct delete_track_target *target, uint32_t mount_generation,
                         uint32_t opened_device);
/* Consume the confirmation exactly once. A successful caller may use the
 * remaining identity fields while it still owns the playback/filesystem lock. */
int delete_track_finish(struct delete_track_target *target, uint32_t mount_generation,
                        uint32_t opened_device, int accepted);

enum delete_track_result {
    DELETE_TRACK_REJECTED,
    DELETE_TRACK_STOP_FAILED,
    DELETE_TRACK_TARGET_CHANGED,
    DELETE_TRACK_IO_ERROR,
    DELETE_TRACK_REMOVED,
    DELETE_TRACK_UNSUPPORTED_FILESYSTEM,
    DELETE_TRACK_STILL_OPEN,
};

/* Call under the filesystem lock with the generation the confirmation was
 * checked against. Returns DELETE_TRACK_STILL_OPEN while any file handle,
 * not only the decoder's, refers to the target. */
enum delete_track_result delete_track_remove(struct delete_track_target *target,
        const volatile uint32_t *mount_generation, uint32_t generation);
/* Stop the decoder, then remove the target. Call without the lock. */
enum delete_track_result delete_track_stop_and_remove(struct delete_track_target *target,
        const volatile uint32_t *mount_generation, uint32_t generation);

/* Run from the playback UI task. The mount generation must be owned by the
 * caller and remain readable across the stock decoder shutdown. */
enum delete_track_result delete_track_execute(struct delete_track_target *target,
        const volatile uint32_t *mount_generation, uint32_t opened_device, int accepted);
enum delete_track_result delete_track_remove_exfat(struct delete_track_target *target);

#endif
