#include "delete_track_dialog.h"
#include "delete_track_mount.h"
#include "delete_track_successor.h"

#define STOCK(address, result, ...) ((result (*)(__VA_ARGS__))((address) | 1u))
#define get_key STOCK(0x030632da, uint32_t, void)
#define take_input_event STOCK(0x030515a4, int, unsigned)
#define post_ui_event STOCK(0x0306b0d8, void, unsigned)
#define update_now_playing STOCK(0x0307cb4e, int, void)
#define lock_filesystem STOCK(0x0306a4a6, void, void)
#define unlock_filesystem STOCK(0x0306a4fe, void, void)
#define mount_device STOCK(0x0305add6, int, unsigned)
#define open_window STOCK(0x0306b302, int, unsigned, const uint16_t *)
#define show_notice STOCK(0x03059888, int, unsigned, unsigned, const uint16_t *)

enum { REDRAW_NOW_PLAYING = 0x39 };

static struct delete_track_target pending;

/* Read by the emulator to distinguish a dismissed dialog from an answer. */
volatile int delete_preview_answer = -1;

static int open_delete_dialog(void)
{
    uint32_t generation = delete_track_mount.current_generation;
    if (generation == delete_track_mount.opened_generation &&
        delete_track_show_dialog(&pending, generation, delete_track_mount.opened_device)) {
        delete_preview_answer = -1;
        return 1;
    }
    /* Make a recognized gesture visible even without verified file provenance. */
    if (!*(volatile uint32_t *)0x0300e5b4) {
        const uint16_t notice[5] = {0x38, 0x37, 3, 0, 1};
        return show_notice(0x0300e5b4, 0x03004654, notice);
    }
    return 0;
}

int delete_preview_input_event(unsigned event)
{
    return take_input_event(event) && !open_delete_dialog();
}

uint32_t delete_preview_get_key(void)
{
    uint32_t key = get_key();
    static uint32_t last_release;
    static uint8_t armed;
    if (key == 0x40000040) {
        uint32_t now = *(volatile uint32_t *)0x0300c02c;
        int double_press = armed && now - last_release <= 35;
        last_release = now;
        armed = !double_press;
        if (double_press && open_delete_dialog())
            return 0;
    } else if (key) {
        armed = 0;
    }
    return key;
}

int delete_preview_update(void)
{
    int accepted = delete_track_poll_answer(&pending);
    if (accepted >= 0) {
        /* The widget releases its owner without clearing the playback frame. */
        post_ui_event(REDRAW_NOW_PLAYING);
        uint32_t generation = delete_track_mount.current_generation;
#ifdef DELETE_TRACK_ENABLE_WRITES
        delete_preview_answer = delete_track_execute(&pending, &delete_track_mount.current_generation,
                delete_track_mount.opened_device, accepted && generation == delete_track_mount.opened_generation);
        volatile uint8_t *library = (volatile uint8_t *)0x0300c04c;
        if (delete_preview_answer == DELETE_TRACK_IO_ERROR)
            library[0x6c] = 0;
        if (delete_preview_answer == DELETE_TRACK_REMOVED) {
            /* The stock scanner indexes the mounted volume. */
            volatile uint16_t *refresh_state = (volatile uint16_t *)0x0300c08e;
            uint16_t parameters[16] = {1, [15] = DELETE_TRACK_REFRESH_TAG};
            unsigned volume = pending.device == 0x40000 ? 4 : 2;
            library[0x6c] = 0;
            if (mount_device(volume) != 0)
                return update_now_playing();
            library[0xe] = volume;
            lock_filesystem();
            delete_track_prepare_successor(&pending);
            unlock_filesystem();
            *refresh_state = 1;
            open_window(0x0d, parameters);
            return 1;
        }
#else
        lock_filesystem();
        delete_preview_answer = delete_track_finish(&pending, generation, delete_track_mount.opened_device,
                accepted && generation == delete_track_mount.opened_generation);
        unlock_filesystem();
#endif
    }
    return update_now_playing();
}
