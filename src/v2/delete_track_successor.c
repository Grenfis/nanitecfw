#include <stdint.h>
#include "delete_track_mount.h"
#include "delete_track_successor.h"
#include "delete_track_target.h"

/* V2.0.0. Run with the decoder stopped, the old queue still active and the
 * filesystem lock held. Refresh can change both the queue and the mount. */
#define STOCK(address, result, ...) ((result (*)(__VA_ARGS__))((address) | 1u))
#define choose_next STOCK(0x03060ca4, int, uint32_t *, int)
#define open_file STOCK(0x0305be72, unsigned, uint8_t *, uint32_t, uint32_t, unsigned, const char *)
#define close_file STOCK(0x0305c340, int, unsigned)
#define read_library STOCK(0x0306b404, int, unsigned, uint32_t, unsigned, void *)
#define mount_device STOCK(0x0305add6, int, unsigned)
#define lock_filesystem STOCK(0x0306a4a6, void, void)
#define unlock_filesystem STOCK(0x0306a4fe, void, void)
#define open_window STOCK(0x0306b302, int, unsigned, const uint16_t *)
#define get_key STOCK(0x030632da, uint32_t, void)
#define watchdog STOCK(0x03065898, void, void)
#define load_favorites STOCK(0x0306c24a, void, void)
#define mounted_device (*(volatile uint32_t *)0x03077d08)
#define library_device (*(volatile uint8_t *)0x0300c05b)
#define play_mode (*(volatile uint8_t *)0x0300c0b9)
#define play_order (*(volatile uint8_t *)0x0300c0bb)
#define SUCCESSOR_CODE __attribute__((section(".successor")))

struct successor {
    uint32_t device;
    uint32_t directory;
    uint32_t entry;
    uint32_t cluster;
    uint32_t size;
    uint8_t name[11];
    uint16_t kind;
    uint16_t row;
    uint16_t depth;
    uint16_t scope;
};

struct directory_entry {
    uint32_t directory;
    uint32_t position;
};

struct library_group {
    uint16_t representative;
    uint16_t first_child;
    uint16_t children;
    uint16_t tracks;
};

struct successor_request {
    uint32_t cookie;
    struct successor song;
    uint32_t generation;
    unsigned row;
    unsigned playlist_count;
    uint16_t track_base;
    uint16_t track_count;
    uint16_t list_base;
    uint8_t mode;
    uint8_t order;
};

/* Module 34 reloads its code without overwriting this request. Only the
 * tagged stock window transitions can consume it. */
#define request (*(volatile struct successor_request *)0x0309ef80)
#define REFRESH_PENDING 0xa6e7105cu
#define PLAY_PENDING 0x5ca6e710u
#define LAUNCH_PENDING 0x1071c6a5u
#define ALL_SONGS_OFFSET 0x52000
#define MAX_SORTED_ROWS 0x2000
#define MAX_RECORDS 0x8000
#define FAVORITES 2
#define M3U 0x0e
#define MAX_PLAYLIST_ROWS 500
#define active_favorite_count (*(volatile uint16_t *)0x0300c084)
#define active_playlist_count (*(volatile uint16_t *)0x0300d140)
#define favorites ((const volatile struct directory_entry *)0x03075500)
#define playlist ((volatile struct directory_entry *)0x0300d144)
#define saved_playlist ((volatile struct directory_entry *)0x0309dfa0)
/* The last halfword of the stock window dispatcher’s copied descriptor. */
#define window_tag (*(volatile uint16_t *)0x03004b2e)
_Static_assert(0x0309dfa0 + MAX_PLAYLIST_ROWS * sizeof(struct directory_entry) <= 0x0309ef80,
               "Playlist snapshot overlaps the resume request");
_Static_assert(sizeof(struct successor_request) <= 0x80, "Resume request overlaps its code");

static const uint32_t library_maps[6] __attribute__((section(".successor_data"))) = {
    [1] = ALL_SONGS_OFFSET,
    [3] = 0x52020,
    [4] = 0x52140,
    [5] = 0x521e0,
};

static SUCCESSOR_CODE unsigned map_offset(unsigned kind)
{
    return kind < sizeof(library_maps) / sizeof(library_maps[0]) ? library_maps[kind] : 0;
}

static SUCCESSOR_CODE unsigned group_depth(unsigned kind)
{
    if (kind == 3)
        return 2;
    if (kind == 4)
        return 1;
    if (kind == 5)
        return 3;
    return 0;
}

