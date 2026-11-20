#include "delete_track_dialog.h"

#define STOCK(address, result, ...) ((result (*)(__VA_ARGS__))((address) | 1u))
#define lock_filesystem STOCK(0x0306a4a6, void, void)
#define unlock_filesystem STOCK(0x0306a4fe, void, void)
#define show_widget STOCK(0x03059888, int, uint32_t, uint32_t, const uint8_t *)
#define event_test STOCK(0x0306b138, int, uint32_t)
#define event_take STOCK(0x0306b0f4, int, uint32_t)

enum {
    PLAYBACK_WIDGET = 0x0300e5b4,
    CONFIRMATION_WIDGET = 0x03004600,
    ACCEPTED_EVENT = 0xdf,
    CANCELLED_EVENT = 0xe0,
};

static const volatile uint32_t *const playback_child = (const volatile uint32_t *)PLAYBACK_WIDGET;

int delete_track_show_dialog(struct delete_track_target *target, uint32_t mount_generation,
                             uint32_t opened_device)
{
    /* The stock widget's five-byte descriptor selects No and string 0x49. */
    static const uint8_t prompt[] = {0, 0x38, 0, 0x49, 0};
    if (*playback_child || target->mount_generation ||
        event_test(ACCEPTED_EVENT) || event_test(CANCELLED_EVENT))
        return 0;

    lock_filesystem();
    int captured = delete_track_capture(target, mount_generation, opened_device);
    unlock_filesystem();
    if (!captured)
        return 0;

    if (show_widget(PLAYBACK_WIDGET, CONFIRMATION_WIDGET, prompt))
        return 1;
    target->mount_generation = 0;
    return 0;
}

int delete_track_poll_answer(const struct delete_track_target *target)
{
    if (!target->mount_generation || *playback_child == CONFIRMATION_WIDGET)
        return -1;
    if (*playback_child)
        return 0;

    /* Absence of an explicit answer also covers a window or media reset. */
    int cancelled = event_take(CANCELLED_EVENT);
    int accepted = event_take(ACCEPTED_EVENT);
    return accepted && !cancelled;
}

int delete_track_poll_dialog(struct delete_track_target *target,
        const volatile uint32_t *mount_generation, uint32_t opened_device)
{
    int accepted = delete_track_poll_answer(target);
    if (accepted < 0)
        return -1;
    return delete_track_execute(target, mount_generation, opened_device, accepted);
}
