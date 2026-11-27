#ifndef AEGIS_NATIVE_H
#define AEGIS_NATIVE_H

#include <stdint.h>

#if defined(_WIN32)
#if defined(AEGIS_NATIVE_BUILD)
#define AEGIS_API __declspec(dllexport)
#else
#define AEGIS_API __declspec(dllimport)
#endif
#define AEGIS_CALL __cdecl
#else
#define AEGIS_API __attribute__((visibility("default")))
#define AEGIS_CALL
#endif

#ifdef __cplusplus
extern "C" {
#endif

enum {
    AEGIS_ABI_VERSION = 1,
    AEGIS_WINDOW_SIZE = 32,
    AEGIS_MAX_CAPACITY = 4096,
    AEGIS_MAX_SUBMIT = 256,
    AEGIS_FRAME_SIZE = 48,
    AEGIS_SNAPSHOT_SIZE = 80
};

enum {
    AEGIS_OK = 0,
    AEGIS_INVALID_ARGUMENT = 1,
    AEGIS_INVALID_STATE = 2,
    AEGIS_QUEUE_FULL = 3,
    AEGIS_TIMEOUT = 4,
    AEGIS_BUSY = 5,
    AEGIS_INTERNAL_ERROR = 6
};

enum {
    AEGIS_QUALITY_VALID = 0,
    AEGIS_QUALITY_MISSING = 1,
    AEGIS_QUALITY_BAD = 2
};

enum {
    AEGIS_RESULT_EMPTY = 0,
    AEGIS_RESULT_WARMING_UP = 1,
    AEGIS_RESULT_READY = 2
};

enum {
    AEGIS_ENGINE_CREATED = 0,
    AEGIS_ENGINE_RUNNING = 1,
    AEGIS_ENGINE_DRAINING = 2,
    AEGIS_ENGINE_CANCELING = 3,
    AEGIS_ENGINE_STOPPED = 4,
    AEGIS_ENGINE_FAILED = 5
};

enum {
    AEGIS_STOP_DRAIN = 0,
    AEGIS_STOP_CANCEL = 1
};

typedef struct aegis_engine* aegis_handle_t;

typedef struct aegis_frame {
    /* Positive, increasing per new collector run within this handle. */
    uint64_t source_run_token;
    int64_t sequence;
    int64_t observed_unix_ms;
    /* Both values must be finite and in [0, 100] for VALID; ignored otherwise. */
    double actual_flow_lpm;
    double setpoint_flow_lpm;
    uint32_t quality;
    uint32_t reserved;
} aegis_frame_t;

typedef struct aegis_snapshot {
    uint64_t source_run_token;
    int64_t last_sequence; /* -1 until the first frame is processed. */
    int64_t observed_unix_ms;
    double mean_absolute_deviation_lpm; /* Meaningful only when READY. */
    uint64_t accepted_frame_count;
    uint64_t processed_frame_count; /* Includes ignored frames. */
    uint64_t discarded_frame_count;
    uint64_t ignored_frame_count; /* Replay, retired runs, and reversed timestamps. */
    uint32_t sample_count;
    uint32_t result_state;
    uint32_t engine_state;
    uint32_t reserved;
} aegis_snapshot_t;

/* One handle owns one stream and starts once. Calls may run concurrently
 * except release, for which the caller must exclude concurrent calls. */
AEGIS_API uint32_t AEGIS_CALL aegis_abi_version(void);
AEGIS_API int32_t AEGIS_CALL aegis_create(uint32_t capacity, aegis_handle_t* out_handle);
AEGIS_API int32_t AEGIS_CALL aegis_start(aegis_handle_t handle);

/* After validating every frame, copies an accepted prefix. QUEUE_FULL may
 * accompany a nonzero prefix. Retry only the remaining suffix. Struct sizes
 * must match this ABI exactly. The caller owns both buffers for the call. */
AEGIS_API int32_t AEGIS_CALL aegis_submit_frames(aegis_handle_t handle,
    const aegis_frame_t* frames, uint32_t count, uint32_t frame_size, uint32_t* out_accepted);
AEGIS_API int32_t AEGIS_CALL aegis_read_snapshot(aegis_handle_t handle,
    aegis_snapshot_t* out_snapshot, uint32_t snapshot_size);

/* Stop rejects new submissions. Repeats are safe, and CANCEL may upgrade
 * DRAIN. A zero timeout polls; UINT32_MAX is reserved. OK from wait means
 * the worker has joined. If the worker itself failed, wait joins it and
 * returns INTERNAL_ERROR. */
AEGIS_API int32_t AEGIS_CALL aegis_request_stop(aegis_handle_t handle, uint32_t mode);
AEGIS_API int32_t AEGIS_CALL aegis_wait_stopped(aegis_handle_t handle, uint32_t timeout_ms);
/* BUSY leaves ownership with the caller. Call wait after stopping a started
 * engine. Only OK consumes the handle. */
AEGIS_API int32_t AEGIS_CALL aegis_release(aegis_handle_t handle);

#ifdef __cplusplus
}
#endif

#endif
