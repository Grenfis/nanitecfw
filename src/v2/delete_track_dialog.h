#ifndef DELETE_TRACK_DIALOG_H
#define DELETE_TRACK_DIALOG_H

#include "delete_track_target.h"

int delete_track_show_dialog(struct delete_track_target *target, uint32_t mount_generation,
                             uint32_t opened_device);

/* With a pending target, return -1 while the widget is active, 0 on dismissal,
 * or 1 on Yes. The caller must then consume the target. */
int delete_track_poll_answer(const struct delete_track_target *target);

/* Call from the Now Playing task after the stock widget processes keys.
 * Returns -1 while waiting or when there is no pending confirmation. */
int delete_track_poll_dialog(struct delete_track_target *target,
        const volatile uint32_t *mount_generation, uint32_t opened_device);

#endif
