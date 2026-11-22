#include "delete_track_dialog.h"
#include "delete_track_mount.h"

#define STOCK(address, result, ...) ((result (*)(__VA_ARGS__))((address) | 1u))
#define get_key STOCK(0x030632da, uint32_t, void)
#define take_input_event STOCK(0x030515a4, int, unsigned)
#define post_ui_event STOCK(0x0306b0d8, void, unsigned)
#define update_now_playing STOCK(0x0307cb4e, int, void)
#define handle_playback_key STOCK(0x0307ce30, int, void)
#define lock_filesystem STOCK(0x0306a4a6, void, void)
#define unlock_filesystem STOCK(0x0306a4fe, void, void)
#define open_window STOCK(0x0306b302, int, unsigned, const uint16_t *)
#define show_notice STOCK(0x03059888, int, unsigned, unsigned, const uint16_t *)

enum { REDRAW_NOW_PLAYING = 0x39, NEXT_KEY = 0x40000004, MAIN_MENU = 0 };

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

#ifdef DELETE_TRACK_ENABLE_WRITES
/* The playback overlay is full. The decoder keeps this one loaded. */
#define PLAYBACK_HELPER __attribute__((section(".successor"), noinline))

static uint32_t removal_generation;
static uint8_t removal_pending, pressing_next;

static PLAYBACK_HELPER enum delete_track_result remove_pending(void)
{
    lock_filesystem();
    enum delete_track_result result = delete_track_remove(&pending, &delete_track_mount.current_generation,
                                                          removal_generation);
    unlock_filesystem();
    return result;
}

/* Leave the file the way the file manager does: close it, delete it, and
 * let the main menu rebuild the library later. */
static PLAYBACK_HELPER enum delete_track_result skip_and_remove(void)
{
    enum delete_track_result result = remove_pending();
    if (result == DELETE_TRACK_STILL_OPEN) {
        /* The stock handler acts on the second report of a held Next. */
        *(volatile uint16_t *)0x0300bfb6 = 2;
        pressing_next = 1;
        handle_playback_key();
        pressing_next = 0;
        result = remove_pending();
    }
    volatile uint8_t *library = (volatile uint8_t *)0x0300c04c;
    if (result == DELETE_TRACK_STILL_OPEN) {
        /* Next kept the file, as in a one-track queue. */
        const uint16_t parameters[16] = {0};
        result = delete_track_stop_and_remove(&pending, &delete_track_mount.current_generation,
                                              removal_generation);
        library[0x6c] = 0;
        open_window(MAIN_MENU, parameters);
    }
    if (result == DELETE_TRACK_REMOVED || result == DELETE_TRACK_IO_ERROR)
        *(volatile uint16_t *)0x0300c08e = 1;
    return result;
}
#endif

uint32_t delete_preview_get_key(void)
{
#ifdef DELETE_TRACK_ENABLE_WRITES
    if (pressing_next)
        return NEXT_KEY;
    if (removal_pending) {
        removal_pending = 0;
        delete_preview_answer = skip_and_remove();
        return 0;
    }
#endif
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
        lock_filesystem();
        delete_preview_answer = delete_track_finish(&pending, generation, delete_track_mount.opened_device,
                accepted && generation == delete_track_mount.opened_generation);
        unlock_filesystem();
#ifdef DELETE_TRACK_ENABLE_WRITES
        /* The playback key handler runs next and moves to the following track. */
        removal_generation = generation;
        removal_pending = delete_preview_answer;
#endif
    }
    return update_now_playing();
}
