#include "aegis_native.h"

#include <stddef.h>
#include <stdio.h>

_Static_assert(sizeof(aegis_frame_t) == AEGIS_FRAME_SIZE, "C ABI frame size");
_Static_assert(offsetof(aegis_frame_t, quality) == 40, "C ABI frame field offset");
_Static_assert(sizeof(aegis_snapshot_t) == AEGIS_SNAPSHOT_SIZE, "C ABI snapshot size");
_Static_assert(offsetof(aegis_snapshot_t, engine_state) == 72, "C ABI snapshot field offset");

#define CHECK(condition) do { \
    if (!(condition)) { \
        fprintf(stderr, "C ABI check failed at line %d: %s\n", __LINE__, #condition); \
        return 1; \
    } \
} while (0)

int main(void)
{
    aegis_handle_t handle = NULL;
    aegis_snapshot_t snapshot = {0};
    aegis_frame_t frame = {1, 0, 1000, 0.0, 0.0, AEGIS_QUALITY_VALID, 0};
    uint32_t accepted = 0;

    CHECK(aegis_abi_version() == AEGIS_ABI_VERSION);
    CHECK(aegis_create(1, &handle) == AEGIS_OK);
    CHECK(aegis_start(handle) == AEGIS_OK);
    CHECK(aegis_submit_frames(handle, &frame, 1, AEGIS_FRAME_SIZE, &accepted) == AEGIS_OK);
    CHECK(accepted == 1);
    CHECK(aegis_request_stop(handle, AEGIS_STOP_DRAIN) == AEGIS_OK);
    CHECK(aegis_wait_stopped(handle, 5000) == AEGIS_OK);
    CHECK(aegis_read_snapshot(handle, &snapshot, AEGIS_SNAPSHOT_SIZE) == AEGIS_OK);
    CHECK(snapshot.engine_state == AEGIS_ENGINE_STOPPED);
    CHECK(snapshot.result_state == AEGIS_RESULT_WARMING_UP);
    CHECK(snapshot.last_sequence == 0 && snapshot.sample_count == 1);
    CHECK(snapshot.processed_frame_count == 1 && snapshot.discarded_frame_count == 0);
    CHECK(aegis_release(handle) == AEGIS_OK);
    return 0;
}
