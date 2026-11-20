#ifndef DELETE_TRACK_SUCCESSOR_H
#define DELETE_TRACK_SUCCESSOR_H

#define DELETE_TRACK_REFRESH_TAG 0x6a00
#define DELETE_TRACK_PLAY_TAG 0x6a1e

#ifndef __ASSEMBLER__
struct delete_track_target;
void delete_track_prepare_successor(const struct delete_track_target *target);

#endif
#endif
