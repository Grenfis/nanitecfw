#include "delete_track_target.h"

/* Stock V2.0.0, verified against its playback open path and handle table. */
static const volatile uint32_t *const playback_handle = (const volatile uint32_t *)0x030045ac;
static const volatile uint32_t *const playback_has_range = (const volatile uint32_t *)0x0300c788;
static const volatile uint8_t *const storage = (const volatile uint8_t *)0x03077d00;
static const volatile uint8_t *const files = (const volatile uint8_t *)0x03078844;

static uint32_t read_file_field(const volatile uint8_t *field)
{
    return (uint32_t)field[0] | (uint32_t)field[1] << 8 |
           (uint32_t)field[2] << 16 | (uint32_t)field[3] << 24;
}

int delete_track_capture(struct delete_track_target *target, uint32_t mount_generation,
                         uint32_t opened_device)
{
    target->mount_generation = 0;
    uint32_t handle = *playback_handle;
    if (handle >= 8 || *playback_has_range != 0 || mount_generation == 0)
        return 0;

    const volatile uint8_t *file = files + handle * 0x27e;
    uint32_t device = read_file_field(storage + 8);
    uint32_t first_cluster = read_file_field(file + 0x14);
    uint8_t file_system = storage[0];
    if (file[0] != 1 || (device != 0x30002 && device != 0x40000) ||
        device != opened_device || file_system > 3 ||
        first_cluster < 2 || first_cluster >= 0x0ffffff7)
        return 0;

    target->mount_generation = mount_generation;
    target->device = device;
    target->directory = read_file_field(file + 0x0c);
    target->entry = read_file_field(file + 0x1c);
    target->first_cluster = first_cluster;
    target->size = read_file_field(file + 0x10);
    target->file_system = file_system;
    target->handle = handle;
    for (unsigned i = 0; i < sizeof(target->name); i++)
        target->name[i] = file[1 + i];
    if (file_system == 3) {
        for (unsigned i = 0; i < sizeof(target->exfat_directory); i++)
            target->exfat_directory[i] = file[0x22a + i];
    }
    return 1;
}

int delete_track_matches(const struct delete_track_target *target, uint32_t mount_generation,
                         uint32_t opened_device)
{
    struct delete_track_target current;
    if (target->mount_generation != mount_generation ||
        !delete_track_capture(&current, mount_generation, opened_device))
        return 0;

    if (current.device != target->device || current.file_system != target->file_system ||
        current.handle != target->handle || current.directory != target->directory ||
        current.entry != target->entry || current.first_cluster != target->first_cluster ||
        current.size != target->size)
        return 0;

    for (unsigned i = 0; i < sizeof(current.name); i++) {
        if (current.name[i] != target->name[i])
            return 0;
    }
    if (current.file_system == 3) {
        for (unsigned i = 0; i < sizeof(current.exfat_directory); i++) {
            if (current.exfat_directory[i] != target->exfat_directory[i])
                return 0;
        }
    }
    return 1;
}

int delete_track_finish(struct delete_track_target *target, uint32_t mount_generation,
                        uint32_t opened_device, int accepted)
{
    int matched = accepted == 1 && delete_track_matches(target, mount_generation, opened_device);
    target->mount_generation = 0;
    return matched;
}

#define STOCK(address, result, ...) ((result (*)(__VA_ARGS__))((address) | 1u))
#define lock_filesystem STOCK(0x0306a4a6, void, void)
#define unlock_filesystem STOCK(0x0306a4fe, void, void)
#define stop_playback STOCK(0x0308331c, int, int)
#define read_fat_entry STOCK(0x0305b44a, int, uint8_t *, uint32_t, uint32_t)
#define open_fat_handle STOCK(0x0305fc0e, int, uint32_t, uint8_t *)
#define release_fat_chain STOCK(0x0305e75c, void, uint32_t)
#define remove_fat_entry STOCK(0x0305f392, int, uint32_t, uint8_t *)
#define read_sectors STOCK(0x0306b7ae, int, uint32_t, uint32_t, uint32_t, void *)
#define write_sectors STOCK(0x0306b7f6, int, uint32_t, uint32_t, uint32_t, const void *)

