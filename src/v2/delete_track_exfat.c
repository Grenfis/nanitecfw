#include "delete_track_target.h"

#define STOCK(address, result, ...) ((result (*)(__VA_ARGS__))((address) | 1u))
#define read_sectors STOCK(0x0306b7ae, int, uint32_t, uint32_t, uint32_t, void *)
#define write_sectors STOCK(0x0306b7f6, int, uint32_t, uint32_t, uint32_t, const void *)
#define read_entry STOCK(0x0305b302, int, uint8_t *, uint8_t *, uint32_t)
#define unlink_entry STOCK(0x0305f41e, int, uint8_t *, uint32_t)
#define watchdog STOCK(0x03065898, void, void)

enum { SECTOR_SIZE = 512, SECTOR_SHIFT = 9, BITMAP_ENTRY = 0x81 };
#define END_OF_CHAIN UINT32_MAX

#ifdef DELETE_TRACK_ENABLE_WRITES
/* The decoder has loaded this overlay before a playback handle can be deleted. */
#define EXFAT_HELPER __attribute__((section(".successor"), noinline))
#else
#define EXFAT_HELPER
#endif

struct sector_cache {
    uint32_t lba;
    uint8_t bytes[SECTOR_SIZE];
};

struct volume {
    uint32_t device, start, fat, heap, clusters, root, bitmap;
    unsigned shift;
    struct sector_cache fat_cache, bitmap_cache;
};

static uint32_t u32(const volatile uint8_t *data)
{
    return data[0] | (uint32_t)data[1] << 8 | (uint32_t)data[2] << 16 | (uint32_t)data[3] << 24;
}

static int valid_cluster(const struct volume *volume, uint32_t cluster)
{
    return cluster - 2 < volume->clusters;
}

static uint32_t sector_of(const struct volume *volume, uint32_t cluster)
{
    return volume->heap + ((cluster - 2) << volume->shift);
}

static EXFAT_HELPER int load_sector(const struct volume *volume, struct sector_cache *cache, uint32_t lba)
{
    if (cache->lba != lba) {
        if (read_sectors(volume->device, lba, 1, cache->bytes))
            return 0;
        cache->lba = lba;
    }
    return 1;
}

static EXFAT_HELPER uint32_t next_cluster(struct volume *volume, uint32_t cluster)
{
    if (!valid_cluster(volume, cluster) ||
        !load_sector(volume, &volume->fat_cache, volume->fat + (cluster >> 7)))
        return 0;
    return u32(volume->fat_cache.bytes + ((cluster & 127) << 2));
}

static int find_bitmap(struct volume *volume)
{
    uint32_t cluster = volume->root;
    uint8_t *bytes = volume->bitmap_cache.bytes;
    for (uint32_t count = 0; count < volume->clusters && valid_cluster(volume, cluster); count++) {
        uint32_t first = sector_of(volume, cluster);
        for (unsigned i = 0; i < (1u << volume->shift); i++) {
            if (!load_sector(volume, &volume->bitmap_cache, first + i))
                return 0;
            for (unsigned offset = 0; offset < SECTOR_SIZE; offset += 32) {
                const uint8_t *entry = bytes + offset;
                if (!entry[0])
                    return 0;
                if (entry[0] == BITMAP_ENTRY && entry[1] == 0) {
                    volume->bitmap = u32(entry + 20);
                    return valid_cluster(volume, volume->bitmap) &&
                           u32(entry + 28) == 0 && u32(entry + 24) == (volume->clusters + 7) / 8;
                }
            }
        }
        watchdog();
        cluster = next_cluster(volume, cluster);
    }
    return 0;
}

static int load_volume(struct volume *volume, uint32_t device)
{
    volume->device = device;
    /* Partition start recorded by the stock V2.0.0 exFAT mount. */
    volume->start = u32((const volatile uint8_t *)0x03077d2f);
    volume->fat_cache.lba = UINT32_MAX;
    volume->bitmap_cache.lba = UINT32_MAX;
    if (!load_sector(volume, &volume->bitmap_cache, volume->start))
        return 0;
    const uint8_t *boot = volume->bitmap_cache.bytes;
    if (u32(boot + 3) != 0x41465845 || u32(boot + 7) != 0x20202054 ||
        boot[0x6c] != SECTOR_SHIFT || boot[0x6d] > 16 || boot[0x6e] != 1 ||
        (boot[0x6a] & 7) || u32(boot + 0x4c))
        return 0;
    uint32_t length = u32(boot + 0x48);
    uint32_t fat_offset = u32(boot + 0x50), fat_length = u32(boot + 0x54);
    uint32_t heap_offset = u32(boot + 0x58);
    volume->clusters = u32(boot + 0x5c);
    volume->root = u32(boot + 0x60);
    volume->shift = boot[0x6d];
    if (length > UINT32_MAX - volume->start || heap_offset >= length || fat_offset < 24 ||
        fat_offset >= heap_offset || fat_length > heap_offset - fat_offset ||
        volume->clusters == 0 || volume->clusters > (length - heap_offset) >> volume->shift ||
        volume->clusters > UINT32_MAX - 127 || (volume->clusters + 2 + 127) / 128 > fat_length ||
        !valid_cluster(volume, volume->root))
        return 0;
    volume->fat = volume->start + fat_offset;
    volume->heap = volume->start + heap_offset;
    return find_bitmap(volume);
}