static SUCCESSOR_CODE int read_group(uint32_t sector, unsigned index, struct library_group *group)
{
    return read_library(0x30001, (sector << 9) + index * sizeof(*group), sizeof(*group), group) == 0;
}

static SUCCESSOR_CODE int read_track_record(uint32_t base, uint32_t map, unsigned row,
                                             uint32_t record[5], uint32_t *address)
{
    uint16_t id;
    if (read_library(0x30001, (map << 9) + row * 2, sizeof(id), &id) || id >= MAX_RECORDS)
        return 0;
    *address = (base << 9) + ((uint32_t)id << 11);
    return !read_library(0x30001, *address, sizeof(uint32_t) * 5, record);
}

static SUCCESSOR_CODE uint32_t file_field(const volatile uint8_t *p)
{
    return p[0] | (uint32_t)p[1] << 8 | (uint32_t)p[2] << 16 | (uint32_t)p[3] << 24;
}

static SUCCESSOR_CODE int probe(struct successor *song, uint32_t directory, uint32_t entry)
{
    if (directory >= 0x0ffffff7 || entry == UINT32_MAX)
        return 0;
    uint8_t name[11];
    char mode[2];
    mode[0] = 'R';
    mode[1] = 0;
    unsigned handle = open_file(name, directory, entry, 3, mode);
    if (handle >= 8)
        return 0;

    const volatile uint8_t *file = (const volatile uint8_t *)0x03078844 + handle * 0x27e;
    song->device = mounted_device;
    song->directory = file_field(file + 0x0c);
    song->entry = file_field(file + 0x1c);
    song->cluster = file_field(file + 0x14);
    song->size = file_field(file + 0x10);
    for (unsigned i = 0; i < sizeof(song->name); i++)
        song->name[i] = name[i];
    close_file(handle);
    return file[0] == 0 && song->directory == directory && song->entry == entry &&
           song->size && song->cluster >= 2 && song->cluster < 0x0ffffff7;
}

SUCCESSOR_CODE int delete_track_choose_successor(struct successor *song, uint32_t device,
                                                 uint32_t deleted_directory, uint32_t deleted_entry)
{
    const volatile uint32_t *const playback = (const volatile uint32_t *)0x0300c744;
    uint32_t queue[0x120 / 4];
    song->device = 0;
    if ((device != 0x30002 && device != 0x40000) || mounted_device != device ||
        library_device != (device == 0x40000 ? 4 : 2) ||
        *(volatile uint32_t *)0x030045ac != UINT32_MAX)
        return 0;
    for (unsigned i = 0; i < sizeof(queue) / sizeof(queue[0]); i++)
        queue[i] = playback[i];

    unsigned count = (uint16_t)queue[0];
    unsigned current = queue[0] >> 16;
    unsigned kind = queue[0xf0 / 4] >> 16;
    unsigned depth = (uint16_t)queue[0xf4 / 4];
    unsigned scope = depth;
    unsigned offset = map_offset(kind);
    uint32_t base = queue[0xf8 / 4];
    uint32_t map = queue[0xfc / 4];
    if (count < 2 || count > MAX_SORTED_ROWS || !current || current > count ||
        (!offset && kind != M3U && kind != FAVORITES) ||
        (depth != group_depth(kind) && !(kind == 3 && depth == 1) && !(kind == 5 && depth == 2)) ||
        queue[0x34 / 4] != deleted_directory || queue[0x38 / 4] != deleted_entry + 1)
        return 0;
    unsigned first = (uint16_t)(queue[0x110 / 4 + depth / 2] >> (16 * (depth & 1)));
    if (!depth && first)
        return 0;
    if (offset ? first + count > MAX_SORTED_ROWS || base > UINT16_MAX * 6u || map != base + offset :
                 count > MAX_PLAYLIST_ROWS || count != (kind == M3U ? active_playlist_count :
                                                                      active_favorite_count))
        return 0;
    /* The stock launcher flattens both genre and artist Play All to depth
     * two. For a genre with multiple artists, compare the original span. */
    if (kind == 5 && depth == 2) {
        struct library_group genre;
        unsigned index = (uint16_t)queue[0x100 / 4];
        unsigned genres = *(volatile uint16_t *)0x0300c082;
        if (index >= genres || index >= MAX_SORTED_ROWS ||
            !read_group(map + 0x20, index, &genre) || !genre.tracks ||
            first < genre.representative || first + count > genre.representative + genre.tracks)
            return 0;
        if (genre.children > 1 && first == genre.representative && count == genre.tracks)
            scope = 1;
    }

    if (choose_next(queue, 1) || queue[0x44 / 4])
        return 0;
    unsigned next = queue[0] >> 16;
    if (!next || next > count)
        return 0;

    uint32_t directory = queue[0x34 / 4];
    uint32_t position = queue[0x38 / 4];
    if (!offset) {
        const volatile struct directory_entry *entries = kind == M3U ? playlist : favorites;
        unsigned available = kind == M3U ? active_playlist_count : active_favorite_count;
        if (next > available || entries[next - 1].directory != directory ||
            entries[next - 1].position != position)
            return 0;
    } else {
        uint32_t record[5], address;
        /* The stock selector does not propagate every library read error. */
        if (!read_track_record(base, map, first + next - 1, record, &address) ||
            record[0] != directory || record[1] != position || record[3] || record[4])
            return 0;
    }
    uint32_t entry = position - 1;
    if ((directory == deleted_directory && entry == deleted_entry) ||
        !probe(song, directory, entry) || song->device != device) {
        song->device = 0;
        return 0;
    }
    song->kind = kind;
    song->row = next;
    song->depth = depth;
    song->scope = scope;
    return 1;
}