static int fat_walk(const struct delete_track_target *target, int release)
{
    const volatile uint8_t *layout = storage + 0xc;
    uint32_t sectors_per_cluster = read_file_field(layout + 4);
    uint32_t clusters = read_file_field(layout + 0x1b);
    uint32_t sectors_per_fat = read_file_field(layout + 0x13);
    uint32_t start = read_file_field(layout + 0x23), reserved = layout[8] | layout[9] << 8;
    unsigned copies = layout[0xa];
    const uint8_t format = target->file_system;
    const uint32_t mask = format == 0 ? 0xfff : format == 1 ? 0xffff : 0x0fffffff;
    uint8_t data[1024];
    if (layout[0] != 0 || layout[1] != 2 || !sectors_per_cluster || sectors_per_cluster > 128 ||
        (sectors_per_cluster & (sectors_per_cluster - 1)) || clusters >= mask - 9 ||
        !sectors_per_fat || !copies || copies > 2 || start > UINT32_MAX - reserved ||
        sectors_per_fat > (UINT32_MAX - start - reserved) / copies || !target->size ||
        *(volatile uint8_t *)0x03077af0)
        return 0;
    if (format == 2) {
        if (read_sectors(target->device, start, 1, data))
            return 0;
        if (data[40] & 0x80) {
            if (data[40] & 0x0f)
                return 0; /* Stock reads the first table on this layout. */
            copies = 1;
        }
    }
    uint32_t first_fat = start + reserved;
    uint32_t size = sectors_per_cluster * 512;
    uint32_t count = (target->size - 1) / size + 1;
    if (count > clusters)
        return 0;
    uint32_t cluster = target->first_cluster;
    for (uint32_t index = 0; index < count; index++) {
        if (cluster - 2 >= clusters)
            return 0;
        uint32_t offset = format == 0 ? cluster + cluster / 2 : cluster << format;
        uint32_t sector = offset >> 9;
        unsigned position = offset & 511;
        unsigned width = position == 511 ? 2 : 1;
        uint32_t next = 0;
        if (sector + width > sectors_per_fat)
            return 0;
        for (unsigned copy = 0; copy < copies; copy++) {
            uint32_t lba = first_fat + copy * sectors_per_fat + sector;
            if (read_sectors(target->device, lba, width, data))
                return 0;
            unsigned shift = format == 0 ? (cluster & 1) * 4 : 0;
            uint32_t value = format == 2 ? read_file_field(data + position) :
                                         data[position] | (uint32_t)data[position + 1] << 8;
            uint32_t entry = (value >> shift) & mask;
            if (copy && entry != next)
                return 0;
            next = entry;
            if (release) {
                value &= ~(mask << shift);
                data[position] = value;
                data[position + 1] = value >> 8;
                if (format == 2) {
                    data[position + 2] = value >> 16;
                    data[position + 3] = value >> 24;
                }
                if (write_sectors(target->device, lba, width, data))
                    return 0;
            }
        }
        cluster = next;
    }
    if (release)
        *(volatile uint32_t *)0x03077af4 = 0;
    return cluster >= mask - 7;
}

static enum delete_track_result remove_fat(struct delete_track_target *target)
{
    uint8_t entry[32];
    if (read_fat_entry(entry, target->directory, target->entry))
        return DELETE_TRACK_IO_ERROR;
    if (entry[0] == 0 || entry[0] == 0xe5 || (entry[0x0b] & 0x19) != 0)
        return DELETE_TRACK_TARGET_CHANGED;
    for (unsigned i = 0; i < sizeof(target->name); i++) {
        if (entry[i] != target->name[i])
            return DELETE_TRACK_TARGET_CHANGED;
    }
    uint32_t cluster = (uint32_t)entry[0x14] << 16 | (uint32_t)entry[0x15] << 24 |
                       (uint32_t)entry[0x1b] << 8 | entry[0x1a];
    if (cluster != target->first_cluster || read_file_field(entry + 0x1c) != target->size ||
        open_fat_handle(target->directory, target->name) < 8)
        return DELETE_TRACK_TARGET_CHANGED;
    int raw_fat = target->file_system == 0 || storage[0x16] > 1;
    if (raw_fat && !fat_walk(target, 0))
        return DELETE_TRACK_TARGET_CHANGED;

    /* Keep a live directory entry from pointing to a freed chain on write error. */
    if (remove_fat_entry(target->directory, target->name))
        return DELETE_TRACK_IO_ERROR;
    if (raw_fat) {
        if (!fat_walk(target, 1))
            return DELETE_TRACK_IO_ERROR;
    } else {
        release_fat_chain(cluster);
    }
    return DELETE_TRACK_REMOVED;
}

static int target_open(const struct delete_track_target *target)
{
    for (unsigned handle = 0; handle < 8; handle++) {
        const volatile uint8_t *file = files + handle * 0x27e;
        if (file[0] && (read_file_field(file + 0x14) == target->first_cluster ||
                        (read_file_field(file + 0x0c) == target->directory &&
                         read_file_field(file + 0x1c) == target->entry)))
            return 1;
    }
    return 0;
}

enum delete_track_result delete_track_remove(struct delete_track_target *target,
        const volatile uint32_t *mount_generation, uint32_t generation)
{
    if (*mount_generation != generation || storage[0] != target->file_system ||
        read_file_field(storage + 8) != target->device)
        return DELETE_TRACK_TARGET_CHANGED;
    if (target_open(target))
        return DELETE_TRACK_STILL_OPEN;
    return target->file_system == 3 ? delete_track_remove_exfat(target) : remove_fat(target);
}

enum delete_track_result delete_track_stop_and_remove(struct delete_track_target *target,
        const volatile uint32_t *mount_generation, uint32_t generation)
{
    if (stop_playback(1) != 1)
        return DELETE_TRACK_STOP_FAILED;

    lock_filesystem();
    enum delete_track_result result = DELETE_TRACK_TARGET_CHANGED;
    if (*playback_handle == UINT32_MAX)
        result = delete_track_remove(target, mount_generation, generation);
    unlock_filesystem();
    return result == DELETE_TRACK_STILL_OPEN ? DELETE_TRACK_TARGET_CHANGED : result;
}

enum delete_track_result delete_track_execute(struct delete_track_target *target,
        const volatile uint32_t *mount_generation, uint32_t opened_device, int accepted)
{
    lock_filesystem();
    uint32_t generation = *mount_generation;
    int matched = delete_track_finish(target, generation, opened_device, accepted);
    unlock_filesystem();
    if (!matched)
        return DELETE_TRACK_REJECTED;
    return delete_track_stop_and_remove(target, mount_generation, generation);
}