static int bitmap_walk(struct volume *volume, uint32_t first, uint32_t count, int contiguous, int release)
{
    uint32_t cluster = first, bitmap_cluster = volume->bitmap, bitmap_index = 0;
    uint32_t dirty_sector = UINT32_MAX;
    for (uint32_t i = 0; i < count; i++) {
        if (!valid_cluster(volume, cluster) || cluster == volume->root)
            return 0;
        uint32_t bit = cluster - 2;
        uint32_t sector_index = bit >> 12;
        uint32_t required_cluster = sector_index >> volume->shift;
        if (required_cluster < bitmap_index) {
            bitmap_index = 0;
            bitmap_cluster = volume->bitmap;
        }
        while (bitmap_index < required_cluster) {
            bitmap_cluster = next_cluster(volume, bitmap_cluster);
            if (!valid_cluster(volume, bitmap_cluster))
                return 0;
            bitmap_index++;
        }
        if (cluster == bitmap_cluster)
            return 0;
        uint32_t lba = sector_of(volume, bitmap_cluster) + (sector_index & ((1u << volume->shift) - 1));
        if (dirty_sector != UINT32_MAX && lba != dirty_sector) {
            if (write_sectors(volume->device, dirty_sector, 1, volume->bitmap_cache.bytes))
                return 0;
            dirty_sector = UINT32_MAX;
        }
        if (!load_sector(volume, &volume->bitmap_cache, lba))
            return 0;
        uint8_t mask = 1u << (bit & 7);
        uint8_t *byte = volume->bitmap_cache.bytes + ((bit >> 3) & (SECTOR_SIZE - 1));
        if (!(*byte & mask))
            return 0;
        if (release) {
            *byte &= ~mask;
            dirty_sector = lba;
        }
        cluster = contiguous ? cluster + 1 : next_cluster(volume, cluster);
        if ((i & 127) == 0)
            watchdog();
    }
    if (!contiguous && cluster != END_OF_CHAIN)
        return 0;
    return dirty_sector == UINT32_MAX ||
           !write_sectors(volume->device, dirty_sector, 1, volume->bitmap_cache.bytes);
}

static EXFAT_HELPER int set_dirty(struct volume *volume, int dirty)
{
    struct sector_cache *cache = &volume->bitmap_cache;
    if (!load_sector(volume, cache, volume->start))
        return 0;
    if (dirty)
        cache->bytes[0x6a] |= 2;
    else {
        cache->bytes[0x6a] &= ~2;
        cache->bytes[0x70] = 0xff; /* PercentInUse becomes unknown. */
    }
    return !write_sectors(volume->device, volume->start, 1, cache->bytes);
}

static uint16_t entry_checksum(uint16_t sum, const uint8_t *entry, int primary)
{
    for (unsigned i = 0; i < 32; i++) {
        if (!primary || (i != 2 && i != 3))
            sum = (uint16_t)((sum >> 1 | sum << 15) + entry[i]);
    }
    return sum;
}

static int verify_entry_set(struct delete_track_target *target, const uint8_t *primary,
                            const uint8_t *stream)
{
    unsigned count = primary[1], names = (stream[3] + 14) / 15;
    if (!names || count != names + 1 || target->entry > UINT32_MAX - count)
        return 0;
    uint16_t sum = entry_checksum(entry_checksum(0, primary, 1), stream, 0);
    for (unsigned i = 2; i <= count; i++) {
        uint8_t name[32];
        if (read_entry(name, target->exfat_directory, target->entry + i) || name[0] != 0xc1)
            return 0;
        sum = entry_checksum(sum, name, 0);
    }
    return sum == (primary[2] | primary[3] << 8);
}

enum delete_track_result delete_track_remove_exfat(struct delete_track_target *target)
{
    uint8_t primary[32], stream[32];
    if (target->entry == UINT32_MAX || read_entry(primary, target->exfat_directory, target->entry) ||
        read_entry(stream, target->exfat_directory, target->entry + 1))
        return DELETE_TRACK_IO_ERROR;
    if (primary[0] != 0x85 || primary[1] < 2 || primary[1] > 17 || (primary[4] & 0x19) ||
        stream[0] != 0xc0 || !(stream[1] & 1) || u32(stream + 20) != target->first_cluster ||
        u32(stream + 8) != target->size || u32(stream + 12) || u32(stream + 28) ||
        !verify_entry_set(target, primary, stream))
        return DELETE_TRACK_TARGET_CHANGED;
    uint32_t size = u32(stream + 24);
    struct volume volume;
    if (size < target->size || !size || !load_volume(&volume, target->device))
        return DELETE_TRACK_UNSUPPORTED_FILESYSTEM;
    uint32_t count = ((size - 1) >> (volume.shift + SECTOR_SHIFT)) + 1;
    int contiguous = (stream[1] & 2) != 0;
    if (count > volume.clusters || !bitmap_walk(&volume, target->first_cluster, count, contiguous, 0))
        return DELETE_TRACK_TARGET_CHANGED;

    /* Unlink first. An interrupted bitmap update can leave allocated space,
     * but cannot expose a live directory entry whose sectors can be reused. */
    if (!set_dirty(&volume, 1) || unlink_entry(target->exfat_directory, target->entry) ||
        !bitmap_walk(&volume, target->first_cluster, count, contiguous, 1) ||
        !set_dirty(&volume, 0))
        return DELETE_TRACK_IO_ERROR;
    return DELETE_TRACK_REMOVED;
}