static SUCCESSOR_CODE int same_file(const struct successor *song)
{
    struct successor current;
    if (!probe(&current, song->directory, song->entry) ||
        current.device != song->device || current.cluster != song->cluster ||
        current.size != song->size)
        return 0;
    for (unsigned i = 0; i < sizeof(song->name); i++) {
        if (song->name[i] != current.name[i])
            return 0;
    }
    return 1;
}

/* Return the one-based row in the rebuilt sorted map, or zero when the
 * volume, record or file cannot be confirmed. */
SUCCESSOR_CODE unsigned delete_track_find_successor(const struct successor *song)
{
    uint32_t base = *(volatile uint32_t *)0x0300c030;
    unsigned count = *(volatile uint16_t *)0x0300c07c;
    unsigned device = song->device == 0x40000 ? 4 : 2;
    unsigned offset = map_offset(song->kind);
    if ((song->device != 0x30002 && song->device != 0x40000) ||
        library_device != device || mounted_device != song->device ||
        base > UINT16_MAX * 6u || count > MAX_SORTED_ROWS || !offset)
        return 0;

    unsigned found = 0;
    uint16_t rows[256];
    for (unsigned first = 0; first < count; first += 256) {
        watchdog();
        unsigned length = count - first;
        if (length > 256)
            length = 256;
        if (read_library(0x30001, ((base + offset) << 9) + first * 2,
                         length * sizeof(rows[0]), rows))
            return 0;
        for (unsigned i = 0; i < length; i++) {
            uint32_t record[5];
            if (rows[i] >= MAX_RECORDS ||
                read_library(0x30001, (base << 9) + (rows[i] << 11),
                             sizeof(record), record))
                return 0;
            if (record[0] != song->directory || record[1] != song->entry + 1 ||
                record[3] || record[4])
                continue;
            if (found)
                return 0;
            found = first + i + 1;
        }
    }
    return found && same_file(song) ? found : 0;
}

/* The representative is the first track in the group in this sorted map.
 * First-child and count refer to the next level, or to tracks at the leaf. */
static SUCCESSOR_CODE unsigned group_browser_descriptor(const struct successor *song,
        unsigned sorted_row, volatile uint16_t *browser)
{
    unsigned kind = song->kind;
    unsigned scope = song->scope;
    unsigned depth = group_depth(kind);
    if (!scope || scope > depth)
        return 0;
    unsigned first = 0;
    unsigned count = *(volatile uint16_t *)(0x0300c07eu + 2 * (kind - 3));
    uint32_t sector = *(volatile uint32_t *)0x0300c030 + map_offset(kind) + 0x20;
    unsigned track = sorted_row - 1;
    struct library_group group;
    for (unsigned level = 0; level < scope; level++, sector += 0x80) {
        if (!count || first + count > MAX_SORTED_ROWS)
            return 0;
        unsigned low = first, high = first + count;
        while (low < high) {
            unsigned mid = low + (high - low) / 2;
            struct library_group sample;
            if (!read_group(sector, mid, &sample))
                return 0;
            if (sample.representative <= track)
                low = mid + 1;
            else
                high = mid;
        }
        if (low == first)
            return 0;
        unsigned index = low - 1;
        if (!read_group(sector, index, &group) || group.representative > track)
            return 0;
        browser[3 + level] = first;
        /* Entering an artist inside a genre consumes its synthetic row.
         * Nested album lists retain the Play All offset. */
        browser[8 + level] = index - first + (level != 0 && !(kind == 5 && level == 1));
        first = group.first_child;
        count = group.children;
    }
    unsigned begin = first;
    unsigned end = first + count;
    if (scope < depth) {
        begin = group.representative;
        end = begin + group.tracks;
    }
    if (!count || end > MAX_SORTED_ROWS || track < begin || track >= end)
        return 0;
    request.track_base = begin;
    request.track_count = end - begin;
    request.list_base = first;
    browser[0] = 1;
    browser[7] = scope;
    browser[3 + scope] = first;
    browser[8 + scope] = scope < depth ? 0 : track - begin;
    return track - begin + 1;
}

/* Refresh clears the stock M3U cache. Preserve its order and the selected
 * occurrence, removing every reference to the deleted directory entry. */
static SUCCESSOR_CODE unsigned save_playlist(struct successor *song,
                                             const struct delete_track_target *target)
{
    unsigned selected = 0, kept = 0;
    unsigned count = active_playlist_count;
    if (count > MAX_PLAYLIST_ROWS)
        return 0;
    for (unsigned i = 0; i < count; i++) {
        struct directory_entry entry = playlist[i];
        if (entry.directory == target->directory && entry.position == target->entry + 1)
            continue;
        if (entry.directory >= 0x0ffffff7 || !entry.position)
            return 0;
        saved_playlist[kept++] = entry;
        if (i + 1 == song->row)
            selected = kept;
    }
    song->row = selected;
    return selected ? kept : 0;
}

static SUCCESSOR_CODE unsigned restore_playlist(const struct successor *song)
{
    unsigned count = request.playlist_count;
    unsigned row = song->row;
    if (!row || row > count || count > MAX_PLAYLIST_ROWS ||
        saved_playlist[row - 1].directory != song->directory ||
        saved_playlist[row - 1].position != song->entry + 1 || !same_file(song))
        return 0;
    for (unsigned i = 0; i < count; i++)
        playlist[i] = saved_playlist[i];
    active_playlist_count = count;
    return row;
}

static SUCCESSOR_CODE unsigned find_favorite(const struct successor *song)
{
    load_favorites();
    unsigned count = active_favorite_count;
    if (count > MAX_PLAYLIST_ROWS)
        return 0;
    unsigned row = 0;
    for (unsigned i = 0; i < count; i++) {
        if (favorites[i].directory == song->directory && favorites[i].position == song->entry + 1) {
            if (row)
                return 0;
            row = i + 1;
        }
    }
    return row && same_file(song) ? row : 0;
}

/* Capture against the old library before opening the refresh window. */
SUCCESSOR_CODE void delete_track_prepare_successor(const struct delete_track_target *target)
{
    request.cookie = 0;
    struct successor song;
    if (delete_track_choose_successor(&song, target->device, target->directory, target->entry)) {
        if (song.kind == M3U) {
            unsigned count = save_playlist(&song, target);
            if (!count)
                return;
            request.playlist_count = count;
        }
        request.song = song;
        request.mode = play_mode;
        request.order = play_order;
        request.cookie = REFRESH_PENDING;
    }
}

__attribute__((section(".successor_refresh")))
void delete_track_refresh_finished(unsigned window, const uint16_t *parameters)
{
    uint32_t cookie = request.cookie;
    request.cookie = 0;
    if (cookie == REFRESH_PENDING && window == 1 && window_tag == DELETE_TRACK_REFRESH_TAG) {
        struct successor song = request.song;
        unsigned volume = song.device == 0x40000 ? 4 : 2;
        volatile uint8_t *selected_volume = (volatile uint8_t *)0x0300c05a;
        unsigned row = 0;
        volatile uint16_t browser[16];
        for (unsigned i = 0; i < 16; i++)
            browser[i] = 0;
        lock_filesystem();
        if (library_device == volume &&
            (mounted_device == song.device || mount_device(volume) == 0)) {
            *selected_volume = volume;
            if (song.kind == M3U)
                row = restore_playlist(&song);
            else if (song.kind == FAVORITES)
                row = find_favorite(&song);
            else
                row = delete_track_find_successor(&song);
            request.track_base = 0;
            request.track_count = 0;
            request.list_base = 0;
            if (row && song.scope)
                row = group_browser_descriptor(&song, row, browser);
        }
        unlock_filesystem();
        if (row) {
            browser[1] = 0x82;
            browser[15] = DELETE_TRACK_PLAY_TAG;
            *(volatile uint16_t *)0x0300c36a = song.kind;
            request.row = row;
            request.generation = delete_track_mount.current_generation;
            request.cookie = PLAY_PENDING;
            open_window(2, (const uint16_t *)browser);
            return;
        }
    }
    open_window(window, parameters);
}

/* Entered by the stock refresh tail only after a deletion requested a rescan.
 * The decoder overlay is already resident and the stock scanner ends below it. */
__attribute__((naked, section(".successor_refresh")))
void delete_track_refresh_return(void)
{
    __asm__ volatile(
        "bl delete_track_refresh_finished\n"
        "ldr.w pc, =0x0307a8ad\n"
        ".ltorg\n"
    );
}

__attribute__((section(".successor_browser")))
uint32_t delete_track_browser_key(void)
{
    uint32_t key = get_key();
    volatile uint16_t *title = (volatile uint16_t *)0x03004b12;
    volatile uint16_t *list = (volatile uint16_t *)0x0300e968;
    if (*title != 0x82)
        return key;
    if (window_tag == DELETE_TRACK_PLAY_TAG) {
        unsigned kind = request.song.kind;
        unsigned scope = request.song.scope;
        int flat = scope < group_depth(kind);
        *title = kind == M3U ? 0x5e : kind == FAVORITES ? 0x61 :
                kind >= 3 && kind <= 5 ? 0x58 + kind : 0x5a;
        unsigned row = request.row;
        uint32_t cookie = request.cookie;
        request.cookie = 0;
        if (cookie != PLAY_PENDING || row == 0 || scope > 3 || list[5] != scope ||
            list[1 + scope] != request.list_base ||
            (flat ? list[0] < 2 || row > request.track_count : row > list[0]) ||
            *(volatile uint16_t *)0x0300c36a != kind ||
            request.generation != delete_track_mount.current_generation ||
            request.song.device != mounted_device) {
            window_tag = 0;
            return 0;
        }
        list[6 + scope] = flat ? 0 : row - 1;
        list[0xb] = list[6 + scope];
        request.cookie = LAUNCH_PENDING;
        return 0x40000040;
    }
    *title = 0x5a;
    if (!list[0])
        return key;
    list[0xb] = *(volatile uint32_t *)0xe000e018 % list[0];
    return 0x40000040;
}

__attribute__((section(".successor_browser")))
void delete_track_launch_successor(unsigned window, const uint16_t *parameters)
{
    uint32_t cookie = request.cookie;
    request.cookie = 0;
    window_tag = 0;
    if (cookie != LAUNCH_PENDING) {
        open_window(window, parameters);
        return;
    }
    volatile uint16_t opened[16];
    for (unsigned i = 0; i < 16; i++)
        opened[i] = 0;
    if (window == 3 &&
        request.generation == delete_track_mount.current_generation &&
        request.song.device == mounted_device) {
        volatile uint16_t *queue = (volatile uint16_t *)0x0300c744;
        volatile uint32_t *fields = (volatile uint32_t *)0x0300c744;
        struct successor song = request.song;
        unsigned row = request.row;
        int flat = song.scope < group_depth(song.kind);
        int matches = queue[0xf2 / 2] == song.kind && queue[0xf4 / 2] == song.depth &&
                      row && row <= queue[0];
        if (matches && flat) {
            uint32_t base = fields[0xf8 / 4], map = fields[0xfc / 4];
            uint32_t record[5], address;
            matches = queue[0] == request.track_count &&
                      queue[0x110 / 2 + song.depth] == request.track_base &&
                      base <= UINT16_MAX * 6u && map == base + map_offset(song.kind) &&
                      read_track_record(base, map, request.track_base + row - 1, record, &address) &&
                      record[0] == song.directory && record[1] == song.entry + 1 &&
                      !record[3] && !record[4] &&
                      !read_library(0x30001, address + 0x400, 0x100, (void *)0x0300c37a);
            if (matches) {
                queue[1] = row;
                queue[3] = row;
                queue[0x100 / 2 + song.depth] = row - 1;
                fields[0x34 / 4] = song.directory;
                fields[0x38 / 4] = song.entry + 1;
                fields[0x44 / 4] = 0;
                fields[0x48 / 4] = 0;
                fields[0x4c / 4] = 0;
            }
        }
        if (matches && queue[1] == row && fields[0x34 / 4] == song.directory &&
            fields[0x38 / 4] == song.entry + 1) {
            play_mode = request.mode;
            play_order = request.order;
            for (unsigned i = 0; i < 15; i++)
                opened[i] = parameters[i];
            opened[0] = row;
            open_window(window, (const uint16_t *)opened);
            return;
        }
    }
    open_window(1, (const uint16_t *)opened);
}
